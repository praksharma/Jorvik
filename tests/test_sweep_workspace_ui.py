"""Actual sweep output, source locks, unchanged previews and safe window closure."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QMessageBox
from jorvik.app import MainWindow
from jorvik.parametrisation import export_configuration
from test_project import FIXTURE
from test_design_integration import design
import test_shell


class SweepWorkspaceUiTest(unittest.TestCase):
    setUpClass = classmethod(test_shell.ShellTest.setUpClass.__func__)
    wait_for = test_shell.ShellTest.wait_for
    javascript = test_shell.ShellTest.javascript

    def prepare(self, directory):
        window=MainWindow(QSettings(str(directory/'settings.ini'),QSettings.Format.IniFormat))
        window.show();self.wait_for(lambda:window.view.is_ready)
        window.load_geometry(FIXTURE);self.wait_for(lambda:window.import_process is None)
        window.pick_entity(2,1,False);window.sampling.count.setValue(8);window.sampling.start()
        self.wait_for(lambda:window.sampling.process is None)
        window.open_parametrisation();dialog=window.parametrisation_dialog
        setup=dialog.setup();setup['design']=design();dialog.apply_setup(setup)
        dialog.design_panel.generate();return window, dialog

    def dispose(self, window):
        if window.parametrisation_dialog is not None:
            window.parametrisation_dialog.close()
            self.wait_for(lambda:window.parametrisation_dialog is None)
        with patch('jorvik.project_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):window.close()

    def test_whole_design_export_ignores_search_and_preserves_preview(self):
        with TemporaryDirectory() as directory:
            directory=Path(directory);window,dialog=self.prepare(directory)
            try:
                dialog.design_panel.configurations.setCurrentCell(1,0);dialog.preview()
                before=deepcopy(window.view.translation_preview)
                originals=deepcopy(window.sampling.batches)
                dialog.design_panel.search_edit.setText('configuration_000001')
                self.assertEqual(dialog.design_panel.configurations.rowCount(),1)
                panel=dialog.sweep_panel;panel.file_format.setCurrentIndex(panel.file_format.findData('npz'))
                beats=[];timer=QTimer();timer.setInterval(5);timer.timeout.connect(lambda:beats.append(1));timer.start()
                def slow(*args,**kwargs):time.sleep(.05);return export_configuration(*args,**kwargs)
                with patch('jorvik.sweep_export.export_configuration',side_effect=slow):
                    self.assertTrue(panel.start_export(directory/'run'))
                    self.assertFalse(dialog.tabs.widget(0).isEnabled())
                    self.assertFalse(dialog.tabs.widget(1).isEnabled())
                    self.assertFalse(dialog.reset_button.isEnabled())
                    self.wait_for(lambda:not panel.running)
                timer.stop();self.assertTrue(beats)
                manifest=json.loads((directory/'run/manifest.json').read_text())
                self.assertEqual(manifest['status'],'complete')
                self.assertEqual(manifest['configuration_count'],2)
                self.assertEqual(manifest['succeeded_count'],2)
                self.assertEqual(len(list((directory/'run').glob('*.npz'))),2)
                self.assertEqual(window.view.translation_preview,before)
                self.assertEqual(window.sampling.batches,originals)
                self.assertTrue(dialog.tabs.widget(0).isEnabled())
                self.assertTrue(dialog.reset_button.isEnabled())
            finally:self.dispose(window)

    def test_main_window_close_cancels_and_waits_for_current_file(self):
        with TemporaryDirectory() as directory:
            directory=Path(directory);window,dialog=self.prepare(directory)
            started=threading.Event()
            def slow(*args,**kwargs):
                started.set();time.sleep(.15);return export_configuration(*args,**kwargs)
            try:
                with patch('jorvik.sweep_export.export_configuration',side_effect=slow), patch(
                    'jorvik.project_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):
                    self.assertTrue(dialog.sweep_panel.start_export(directory/'cancelled'))
                    self.wait_for(started.is_set)
                    self.assertFalse(window.close())
                    self.assertTrue(window.isVisible())
                    self.assertTrue(window._close_requested)
                    self.wait_for(lambda:not window.isVisible())
                manifest=json.loads((directory/'cancelled/manifest.json').read_text())
                self.assertEqual(manifest['status'],'cancelled')
                self.assertEqual(manifest['succeeded_count'],1)
                self.assertEqual(manifest['pending_count'],1)
                self.assertIsNone(window.parametrisation_dialog)
                self.assertFalse(window._close_requested)
            finally:self.dispose(window)
