"""Read-only worker protocol and real GUI inspection/cancellation checks."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jorvik.sweep_export import export_sweep
from jorvik.sweep_inspection_worker import inspect
from test_sweep_export import inputs


class SweepInspectionWorkerTest(unittest.TestCase):
    def test_formats_metadata_verification_and_corruption(self):
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            for fmt in ('nc', 'npz'):
                root = Path(directory) / fmt
                export_sweep(clouds, base, configs, root, file_format=fmt)
                original = {p.name: p.read_bytes() for p in root.iterdir()}
                events = []
                self.assertTrue(inspect(root, report=events.append))
                self.assertEqual([e['event'] for e in events], ['metadata', 'done'])
                self.assertIsNone(events[-1]['verified'])
                self.assertEqual(len(events[0]['rows']), 3)
                self.assertNotIn('setup', events[0]['rows'][0])
                self.assertEqual(events[0]['summary']['completed'], 3)
                events = []
                self.assertTrue(inspect(root, verify=True, report=events.append))
                self.assertEqual([e['id'] for e in events if e['event']=='verified'], [c['id'] for c in configs])
                self.assertEqual(events[-1]['verified'], 3)
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, original)
                with (root / ('configuration_000002.'+fmt)).open('ab') as stream: stream.write(b'changed')
                events = []
                self.assertFalse(inspect(root, verify=True, report=events.append))
                self.assertEqual(events[-1]['event'], 'verification_error')
                self.assertEqual(events[-1]['id'], configs[1]['id'])
                self.assertIn('checksum', events[-1]['error'])
                self.assertEqual(len([e for e in events if e['event']=='verified']), 1)

    def test_partial_failed_pending_and_legacy_states(self):
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'run'
            from jorvik.parametrisation import export_configuration
            def write(batches, setup, path):
                if setup['design_configuration']['id'].endswith('000002'): raise OSError('Export failed')
                return export_configuration(batches, setup, path)
            with patch('jorvik.sweep_export.export_configuration', side_effect=write):
                export_sweep(clouds, base, configs, root, file_format='npz')
            manifestpath = root / 'manifest.json'
            manifest = json.loads(manifestpath.read_text()); del manifest['integrity']
            manifestpath.write_text(json.dumps(manifest))
            events = []; self.assertTrue(inspect(root, verify=True, report=events.append))
            metadata = events[0]
            self.assertEqual([r['status'] for r in metadata['rows']], ['complete', 'failed', 'pending'])
            self.assertEqual(metadata['summary']['status'], 'failed')
            self.assertIsNone(metadata['summary']['integrity'])
            self.assertEqual(events[-1]['verified'], 1)


class SweepInspectorUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_shell
        test_shell.ShellTest.setUpClass.__func__(cls)

    def wait_for(self, predicate):
        import test_shell
        test_shell.ShellTest.wait_for(self, predicate)

    def test_gui_search_verify_refresh_corruption_and_close(self):
        from jorvik.sweep_inspector import SweepInspector
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'run'
            export_sweep(clouds, base, configs, root, file_format='npz')
            original = {p.name: p.read_bytes() for p in root.iterdir()}
            dialog = SweepInspector(root); dialog.show()
            try:
                self.wait_for(lambda: not dialog.running)
                self.assertEqual(dialog.table.rowCount(), 3)
                self.assertIn('3 complete', dialog.overview.text())
                self.assertIn('Integrity: SHA-256', dialog.details.text())
                dialog.search.setText('000002')
                self.assertEqual(dialog.table.rowCount(), 1)
                dialog.verify_button.click(); self.wait_for(lambda: not dialog.running)
                self.assertEqual(len(dialog.verification), 3)  # Search does not limit verification.
                self.assertIn('3 completed files checked', dialog.status.text())
                self.assertEqual(dialog.table.item(0, 3).text(), 'Verified')
                self.assertEqual({p.name: p.read_bytes() for p in root.iterdir()}, original)
                with (root / 'configuration_000002.npz').open('ab') as stream: stream.write(b'changed')
                dialog.verify_button.click(); self.wait_for(lambda: not dialog.running)
                self.assertIn('checksum mismatch', dialog.status.text())
                self.assertTrue(dialog.verification['configuration_000002'].startswith('Failed:'))
                dialog.refresh_button.click(); self.wait_for(lambda: not dialog.running)
                self.assertEqual(dialog.verification, {})
                self.assertEqual(dialog.table.item(0, 3).text(), 'Not checked')
                dialog.verify_button.click(); dialog.cancel_button.click()
                self.wait_for(lambda: not dialog.running)
                self.assertIn('cancelled', dialog.status.text())
                dialog.start(verify=True); dialog.close()
                self.wait_for(lambda: not dialog.running)
                self.assertFalse(dialog.isVisible())
            finally:
                dialog.reject(); self.wait_for(lambda: not dialog.running)

    def test_invalid_folder_pagination_and_main_window_shutdown(self):
        from jorvik.sweep_inspector import SweepInspector
        from jorvik.app import MainWindow
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QMessageBox
        with TemporaryDirectory() as directory:
            dialog = SweepInspector(directory); dialog.show()
            self.wait_for(lambda: not dialog.running)
            self.assertIn('failed', dialog.status.text())
            self.assertFalse(dialog.verify_button.isEnabled())
            dialog.rows = [{'id': f'configuration_{i:06d}', 'status': 'pending',
                            'parameters': {'angle': i}, 'filename': ''} for i in range(1, 402)]
            dialog.filter_rows(); self.assertEqual(dialog.page.maximum(), 3)
            self.assertEqual(dialog.table.rowCount(), 200)
            dialog.page.setValue(3); self.assertEqual(dialog.table.rowCount(), 1)
            dialog.search.setText('does not exist'); self.assertEqual(dialog.table.rowCount(), 0)
            dialog.reject()
            clouds, base, configs = inputs()
            root = Path(directory) / 'run'; export_sweep(clouds, base, configs, root, file_format='npz')
            window = MainWindow(QSettings(str(Path(directory)/'settings.ini'), QSettings.Format.IniFormat))
            window.show()
            with patch('jorvik.app.QFileDialog.getExistingDirectory', return_value=str(root)):
                window.inspect_sweep_action.trigger()
            self.assertIsNotNone(window.sweep_inspector)
            inspector = window.sweep_inspector
            with patch('jorvik.project_ui.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard):
                window.close()
                self.wait_for(lambda: not inspector.running and not window.isVisible())
            self.assertIsNone(window.sweep_inspector)

    def test_inspection_preserves_workspace_and_idle_dialog_closes_with_app(self):
        from jorvik.app import MainWindow
        from PySide6.QtCore import QSettings
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'run'; export_sweep(clouds, base, configs, root, file_format='npz')
            window = MainWindow(QSettings(str(Path(directory)/'settings.ini'), QSettings.Format.IniFormat))
            window.show()
            before = window.edit_snapshot()
            try:
                with patch('jorvik.app.QFileDialog.getExistingDirectory', return_value=str(root)):
                    window.inspect_sweep_action.trigger()
                inspector = window.sweep_inspector
                self.wait_for(lambda: not inspector.running)
                inspector.verify_button.click(); self.wait_for(lambda: not inspector.running)
                self.assertEqual(window.edit_snapshot(), before)
                window.close()
                self.assertIsNone(window.sweep_inspector)
                self.assertFalse(window.isVisible())
            finally:
                window.close()
