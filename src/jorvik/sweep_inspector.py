"""Responsive, read-only GUI for existing sweep output folders."""
import json
import sys
from pathlib import Path
from PySide6.QtCore import QProcess, Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QSpinBox, QProgressBar, QAbstractItemView)

PAGE_SIZE = 200


class SweepInspector(QDialog):
    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = Path(path)
        self.process = None
        self.rows = []
        self.summary = None
        self.verification = {}
        self._buffer = b''
        self._pending_close = None
        self._cancelled = False
        self._received_done = False
        self._error = None
        self._render_pending = False
        self.setWindowTitle('Inspect exported sweep')
        self.resize(1000, 720)
        self.setMinimumSize(740, 480)
        layout = QVBoxLayout(self)
        location = QLabel(str(self.path)); location.setTextFormat(Qt.TextFormat.PlainText)
        location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(location)
        self.overview = QLabel('Reading sweep metadata…'); self.overview.setWordWrap(True)
        self.overview.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.overview)
        self.details = QLabel(); self.details.setWordWrap(True); self.details.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.details)
        self.search = QLineEdit(); self.search.setPlaceholderText('Search configuration IDs, status or parameter values')
        self.search.textChanged.connect(self.filter_rows); layout.addWidget(self.search)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(['Configuration', 'Export status', 'Named values', 'Verification', 'Output file'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(False); layout.addWidget(self.table)
        pages = QHBoxLayout(); self.page = QSpinBox(); self.page.setRange(1, 1)
        self.page.valueChanged.connect(self.render_page); pages.addWidget(QLabel('Page')); pages.addWidget(self.page)
        self.page_info = QLabel(); pages.addWidget(self.page_info); pages.addStretch(); layout.addLayout(pages)
        self.progress = QProgressBar(); layout.addWidget(self.progress)
        self.status = QLabel('Metadata inspection does not load or verify point arrays.')
        self.status.setWordWrap(True); self.status.setTextFormat(Qt.TextFormat.PlainText); layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.refresh_button = QPushButton('Refresh'); self.refresh_button.clicked.connect(lambda: self.start())
        self.verify_button = QPushButton('Verify completed files'); self.verify_button.clicked.connect(lambda: self.start(verify=True))
        self.cancel_button = QPushButton('Cancel verification'); self.cancel_button.clicked.connect(self.cancel)
        close = QPushButton('Close'); close.clicked.connect(self.reject)
        for button in (self.refresh_button, self.verify_button, self.cancel_button, close): buttons.addWidget(button)
        layout.addLayout(buttons)
        self._filtered = []
        self.start()

    @property
    def running(self):
        return self.process is not None

    def start(self, *, verify=False):
        if self.running: return
        self.rows = []; self.summary = None; self.verification = {}; self._buffer = b''
        self._cancelled = False; self._received_done = False; self._error = None
        self.overview.setText('Reading sweep metadata…'); self.details.clear()
        self.filter_rows()
        self.progress.setRange(0, 0)
        self.status.setText('Verifying completed files…' if verify else 'Reading metadata; point arrays are not loaded.')
        self.refresh_button.setEnabled(False); self.verify_button.setEnabled(False); self.cancel_button.setEnabled(True)
        process = QProcess(self); self.process = process
        process.started.connect(lambda: self.send_request(process, verify))
        process.readyReadStandardOutput.connect(lambda: self.read_output(process))
        process.finished.connect(lambda code, status: self.finished(process, code))
        process.errorOccurred.connect(lambda error: self.finished(process, -1)
            if error == QProcess.ProcessError.FailedToStart else None)
        process.start(sys.executable, ['-m', 'jorvik.sweep_inspection_worker'])

    def send_request(self, process, verify):
        process.write(json.dumps({'path': str(self.path.resolve()), 'verify': verify}).encode())
        process.closeWriteChannel()

    def read_output(self, process):
        if process is not self.process: return
        self._buffer += bytes(process.readAllStandardOutput())
        while b'\n' in self._buffer:
            line, self._buffer = self._buffer.split(b'\n', 1)
            try: self.receive(json.loads(line))
            except (ValueError, KeyError, TypeError) as error:
                self._error = 'Could not read inspection response: ' + str(error)
                process.kill()

    def receive(self, event):
        kind = event['event']
        if kind == 'metadata':
            self.rows = event['rows']; self.summary = event['summary']
            summary = self.summary
            self.overview.setText(f"Run status: {summary['status']} · {summary['completed']} complete · "
                                  f"{summary['failed']} failed · {summary['pending']} pending")
            integrity = ('Integrity: SHA-256 checksums recorded.' if summary['integrity']
                         else 'Legacy run: no checksums. Verification checks structure and provenance only.')
            counts = ', '.join(f"{batch['label']}: {batch['count']:,}" for batch in summary['datasets'])
            self.details.setText(integrity + '\nPoints per configuration: ' + counts)
            self.progress.setRange(0, max(1, summary['completed'])); self.progress.setValue(0)
            self.filter_rows()
        elif kind == 'verified':
            self.verification[event['id']] = 'Verified'
            self.progress.setValue(len(self.verification))
            if not self._render_pending:
                self._render_pending = True
                QTimer.singleShot(0, self.refresh_verification_table)
        elif kind in ('error', 'verification_error'):
            self._error = event['error']
            if 'id' in event:
                self.verification[event['id']] = 'Failed: ' + self._error
                self.render_page()
        elif kind == 'done':
            self._received_done = True
            verified = event['verified']
            self.status.setText('Metadata inspected. Click Verify completed files to check contents.' if verified is None
                                else f'Verification complete: {verified} completed files checked.')
            if verified == 0: self.progress.setValue(1)
        else: raise ValueError('Unknown inspection event.')

    def refresh_verification_table(self):
        self._render_pending = False
        self.render_page()

    def filter_rows(self, *_):
        query = self.search.text().strip().casefold()
        self._filtered = [row for row in self.rows if not query or query in
                          (row['id'] + ' ' + row['status'] + ' ' + json.dumps(row['parameters'], ensure_ascii=False)).casefold()]
        self.page.blockSignals(True); self.page.setRange(1, max(1, (len(self._filtered)+PAGE_SIZE-1)//PAGE_SIZE))
        self.page.setValue(1); self.page.blockSignals(False); self.render_page()

    def render_page(self, *_):
        start = (self.page.value()-1)*PAGE_SIZE
        rows = self._filtered[start:start+PAGE_SIZE]
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            verification = self.verification.get(row['id'], 'Not checked' if row['status']=='complete' else '—')
            values = [row['id'], row['status'], ', '.join(f'{name}={value}' for name, value in row['parameters'].items()),
                      verification, row['filename']]
            for column, value in enumerate(values):
                item = QTableWidgetItem(value); item.setToolTip(value); self.table.setItem(index, column, item)
        self.table.resizeColumnsToContents()
        self.table.setColumnWidth(2, min(340, max(180, self.table.columnWidth(2))))
        self.table.setColumnWidth(3, min(300, self.table.columnWidth(3)))
        self.page_info.setText(f'{len(self._filtered)} matches / {len(self.rows)} configurations')

    def cancel(self):
        if self.process is not None:
            self._cancelled = True; self.cancel_button.setEnabled(False)
            self.process.kill()

    def finished(self, process, code):
        if process is not self.process: return
        self.read_output(process)
        stderr = bytes(process.readAllStandardError()).decode(errors='replace').strip()
        self.process = None; process.deleteLater()
        self.progress.setRange(0, max(1, self.summary['completed'] if self.summary else 1))
        self.refresh_button.setEnabled(True)
        self.verify_button.setEnabled(self.summary is not None and self.summary['completed'] > 0)
        self.cancel_button.setEnabled(False)
        if self._cancelled:
            self.status.setText('Inspection cancelled. Unchecked files remain unverified; no output files were changed.')
        elif self._error or code or not self._received_done:
            self.status.setText('Inspection failed: ' + (self._error or stderr or 'The inspection process stopped unexpectedly.'))
            if self.summary is None: self.overview.setText('Could not inspect this sweep.')
        if self._pending_close is not None:
            result, self._pending_close = self._pending_close, None
            super().done(result)

    def done(self, result):
        if self.running:
            self._pending_close = result; self.cancel()
        else: super().done(result)

    def closeEvent(self, event):
        if self.running:
            self.done(QDialog.DialogCode.Rejected); event.ignore()
        else: super().closeEvent(event)
