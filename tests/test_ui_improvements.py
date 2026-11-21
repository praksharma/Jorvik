"""GUI and WebEngine viewport tests for the 6 CAE/SciML UI upgrades."""
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from jorvik.app import MainWindow
from jorvik.geometry import load_geometry
from jorvik.graphics import configure_graphics
from jorvik.regions import put_region
from jorvik.theme import configure_theme


ROOT = Path(__file__).resolve().parents[1]
ANNULUS = ROOT / 'examples' / 'geometries' / 'cfdbench' / 'annulus_unstructured.geo'


class UiImprovementsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configure_graphics('software')
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        configure_theme(cls.app, 'light')
        discard = patch('jorvik.project_ui.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard)
        discard.start()
        cls.addClassCleanup(discard.stop)

    def wait_for(self, predicate, seconds=25):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.01)
        self.fail('Timed out waiting for GUI / viewport state')

    def javascript(self, window, expression):
        values = []
        window.view.web.page().runJavaScript(expression, values.append)
        self.wait_for(lambda: bool(values))
        return values[0]

    def diagnostics(self, window):
        import json
        return json.loads(self.javascript(window, 'JSON.stringify(window.geoViewer.diagnostics())'))

    def test_tree_filter_shading_clipping_glyphs_colormaps_batch_pinn_and_screenshot(self):
        with TemporaryDirectory() as directory:
            tmp = Path(directory)
            settings = QSettings(str(tmp / 'settings.ini'), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            window.show()
            try:
                self.wait_for(lambda: window.view.is_ready or not window.view.error.isHidden())
                self.assertTrue(window.view.is_ready, window.view.error.text())

                geometry = load_geometry(ANNULUS)
                window.show_geometry(geometry)
                self.wait_for(lambda: self.diagnostics(window)['visibleFaces'] == 1)

                # 1. Live geometry tree filter
                window.tree_filter.setText('Edge 3')
                self.assertFalse(window.items[(1, 3)].isHidden())
                self.assertTrue(window.items[(1, 1)].isHidden())
                self.assertTrue(window.items[(2, 1)].isHidden())
                window.tree_filter.clear()
                self.assertFalse(window.items[(1, 1)].isHidden())
                self.assertFalse(window.items[(2, 1)].isHidden())

                # 2. CAD shading modes (Opaque, Translucent X-Ray, Wireframe) & 3D Clipping Plane
                window.shading_combo.setCurrentIndex(window.shading_combo.findData('translucent'))
                self.wait_for(lambda: self.diagnostics(window)['shadingMode'] == 'translucent')
                window.shading_combo.setCurrentIndex(window.shading_combo.findData('wireframe'))
                self.wait_for(lambda: self.diagnostics(window)['shadingMode'] == 'wireframe')
                window.shading_combo.setCurrentIndex(window.shading_combo.findData('opaque'))
                self.wait_for(lambda: self.diagnostics(window)['shadingMode'] == 'opaque')

                window.clip_axis_combo.setCurrentIndex(window.clip_axis_combo.findData('x'))
                window.clip_slider.setValue(25)
                window.clip_flip_action.setChecked(True)
                window.clip_samples_action.setChecked(True)
                self.wait_for(lambda: self.diagnostics(window)['clippingPlane']['axis'] == 'x')
                clip_diag = self.diagnostics(window)['clippingPlane']
                self.assertAlmostEqual(clip_diag['position'], 0.25, places=2)
                self.assertTrue(clip_diag['flip'])
                self.assertTrue(clip_diag['clipSamples'])
                window.clip_axis_combo.setCurrentIndex(window.clip_axis_combo.findData('off'))
                self.wait_for(lambda: self.diagnostics(window)['clippingPlane']['axis'] == 'off')

                # 3. Define two boundary regions and run 1-Click "Sample all regions + interior"
                #    with PINN enrichments (Darboux curvatures, R-function ADF, and Space-Time [0, 2])
                regions = put_region([], geometry, 'inner', [[1, 1], [1, 2], [1, 3], [1, 4]], '#f0a830')
                regions = put_region(regions, geometry, 'outer', [[1, 5], [1, 6], [1, 7], [1, 8]], '#41c4b8')
                window.boundary_regions = regions
                window.refresh_boundary_regions()
                window.sampling.count.setValue(32)
                window.sampling.method.setCurrentIndex(window.sampling.method.findData('sobol'))
                window.sampling.include_differential_geometry.setChecked(True)
                window.sampling.include_adf.setChecked(True)
                window.sampling.include_spacetime.setChecked(True)
                window.sampling.time_start.setValue(0.0)
                window.sampling.time_end.setValue(2.0)
                window.sampling.causal_windows.setValue(4)

                window.sampling.start_batch_all()
                self.wait_for(lambda: window.sampling.process is None and len(window.sampling.batches) == 3, seconds=40)
                labels = [b['label'] for b in window.sampling.batches]
                self.assertEqual(labels, ['inner', 'outer', 'interior'])

                inner_batch, outer_batch, interior_batch = window.sampling.batches
                self.assertEqual(inner_batch.get('differential_geometry_status'), 'available')
                self.assertEqual(inner_batch.get('curvature_status'), 'available')
                self.assertEqual(interior_batch.get('adf_status'), 'available')
                self.assertEqual(interior_batch.get('spacetime_status'), 'available')

                # 4. 3D Vector arrow glyphs (Outward normals & Darboux frame)
                window.sampling.vector_mode.setCurrentIndex(window.sampling.vector_mode.findData('normals'))
                window.sampling.glyph_scale.setValue(140)
                self.wait_for(lambda: self.diagnostics(window)['vectorMode'] == 'normals')
                diag_norm = self.diagnostics(window)
                self.assertEqual(diag_norm['glyphObjectCount'], 2)  # inner + outer have normals
                self.assertGreater(diag_norm['glyphSegmentCount'], 0)

                window.sampling.vector_mode.setCurrentIndex(window.sampling.vector_mode.findData('darboux'))
                self.wait_for(lambda: self.diagnostics(window)['vectorMode'] == 'darboux')
                diag_darboux = self.diagnostics(window)
                self.assertGreater(diag_darboux['glyphObjectCount'], diag_norm['glyphObjectCount'])

                # 5. Scalar field point-cloud colormaps & HUD colorbar
                for field_key in ('adf', 'weight', 'mean_curvature', 'time', 'tag'):
                    window.sampling.color_by.setCurrentIndex(window.sampling.color_by.findData(field_key))
                    self.wait_for(lambda fk=field_key: self.diagnostics(window)['colorBy'] == fk)
                    diag_c = self.diagnostics(window)
                    self.assertIsNotNone(diag_c['scalarRange'])
                    hud_display = self.javascript(window, "document.getElementById('colorbar-hud').style.display")
                    self.assertEqual(hud_display, 'block')

                window.sampling.colormap.setCurrentIndex(window.sampling.colormap.findData('plasma'))
                self.wait_for(lambda: self.diagnostics(window)['colormap'] == 'plasma')

                # Reset to dataset colour hides HUD colorbar
                window.sampling.color_by.setCurrentIndex(window.sampling.color_by.findData('dataset'))
                self.wait_for(lambda: self.diagnostics(window)['colorBy'] == 'dataset')
                self.assertEqual(
                    self.javascript(window, "document.getElementById('colorbar-hud').style.display"),
                    'none',
                )

                # 6. Hover status bar coordinate readout & Viewport PNG screenshot export
                window.on_viewport_hovered(1, 1, 0.5, 0.0, 0.0)
                self.assertIn('Edge 1', window.statusBar().currentMessage())
                self.assertIn('Region: inner', window.statusBar().currentMessage())
                self.assertIn('x=0.5', window.statusBar().currentMessage())

                shot_path = tmp / 'viewport_capture.png'
                saved = window.save_viewport_screenshot(shot_path)
                self.assertEqual(saved, shot_path)
                self.assertTrue(shot_path.is_file())
                self.assertGreater(shot_path.stat().st_size, 100)

                # Single-step undo reverses the batch generation of all 3 datasets
                window.undo_action.trigger()
                self.assertEqual(len(window.sampling.batches), 0)
                window.redo_action.trigger()
                self.assertEqual(len(window.sampling.batches), 3)
            finally:
                window.close()


if __name__ == '__main__':
    unittest.main()
