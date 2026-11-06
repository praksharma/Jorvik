"""One bounded background sweep export with cooperative cancellation."""
from pathlib import Path
import threading
import time

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QProgressBar, QFileDialog, QMessageBox,
)


def export_sweep(*args, **kwargs):
    # Import lazily so the widget itself remains lightweight and mockable.
    from .sweep_export import export_sweep as run
    return run(*args, **kwargs)


def resume_sweep(*args, **kwargs):
    from .sweep_export import resume_sweep as run
    return run(*args, **kwargs)


class SweepWorker(QThread):
    progressChanged = Signal(dict)

    def __init__(self, batches, base, records, destination, file_format, fail_fast, parent=None, resume_from=None):
        super().__init__(parent)
        self.batches = batches
        self.base = base
        self.records = records
        self.destination = destination
        self.file_format = file_format
        self.fail_fast = fail_fast
        self.resume_from = resume_from
        self.cancel_event = threading.Event()
        self.result = None
        self.error = None

    def run(self):
        try:
            options = dict(fail_fast=self.fail_fast, cancelled=self.cancel_event.is_set,
                           progress=self.progressChanged.emit)
            if self.resume_from is None:
                self.result = export_sweep(self.batches, self.base, self.records, self.destination,
                                           file_format=self.file_format, **options)
            else:
                self.result = resume_sweep(self.batches, self.base, self.records, self.resume_from,
                                           self.destination, **options)
        except InterruptedError as error:
            if self.resume_from is not None and self.cancel_event.is_set() and not Path(self.destination).exists():
                self.result = {'status': 'cancelled', 'no_output': True}
            else: self.error = error.with_traceback(None)
        except Exception as error:
            self.error = error.with_traceback(None)
        finally:
            # Release borrowed clouds and scalar records once the work ends.
            self.batches = self.base = self.records = None


class SweepExportPanel(QWidget):
    busyChanged = Signal(bool)
    finishedRun = Signal(dict)

    def __init__(self, window, snapshot):
        super().__init__()
        self.window = window
        self.snapshot = snapshot
        self._worker = None
        self._started = None
        self._destination = None
        self._messages = []
        self.last_result = None
        self.last_error = None
        layout = QVBoxLayout(self)
        explanation = QLabel('Export every configuration in the generated design, one at a time. '
                             'This leaves the viewport unchanged and writes to a new folder. '
                             'Cancel finishes the current configuration before stopping; completed files '
                             'and the run manifest are retained.')
        explanation.setWordWrap(True); layout.addWidget(explanation)
        form = QFormLayout()
        self.file_format = QComboBox(); self.file_format.setObjectName('sweep_format')
        self.file_format.addItem('NetCDF files (.nc)', 'nc')
        self.file_format.addItem('NumPy archives (.npz)', 'npz')
        form.addRow('Output format', self.file_format)
        self.error_policy = QComboBox(); self.error_policy.setObjectName('sweep_error_policy')
        self.error_policy.addItem('Stop at the first failed configuration', True)
        self.error_policy.addItem('Continue and record configuration failures', False)
        form.addRow('On error', self.error_policy)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        self.start_button = QPushButton('Export generated design…')
        self.start_button.setObjectName('start_sweep_export')
        self.start_button.clicked.connect(lambda: self.start_export())
        self.cancel_button = QPushButton('Cancel export'); self.cancel_button.setObjectName('cancel_sweep_export')
        self.cancel_button.setEnabled(False); self.cancel_button.clicked.connect(self.cancel)
        buttons.addWidget(self.start_button); buttons.addWidget(self.cancel_button)
        self.resume_button = QPushButton('Resume/retry saved sweep…')
        self.resume_button.setToolTip('Verify a previous run, copy completed files and retry remaining configurations in a new folder. Keeps the original output format.')
        self.resume_button.clicked.connect(lambda: self.start_resume())
        layout.addWidget(self.resume_button)
        layout.addLayout(buttons)
        self.progress = QProgressBar(); self.progress.setObjectName('sweep_progress')
        self.progress.setRange(0, 1); self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.counts = QLabel('No sweep exported yet.'); self.counts.setObjectName('sweep_counts')
        self.elapsed = QLabel('Elapsed: 0.0 s'); self.elapsed.setObjectName('sweep_elapsed')
        self.status = QLabel('Generate a current design in Combinations before exporting all configurations.')
        self.status.setObjectName('sweep_status'); self.status.setWordWrap(True)
        self.messages = QLabel(''); self.messages.setObjectName('sweep_messages'); self.messages.setWordWrap(True)
        for label in (self.counts, self.elapsed, self.status, self.messages):
            label.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(label)
        self._timer = QTimer(self); self._timer.setInterval(200); self._timer.timeout.connect(self._update_elapsed)

    @property
    def running(self):
        return self._worker is not None

    def _set_controls(self, busy):
        self.file_format.setEnabled(not busy)
        self.error_policy.setEnabled(not busy)
        self.start_button.setEnabled(not busy)
        self.resume_button.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)

    def start_resume(self, source=None, destination=None):
        if self.running: return False
        if source is None:
            source = QFileDialog.getExistingDirectory(self, 'Choose the saved sweep to resume/retry')
            if not source: return False
        return self.start_export(destination, resume_from=Path(source).resolve())

    def start_export(self, destination=None, *, resume_from=None):
        if self.running: return False
        try:
            base, records = self.snapshot()
            if not records: raise ValueError('Generate a non-empty design before exporting.')
            if destination is None:
                destination, _ = QFileDialog.getSaveFileName(
                    self, 'Export design to a new folder', 'parameter_sweep', 'New sweep folder (*)',
                    options=QFileDialog.Option.DontConfirmOverwrite)
                if not destination: return False
            destination = Path(destination).expanduser().resolve()
            if destination.exists():
                raise FileExistsError('Choose a new output folder. Existing folders and files are preserved.')
            if not destination.parent.is_dir():
                raise ValueError('The output folder parent does not exist.')
            batches = tuple(self.window.sampling.batches)
            if not batches: raise ValueError('Generate spatial samples before exporting.')
        except (ValueError, TypeError, OSError, KeyError) as error:
            QMessageBox.warning(self, 'Sweep export', str(error))
            return False
        worker = SweepWorker(batches, base, records, destination,
                             self.file_format.currentData(), self.error_policy.currentData(), self, resume_from=resume_from)
        self._worker = worker
        self._destination = destination
        self.last_result = self.last_error = None
        self._messages = []; self.messages.clear()
        self._started = time.monotonic()
        self.progress.setRange(0, len(records)); self.progress.setValue(0)
        self.counts.setText(f'0/{len(records):,} attempted · 0 written · 0 failed')
        self.status.setText(('Verifying saved run and original samples before resuming to ' if resume_from else 'Starting export to ') + str(destination))
        self._set_controls(True)
        worker.progressChanged.connect(self._progress)
        worker.finished.connect(self._finished)
        self._timer.start(); self._update_elapsed()
        # Lock the source widgets synchronously before the worker borrows them.
        self.busyChanged.emit(True)
        worker.start()
        return True

    def _update_elapsed(self):
        if self._started is not None:
            self.elapsed.setText(f'Elapsed: {time.monotonic() - self._started:.1f} s')

    def _progress(self, data):
        if not self.running: return
        if data.get('format') in ('nc', 'npz'):
            self.file_format.setCurrentIndex(self.file_format.findData(data['format']))
        total = data.get('configuration_count', self.progress.maximum())
        attempted = data.get('attempted_count', 0)
        succeeded = data.get('succeeded_count', 0); failed = data.get('failed_count', 0)
        self.progress.setRange(0, total); self.progress.setValue(attempted)
        self.counts.setText(f'{attempted:,}/{total:,} attempted · {succeeded:,} written · {failed:,} failed'
                            + (f" · {data['reused_count']:,} reused" if data.get('reused_count') else ''))
        current = data.get('current_id')
        if self._worker.cancel_event.is_set():
            self.status.setText('Cancelling after the current configuration finishes…')
        elif data.get('stage') == 'verifying':
            self.status.setText(f"Verifying saved outputs: {data['verified_count']}/{data['verification_total']} checked…")
        elif current:
            self.status.setText('Exporting ' + str(current))
        warnings = data.get('warnings', [])
        if isinstance(warnings, str): warnings = [warnings]
        errors = [data.get('error'), data.get('current_error')]
        for message in [*warnings, *errors]:
            if message and str(message) not in self._messages:
                self._messages.append(str(message))
        self._messages = self._messages[-5:]
        self.messages.setText('\n'.join(self._messages))

    def cancel(self):
        if not self.running: return
        self._worker.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.status.setText('Cancelling after the current configuration finishes…')

    def _finished(self):
        worker = self._worker
        if worker is None: return
        # The finished signal guarantees run() is over. No active GUI wait occurs.
        worker.wait()
        result, error = worker.result, worker.error
        if result is not None: self._progress(result)
        self._timer.stop(); self._update_elapsed()
        self._worker = None
        self.last_result, self.last_error = result, error
        self._set_controls(False)
        if error is not None:
            if isinstance(error, FileExistsError):
                detail = ' Existing destination retained.'
            elif self._destination.is_dir():
                detail = ' Check the output folder for an incomplete run manifest.'
            else:
                detail = ' No output folder was created.'
            self.status.setText('Export failed: ' + str(error) + '.' + detail)
            self.messages.setText(str(error))
            result = {'status': 'failed', 'error': str(error), 'destination': str(self._destination)}
        elif result is not None:
            status = result.get('status', 'failed')
            descriptions = {'complete': 'Export complete', 'complete_with_errors': 'Export finished with configuration errors',
                            'failed': 'Export stopped after a failure', 'cancelled': 'Export cancelled'}
            description = descriptions.get(status, 'Export ended with an unknown status')
            suffix = '' if status == 'complete' else ' Partial outputs and their status records are retained.'
            if result.get('no_output'):
                self.status.setText('Resume cancelled during verification. No output folder was created.')
            else:
                self.status.setText(description + ': ' + str(self._destination) + '.' + suffix)
        worker.deleteLater()
        self.busyChanged.emit(False)
        self.finishedRun.emit(result or {'status': 'failed', 'error': 'No result returned.'})

    def closeEvent(self, event):
        if self.running:
            self.cancel(); event.ignore()
        else:
            super().closeEvent(event)
