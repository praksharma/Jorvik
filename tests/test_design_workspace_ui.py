"""End-to-end selected design preview/export, project persistence and invalid drafts."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox
from jorvik.app import MainWindow
from jorvik.parametrisation import transform_batches
from jorvik.project import load_project
from test_project import FIXTURE
from test_design_integration import design
import test_shell


class DesignWorkspaceUiTest(unittest.TestCase):
    setUpClass = classmethod(test_shell.ShellTest.setUpClass.__func__)
    wait_for = test_shell.ShellTest.wait_for
    javascript = test_shell.ShellTest.javascript

    def test_selected_export_stale_preview_persistence_and_invalid_close(self):
        with TemporaryDirectory() as directory:
            directory=Path(directory)
            window=MainWindow(QSettings(str(directory/'settings.ini'),QSettings.Format.IniFormat))
            window.show()
            try:
                self.wait_for(lambda:window.view.is_ready)
                window.load_geometry(FIXTURE);self.wait_for(lambda:window.import_process is None)
                window.pick_entity(2,1,False)
                window.sampling.count.setValue(8);window.sampling.start()
                self.wait_for(lambda:window.sampling.process is None)
                originals=deepcopy(window.sampling.batches)
                window.open_parametrisation(); dialog=window.parametrisation_dialog
                setup=dialog.setup();setup['design']=design();dialog.apply_setup(setup)
                panel=dialog.design_panel
                self.assertEqual(dialog.setup(),setup)
                self.assertIsNone(window.view.translation_preview)
                with patch.object(window.sampling,'start') as sampling:
                    panel.generate_button.click();sampling.assert_not_called()
                self.assertEqual(panel.configurations.rowCount(),2)
                panel.configurations.setCurrentCell(1,0)
                self.assertIsNone(window.view.translation_preview)
                panel.preview_button.click()
                active=dialog.active_setup()
                self.assertEqual(active['design_configuration']['id'],'configuration_000002')
                self.assertEqual(window.view.translation_preview,active)
                self.assertEqual(active['translation'],[2.,0.,0.])
                output=directory/'selected.npz'
                with patch('jorvik.parametrisation_panel.QFileDialog.getSaveFileName',return_value=(str(output),'')):
                    panel.export_button.click()
                expected=transform_batches(originals,active)
                with np.load(output,allow_pickle=False) as arrays:
                    manifest=json.loads(arrays['_gs_metadata'].item())
                    self.assertEqual(manifest['configuration_count'],1)
                    self.assertEqual(manifest['parametrisation']['design'],setup['design'])
                    np.testing.assert_allclose(arrays[manifest['datasets'][0]['array']],expected[0]['coordinates'])
                old_preview=deepcopy(window.view.translation_preview)
                dialog.offsets[1].setValue(1)
                self.assertEqual(panel.configurations.rowCount(),0)
                with patch('jorvik.parametrisation_panel.QMessageBox.warning') as warning:
                    dialog.preview();warning.assert_called_once()
                self.assertEqual(window.view.translation_preview,old_preview)
                self.assertEqual(window.sampling.batches,originals)
                dialog.close();self.wait_for(lambda:window.parametrisation_dialog is None)
                saved=deepcopy(window.parametrisation_setup)
                self.assertEqual(saved['design'],setup['design'])
                window.project_path=directory/'design.jorvik';self.assertTrue(window.save_project())
                self.assertEqual(load_project(window.project_path)[0]['state']['parametrisation'],saved)
                window.open_parametrisation();dialog=window.parametrisation_dialog
                self.assertEqual(dialog.setup(),saved)
                self.assertEqual(dialog.design_panel.configurations.rowCount(),0)
                dialog.design_panel.parameters.item(0,7).setText('unfinished draft')
                dialog.close();self.wait_for(lambda:window.parametrisation_dialog is None)
                self.assertEqual(window.parametrisation_setup,saved)
                self.assertTrue(window.sampling.isEnabled())
                self.assertIn('previous valid setup retained',window.statusBar().currentMessage())
            finally:
                if window.parametrisation_dialog is not None:window.parametrisation_dialog.close()
                with patch('jorvik.project_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):window.close()
