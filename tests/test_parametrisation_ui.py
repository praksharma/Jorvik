"""Transform controls, configuration loading and real preview reset lifecycle."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings, Qt
from PySide6.QtTest import QTest
import numpy as np

from jorvik.app import MainWindow
import test_shell

FIXTURE = Path(__file__).resolve().parents[1] / 'examples/geometries/cad/cylinder.brep'


class ParametrisationUiTest(unittest.TestCase):
    setUpClass = classmethod(test_shell.ShellTest.setUpClass.__func__)
    wait_for = test_shell.ShellTest.wait_for
    javascript = test_shell.ShellTest.javascript

    def diagnostics(self, window):
        return json.loads(self.javascript(window, 'JSON.stringify(window.geoViewer.diagnostics())'))

    def test_reset_edits_and_close_preserve_originals(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = QSettings(str(Path(directory) / 'settings.ini'), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            window.show()
            try:
                self.wait_for(lambda: window.view.is_ready)
                window.load_geometry(FIXTURE)
                self.wait_for(lambda: window.import_process is None)
                self.assertIsNotNone(window.geometry)
                window.pick_entity(2, 1, False)
                window.sampling.count.setValue(16)
                window.sampling.label.setText('wall')
                window.sampling.start()
                self.wait_for(lambda: window.sampling.process is None)
                self.assertEqual(len(window.sampling.batches), 1)
                other = deepcopy(window.sampling.batches[0])
                other['label'] = 'other'
                other['colour'] = '#ffc857'
                window.sampling.batches.append(other)
                window.sampled_areas.set_area(other)
                window.sampled_areas.items['other'].setCheckState(0, Qt.CheckState.Unchecked)
                window.hide_faces([2])
                window.sampling.refresh_preview()
                self.wait_for(lambda: self.diagnostics(window)['sampleCount'] == 32)
                original_geometry = deepcopy(window.geometry)
                original_batches = deepcopy(window.sampling.batches)
                original_visibility = deepcopy(window.view.geometry_visibility)
                baseline = self.diagnostics(window)
                original_selection = window.selection.copy()

                for dismiss in ('close', 'escape'):
                    with self.subTest(dismiss=dismiss):
                        window.open_parametrisation()
                        dialog = window.parametrisation_dialog
                        with patch.object(window.sampling, 'start') as generate, patch(
                            'jorvik.parametrisation_panel.export_configuration'
                        ) as export:
                            dialog.offsets[0].setValue(2)
                            self.assertIn('Parameters changed', dialog.status.text())
                            self.assertIsNone(window.view.translation_preview)
                            dialog.preview()
                            self.wait_for(lambda: self.diagnostics(window)['geometryOffset'] == [2, 0, 0])
                            dialog.offsets[0].setValue(3)
                            dialog.scope.setCurrentIndex(dialog.scope.findData('regions'))
                            for i in range(dialog.regions.count()):
                                item = dialog.regions.item(i)
                                item.setCheckState(Qt.CheckState.Checked if item.text() == 'wall' else Qt.CheckState.Unchecked)
                            self.assertIn('previous preview', dialog.status.text())
                            self.assertEqual(self.diagnostics(window)['geometryOffset'], [2, 0, 0])
                            dialog.preview()
                            dialog.preview()
                            self.wait_for(lambda: self.diagnostics(window)['sampleOffsets']['wall'] == [3, 0, 0])
                            current = self.diagnostics(window)
                            self.assertEqual(current['geometryOffset'], [0, 0, 0])
                            self.assertEqual(current['sampleOffsets']['other'], [0, 0, 0])
                            dialog.reset_button.click()
                            self.assertEqual(dialog.setup(), {
                                'schema_version': 2, 'scope': 'domain', 'regions': [],
                                'translation': [0., 0., 0.], 'rotation_axis': [0., 0., 1.],
                                'rotation_angle': 0., 'angle_units': 'degrees', 'scale': [1., 1., 1.],
                                'pivot': [0., 0., 0.], 'order': ['scale', 'rotate', 'translate'],
                                'frame': 'world', 'source_hash': window.geometry['source_hash']})
                            self.assertIsNone(window.view.translation_preview)
                            self.assertIn('Setup reset', dialog.status.text())
                            generate.assert_not_called()
                            export.assert_not_called()
                        self.wait_for(lambda: self.diagnostics(window)['sampleOffsets']['wall'] == [0, 0, 0])
                        # Export after reset must also evaluate the identity configuration.
                        path = Path(directory) / 'reset.npz'
                        with patch('jorvik.parametrisation_panel.QFileDialog.getSaveFileName',
                                   return_value=(str(path), 'NumPy archive (*.npz)')):
                            dialog.export_configuration()
                        with np.load(path, allow_pickle=False) as data:
                            np.testing.assert_array_equal(data['_gs_translation'], [0, 0, 0])
                            for batch in original_batches:
                                np.testing.assert_array_equal(data[batch['label']], batch['coordinates'])
                        # Dismiss an active preview through both real UI paths.
                        dialog.offsets[1].setValue(4)
                        dialog.preview()
                        if dismiss == 'close':
                            dialog.close()
                        else:
                            QTest.keyClick(dialog, Qt.Key.Key_Escape)
                        self.wait_for(lambda: window.parametrisation_dialog is None)
                        self.wait_for(lambda: self.diagnostics(window)['geometryOffset'] == [0, 0, 0])
                        window.open_parametrisation()
                        dialog = window.parametrisation_dialog
                        self.assertEqual(dialog.offsets[1].value(), 4)
                        self.assertIsNone(window.view.translation_preview)
                        dialog.reset_button.click()
                        dialog.close()
                        window.open_parametrisation()
                        dialog = window.parametrisation_dialog
                        self.assertEqual(dialog.setup()['translation'], [0, 0, 0])
                        self.assertEqual(dialog.setup()['regions'], [])
                        self.assertEqual(dialog.setup()['scope'], 'domain')
                        dialog.close()
                        current = self.diagnostics(window)
                        for key in ('visibleFaces', 'visibleEdges', 'visibleSampleCount', 'sampleColours'):
                            self.assertEqual(current[key], baseline[key])
                        self.assertEqual(window.geometry, original_geometry)
                        self.assertEqual(window.sampling.batches, original_batches)
                        self.assertEqual(window.view.geometry_visibility, original_visibility)
                        self.assertEqual(window.selection, original_selection)
                        self.assertEqual(window.sampled_areas.hidden_labels(), ['other'])
                        self.assertTrue(window.sampling.isEnabled())
                        self.assertTrue(window.open_action.isEnabled())
            finally:
                window.close()

    def test_transform_controls_load_export_history_and_project(self):
        from jorvik.parametrisation import transform_batches
        from jorvik.project import load_project
        from PySide6.QtWidgets import QMessageBox
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            window = MainWindow(QSettings(str(directory / 'settings.ini'), QSettings.Format.IniFormat))
            window.show()
            try:
                self.wait_for(lambda: window.view.is_ready)
                window.load_geometry(FIXTURE)
                self.wait_for(lambda: window.import_process is None)
                window.pick_entity(2, 1, False)
                window.sampling.count.setValue(8); window.sampling.label.setText('wall')
                window.sampling.start(); self.wait_for(lambda: window.sampling.process is None)
                originals = deepcopy(window.sampling.batches)
                window.open_parametrisation(); dialog = window.parametrisation_dialog
                dialog.rotation_angle.setValue(90)
                for spin, value in zip(dialog.scales, [2, 3, 4]): spin.setValue(value)
                for spin, value in zip(dialog.pivots, [1, 0, 0]): spin.setValue(value)
                for spin, value in zip(dialog.offsets, [.5, -.5, 0]): spin.setValue(value)
                setup = dialog.setup()
                self.assertEqual(setup['order'], ['scale', 'rotate', 'translate'])
                self.assertEqual(setup['frame'], 'world')
                dialog.preview(); dialog.preview()
                self.assertEqual(window.view.translation_preview, setup)
                self.assertEqual(window.sampling.batches, originals)
                setup_path = directory / 'transform.json'
                with patch('jorvik.parametrisation_panel.QFileDialog.getSaveFileName', return_value=(str(setup_path), '')):
                    dialog.save()
                self.assertEqual(json.loads(setup_path.read_text()), setup)
                dialog.reset_setup()
                self.assertEqual(dialog.setup()['scale'], [1, 1, 1])
                self.assertEqual(dialog.setup()['pivot'], [0, 0, 0])
                self.assertEqual(dialog.setup()['rotation_angle'], 0)
                with patch('jorvik.parametrisation_panel.QFileDialog.getOpenFileName', return_value=(str(setup_path), '')):
                    dialog.load()
                self.assertEqual(dialog.setup(), setup)
                output = directory / 'transformed.npz'
                with patch('jorvik.parametrisation_panel.QFileDialog.getSaveFileName', return_value=(str(output), '')):
                    dialog.export_configuration()
                expected = transform_batches(originals, setup)
                with np.load(output, allow_pickle=False) as data:
                    np.testing.assert_allclose(data['wall'], expected[0]['coordinates'])
                    metadata = json.loads(data['_gs_metadata'].item())
                    self.assertEqual(metadata['parametrisation'], setup)
                # A legacy translation setup uses identity rotation/scaling/pivot.
                legacy = {'schema_version': 1, 'scope': 'domain', 'translation': [2., 0., 0.],
                          'regions': [], 'source_hash': window.geometry['source_hash']}
                legacy_path = directory / 'legacy.json'; legacy_path.write_text(json.dumps(legacy))
                with patch('jorvik.parametrisation_panel.QFileDialog.getOpenFileName', return_value=(str(legacy_path), '')):
                    dialog.load()
                self.assertEqual(dialog.setup()['scale'], [1, 1, 1])
                self.assertEqual(dialog.setup()['rotation_angle'], 0)
                self.assertEqual(dialog.setup()['translation'], [2, 0, 0])
                # Radians are accepted on load and displayed explicitly as degrees.
                radians = dict(setup, rotation_angle=np.pi / 2, angle_units='radians')
                dialog.apply_setup(radians)
                self.assertAlmostEqual(dialog.rotation_angle.value(), 90)
                dialog.apply_setup(setup); dialog.preview()
                before = dialog.setup(); before_preview = deepcopy(window.view.translation_preview)
                for fields in ({'rotation_axis': [0, 0, 0]}, {'scale': [0, 1, 1]},
                               {'translation': [1e13, 0, 0]}, {'rotation_angle': 1e9},
                               {'rotation_axis': [1e-9, 0, 0]}):
                    bad = directory / 'invalid.json'; bad.write_text(json.dumps(dict(setup, **fields)))
                    with self.subTest(fields=fields), patch('jorvik.parametrisation_panel.QFileDialog.getOpenFileName', return_value=(str(bad), '')), patch('jorvik.parametrisation_panel.QMessageBox.warning') as warning:
                        dialog.load(); warning.assert_called_once()
                        self.assertEqual(dialog.setup(), before)
                        self.assertEqual(window.view.translation_preview, before_preview)
                dialog.close()
                self.wait_for(lambda: window.parametrisation_dialog is None)
                self.assertEqual(window.parametrisation_setup, setup)
                window.undo_edit(); self.assertIsNone(window.parametrisation_setup)
                window.redo_edit(); self.assertEqual(window.parametrisation_setup, setup)
                window.project_path = directory / 'transform.jorvik'
                self.assertTrue(window.save_project())
                self.assertEqual(load_project(window.project_path)[0]['state']['parametrisation'], setup)
                window.open_parametrisation(); dialog = window.parametrisation_dialog
                self.assertEqual(dialog.setup(), setup)
                dialog.close()
                self.assertEqual(window.sampling.batches, originals)
            finally:
                if window.parametrisation_dialog is not None: window.parametrisation_dialog.close()
                with patch('jorvik.project_ui.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard): window.close()
