"""Sweep export runs outside Qt's GUI thread with bounded lifecycle and cancellation."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtTest import QSignalSpy
from jorvik.parameter_design import generate_configurations
from jorvik.sweep_panel import SweepExportPanel
from test_affine_transforms import affine_setup
from test_export import batch
from test_parameter_design_ui import design
import test_shell


def result(status='complete', attempted=3, succeeded=3, failed=0, total=3):
    return {'schema_version': 1, 'status': status, 'configuration_count': total,
            'attempted_count': attempted, 'succeeded_count': succeeded,
            'failed_count': failed, 'pending_count': total-attempted,
            'elapsed_seconds': .1, 'all_outputs_complete': status == 'complete'}


class SweepPanelUiTest(unittest.TestCase):
    setUpClass = classmethod(test_shell.ShellTest.setUpClass.__func__)
    wait_for = test_shell.ShellTest.wait_for

    def panel(self):
        main_thread = threading.get_ident()
        clouds = [batch('wall', 3), batch('interior', 5)]
        class Sources:
            @property
            def batches(self):
                if threading.get_ident() != main_thread:
                    raise AssertionError('Worker accessed a UI-owned source attribute')
                return clouds
        base = affine_setup(design=design())
        records = generate_configurations(base, base['design'])
        snapshot = lambda: (base, records)
        window = SimpleNamespace(sampling=Sources(), view=SimpleNamespace(set_translation_preview=lambda value: None))
        panel = SweepExportPanel(window, snapshot)
        def cleanup():
            if panel.running:
                panel.cancel(); self.wait_for(lambda: not panel.running)
            panel.close(); panel.deleteLater(); self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.addCleanup(cleanup)
        return panel, clouds, base, records

    def test_background_progress_and_no_source_or_viewport_changes(self):
        panel, clouds, base, records = self.panel()
        originals = deepcopy(clouds); main_thread = threading.get_ident(); ticks = []
        timer = QTimer(); timer.setInterval(10); timer.timeout.connect(lambda: ticks.append(True))
        busy = QSignalSpy(panel.busyChanged); finished = QSignalSpy(panel.finishedRun)
        observed = []
        def export(batches, setup, configurations, destination, **options):
            self.assertNotEqual(threading.get_ident(), main_thread)
            self.assertIs(batches[0], clouds[0]); self.assertIs(setup, base); self.assertIs(configurations, records)
            observed.append((options['file_format'], options['fail_fast']))
            Path(destination).mkdir()
            for i in range(3):
                time.sleep(.04)
                options['progress'](dict(result(attempted=i+1,succeeded=i+1), current_id=f'configuration_{i+1:06d}'))
            return result()
        panel.file_format.setCurrentIndex(panel.file_format.findData('npz'))
        panel.error_policy.setCurrentIndex(panel.error_policy.findData(False))
        with TemporaryDirectory() as directory, patch('jorvik.sweep_panel.export_sweep', side_effect=export), patch.object(panel.window.view, 'set_translation_preview') as preview:
            timer.start()
            try:
                self.assertTrue(panel.start_export(Path(directory)/'new-sweep'))
                self.assertTrue(panel.running); self.assertFalse(panel.start_button.isEnabled())
                self.assertTrue(panel.cancel_button.isEnabled())
                self.assertFalse(panel.start_export(Path(directory)/'duplicate'))
                self.wait_for(lambda: not panel.running)
            finally: timer.stop()
            self.assertGreaterEqual(len(ticks), 3)
            self.assertEqual(observed, [('npz', False)])
            self.assertEqual(panel.progress.value(), 3)
            self.assertIn('Export complete',panel.status.text())
            self.assertIn('3/3 attempted',panel.counts.text())
            self.assertTrue(panel.start_button.isEnabled());self.assertFalse(panel.cancel_button.isEnabled())
            self.assertEqual(busy.count(),2);self.assertEqual(busy.at(0),[True]);self.assertEqual(busy.at(1),[False])
            self.assertEqual(finished.count(),1);self.assertEqual(panel.last_result['status'],'complete')
            preview.assert_not_called()
        self.assertEqual(clouds,originals)

    def test_cancel_and_close_finish_current_work_without_blocking(self):
        panel, clouds, _, _ = self.panel()
        originals = deepcopy(clouds)
        def export(batches, setup, configurations, destination, **options):
            Path(destination).mkdir()
            options['progress'](dict(result(attempted=0,succeeded=0), current_id='configuration_000001'))
            time.sleep(.12)  # One atomic configuration must finish before stopping.
            self.assertTrue(options['cancelled']())
            return result('cancelled', attempted=1, succeeded=1)
        with TemporaryDirectory() as directory, patch('jorvik.sweep_panel.export_sweep',side_effect=export):
            panel.show()
            self.assertTrue(panel.start_export(Path(directory)/'cancelled'))
            self.assertFalse(panel.close())
            self.assertTrue(panel.running)
            self.assertFalse(panel.cancel_button.isEnabled())
            self.assertIn('Cancelling',panel.status.text())
            self.wait_for(lambda:not panel.running)
            self.assertEqual(panel.last_result['pending_count'],2)
            self.assertIn('Export cancelled',panel.status.text())
            self.assertIn('Partial outputs',panel.status.text())
            self.assertTrue(panel.close())
        self.assertEqual(clouds,originals)

    def test_failure_policy_and_invalid_start_leave_controls_usable(self):
        panel, _, _, _ = self.panel()
        with TemporaryDirectory() as directory:
            directory=Path(directory)
            for existing in (directory, directory/'exists.txt'):
                if existing.suffix: existing.write_text('retain')
                with patch('jorvik.sweep_panel.QMessageBox.warning') as warning:
                    self.assertFalse(panel.start_export(existing));warning.assert_called_once()
                self.assertFalse(panel.running)
            self.assertEqual((directory/'exists.txt').read_text(),'retain')
            with patch.object(panel,'snapshot',side_effect=ValueError('Configurations are stale')), patch('jorvik.sweep_panel.QFileDialog.getSaveFileName') as chooser, patch('jorvik.sweep_panel.QMessageBox.warning') as warning:
                self.assertFalse(panel.start_export());warning.assert_called_once();chooser.assert_not_called()
            def errors(*args,**options):
                options['progress'](dict(result('running',attempted=2,succeeded=1,failed=1), current_id='configuration_000002',error='Disk full'))
                return result('complete_with_errors',attempted=3,succeeded=2,failed=1)
            with patch('jorvik.sweep_panel.export_sweep',side_effect=errors):
                self.assertTrue(panel.start_export(directory/'errors'))
                self.wait_for(lambda:not panel.running)
            self.assertIn('configuration errors',panel.status.text())
            self.assertIn('Disk full',panel.messages.text())
            self.assertEqual(panel.progress.value(),3)
            with patch('jorvik.sweep_panel.export_sweep',side_effect=OSError('Cannot create output')):
                self.assertTrue(panel.start_export(directory/'failed'))
                self.wait_for(lambda:not panel.running)
            self.assertIsInstance(panel.last_error,OSError)
            self.assertIn('Cannot create output',panel.status.text())
            self.assertTrue(panel.start_button.isEnabled())

    def test_real_resume_reuses_verified_output_and_preserves_sources(self):
        from jorvik.sweep_export import export_sweep
        panel, clouds, base, records = self.panel()
        originals = deepcopy(clouds)
        with TemporaryDirectory() as directory:
            source = Path(directory)/'partial'; stop=[]
            export_sweep(clouds, base, records, source, file_format='npz', cancelled=lambda:bool(stop),
                         progress=lambda data:stop.append(True) if data.get('succeeded_count') else None)
            before={p.name:p.read_bytes() for p in source.iterdir()}
            self.assertTrue(panel.start_resume(source, Path(directory)/'resumed'))
            self.assertFalse(panel.resume_button.isEnabled())
            self.wait_for(lambda:not panel.running)
            self.assertIsNone(panel.last_error)
            self.assertEqual(panel.last_result['status'], 'complete')
            self.assertEqual(panel.last_result['reused_count'], 1)
            self.assertEqual(panel.file_format.currentData(), 'npz')
            self.assertIn('1 reused', panel.counts.text())
            self.assertEqual({p.name:p.read_bytes() for p in source.iterdir()}, before)
            self.assertEqual(clouds, originals)

    def test_resume_preflight_cancellation_unlocks_without_creating_folder(self):
        panel, clouds, base, records = self.panel()
        started=threading.Event()
        def resume(batches, setup, configurations, source, destination, **options):
            started.set()
            while not options['cancelled'](): time.sleep(.005)
            raise InterruptedError('Resume cancelled before creating output')
        with TemporaryDirectory() as directory, patch('jorvik.sweep_panel.resume_sweep', side_effect=resume):
            dest=Path(directory)/'new'
            self.assertTrue(panel.start_resume(Path(directory)/'previous', dest))
            self.wait_for(started.is_set); panel.cancel()
            self.wait_for(lambda:not panel.running)
            self.assertEqual(panel.last_result['status'], 'cancelled')
            self.assertIsNone(panel.last_error)
            self.assertIn('No output folder', panel.status.text())
            self.assertFalse(dest.exists()); self.assertTrue(panel.resume_button.isEnabled())
