"""Edit history restores data, references and saved-state identity across real GUI operations."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox
from jorvik.app import MainWindow
from jorvik.regions_panel import BoundaryRegionsDialog
from jorvik.project import load_project
from test_project import FIXTURE
import test_shell


class HistoryUiTest(unittest.TestCase):
    setUpClass = classmethod(test_shell.ShellTest.setUpClass.__func__)
    wait_for = test_shell.ShellTest.wait_for
    javascript = test_shell.ShellTest.javascript

    def test_dataset_history_saved_state_branch_and_document_reset(self):
        with TemporaryDirectory() as directory:
            window = MainWindow(QSettings(str(Path(directory)/'settings.ini'), QSettings.Format.IniFormat))
            window.show()
            try:
                self.wait_for(lambda: window.view.is_ready)
                window.load_geometry(FIXTURE); self.wait_for(lambda: window.import_process is None)
                self.assertFalse(window.undo_action.isEnabled())
                window.pick_entity(2,1,False)
                window.sampling.label.setText('wall');window.sampling.count.setValue(16)
                window.sampling.start()
                self.assertFalse(window.undo_action.isEnabled())
                self.wait_for(lambda: window.sampling.process is None)
                self.assertTrue(window.undo_action.isEnabled())
                coordinates=window.sampling.batches[0]['coordinates']
                window.undo_edit();self.assertEqual(window.sampling.batches, [])
                window.redo_edit();self.assertIs(window.sampling.batches[0]['coordinates'], coordinates)
                window.project_path=Path(directory)/'history.jorvik'
                self.assertTrue(window.save_project());self.assertFalse(window.has_unsaved_changes())
                with patch('jorvik.app.QInputDialog.getText', return_value=('inlet',True)):
                    window.rename_area('wall')
                self.assertTrue(window.has_unsaved_changes())
                window.undo_edit();self.assertFalse(window.has_unsaved_changes())
                window.redo_edit();self.assertEqual(window.sampling.batches[0]['label'],'inlet')
                window.duplicate_area('inlet');self.assertEqual(len(window.sampling.batches),2)
                self.assertIs(window.sampling.batches[0]['coordinates'],window.sampling.batches[1]['coordinates'])
                self.assertIsNot(window.sampling.batches[0],window.sampling.batches[1])
                window.undo_edit();self.assertEqual(len(window.sampling.batches),1)
                window.redo_edit()
                window.parametrisation_setup={'schema_version':1,'scope':'regions','regions':['inlet'],
                    'translation':[1.,0.,0.],'source_hash':window.geometry['source_hash']}
                window.show_sampled_area('inlet');window.isolate_action.setChecked(True)
                window.delete_area('inlet');self.assertIsNone(window.parametrisation_setup)
                self.assertNotIn('inlet',window.sampled_areas.areas)
                window.undo_edit();self.assertEqual(window.parametrisation_setup['regions'],['inlet'])
                self.assertEqual(window.selected_area,'inlet');self.assertTrue(window.isolate_action.isChecked())
                window.sampling.clear.click();self.assertEqual(window.sampling.batches,[])
                window.undo_edit();self.assertEqual(len(window.sampling.batches),2)
                self.assertTrue(window.redo_action.isEnabled())
                window.duplicate_area('inlet');self.assertFalse(window.redo_action.isEnabled())
                self.assertTrue(window.save_project())
                self.assertEqual(len(load_project(window.project_path)[0]['areas']),3)
                self.assertTrue(window.load_project_file(window.project_path))
                self.assertFalse(window.undo_action.isEnabled());self.assertFalse(window.redo_action.isEnabled())
                from jorvik.area_editor import AreaEditor
                old = window.sampling.batches[0]['coordinates']
                label = window.sampling.batches[0]['label']
                editor = AreaEditor(window, label); editor.count.setValue(24); editor.apply()
                self.wait_for(lambda: window.sampling.process is None)
                self.assertEqual(window.sampled_areas.areas[label][0]['count'],24)
                window.undo_action.trigger()
                self.assertIs(window.sampled_areas.areas[label][0]['coordinates'],old)
                window.redo_action.trigger()
                self.assertEqual(window.sampled_areas.areas[label][0]['count'],24)
                window.sampling.start(); window.sampling.stop()
                self.wait_for(lambda: window.sampling.process is None)
                self.assertTrue(any(status.startswith('Not generated') for _,status in window.sampled_areas.areas.values()))
                window.undo_edit()
                self.assertTrue(all(status == 'Generated' for _,status in window.sampled_areas.areas.values()))
            finally:
                with patch('jorvik.project_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):window.close()

    def test_boundary_edits_and_bounded_metadata_snapshots(self):
        with TemporaryDirectory() as directory:
            window=MainWindow(QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat))
            try:
                from jorvik.geometry import load_geometry
                window.show_geometry(load_geometry(FIXTURE))
                dialog=BoundaryRegionsDialog(window)
                dialog.label.setText('wall')
                dialog.set_members([[1,1]])
                dialog.save_region()
                self.assertEqual(len(window.boundary_regions),1)
                window.undo_edit();self.assertEqual(window.boundary_regions,[])
                window.redo_edit();self.assertEqual(window.boundary_regions[0]['label'],'wall')
                for i in range(40):
                    before=window.edit_snapshot()
                    window.boundary_regions[0]['label']=f'wall_{i}'
                    window.record_edit('rename boundary',before)
                self.assertEqual(len(window._history_entries),30)
                window.project_io=object();window.refresh_history_actions()
                self.assertFalse(window.undo_action.isEnabled())
                old=window._history_index;window.undo_edit();self.assertEqual(window._history_index,old)
                window.project_io=None
                dialog.deleteLater()
            finally:
                with patch('jorvik.project_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):window.close()
