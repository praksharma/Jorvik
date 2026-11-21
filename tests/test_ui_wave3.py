"""Automated GUI and WebEngine tests for Wave-3 (Final Polish) UI upgrades."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QMimeData, QPointF, QSettings, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from jorvik.app import MainWindow
from jorvik.area_editor import AreaEditor
from jorvik.geometry import load_geometry
from jorvik.graphics import configure_graphics
from jorvik.theme import configure_theme


ROOT = Path(__file__).resolve().parents[1]
ANNULUS = ROOT / 'examples' / 'geometries' / 'cfdbench' / 'annulus_unstructured.geo'


class UiWave3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configure_graphics('software')
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setQuitOnLastWindowClosed(False)
        configure_theme(cls.app, 'light')
        discard = patch('jorvik.project_ui.QMessageBox.question', return_value=QMessageBox.StandardButton.Discard)
        discard.start()
        cls.addClassCleanup(discard.stop)

    def wait_for(self, predicate, seconds=30):
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

    def camera_state(self, window):
        return json.loads(self.javascript(window, 'JSON.stringify(window.geoViewer.cameraState())'))

    def test_area_editor_pinn_recent_files_drag_drop_zoom_containment_and_python_snippet(self):
        with TemporaryDirectory() as directory:
            tmp = Path(directory)
            settings = QSettings(str(tmp / 'settings.ini'), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            window.show()
            try:
                self.wait_for(lambda: window.view.is_ready or not window.view.error.isHidden())
                self.assertTrue(window.view.is_ready, window.view.error.text())

                # 1. Drag-and-Drop opening of ANNULUS .geo file and Open Recent menu tracking
                mime = QMimeData()
                mime.setUrls([QUrl.fromLocalFile(str(ANNULUS))])
                drag_event = QDragEnterEvent(
                    QPointF(50.0, 50.0).toPoint(),
                    Qt.DropAction.CopyAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                window.dragEnterEvent(drag_event)
                self.assertTrue(drag_event.isAccepted())

                drop_event = QDropEvent(
                    QPointF(50.0, 50.0),
                    Qt.DropAction.CopyAction,
                    mime,
                    Qt.MouseButton.LeftButton,
                    Qt.KeyboardModifier.NoModifier,
                )
                window.dropEvent(drop_event)
                self.wait_for(lambda: window.import_process is None and window.geometry is not None)
                self.assertEqual(window.geometry['name'], ANNULUS.name)
                self.assertIn(str(ANNULUS.resolve()), window.recent_files())
                self.assertGreaterEqual(len(window.recent_menu.actions()), 2)

                # 2. Zoom to selected CAD entity (Edge 1 in quadrant I has positive X, Y center)
                initial_cam = self.camera_state(window)
                window.set_selection_mode('edge')
                window.selection = {(1, 1)}
                window.update_selection()
                window.zoom_to_selection()
                self.wait_for(
                    lambda: not np.allclose(self.camera_state(window)['target'], initial_cam['target'], atol=1e-3)
                )
                zoomed_cam = self.camera_state(window)
                self.assertAlmostEqual(zoomed_cam['target'][0], -0.25, places=2)
                self.assertAlmostEqual(zoomed_cam['target'][1], 0.25, places=2)

                # 3. Generate a boundary dataset with Darboux curvature and edit it in AreaEditor
                window.sampling.target.setCurrentIndex(window.sampling.target.findData('selection'))
                window.sampling.count.setValue(24)
                window.sampling.label.setText('arc_1')
                window.sampling.include_differential_geometry.setChecked(True)
                window.sampling.include_spacetime.setChecked(False)
                window.sampling.start()
                self.wait_for(lambda: window.sampling.process is None and len(window.sampling.batches) == 1)
                self.assertEqual(window.sampling.batches[0].get('differential_geometry_status'), 'available')

                editor = AreaEditor(window, 'arc_1')
                try:
                    self.assertTrue(editor.include_differential_geometry.isChecked())
                    editor.entity_filter.setText('Edge 2')
                    self.assertFalse(editor.entities.item(1).isHidden())
                    self.assertTrue(editor.entities.item(0).isHidden())
                    editor.entity_filter.clear()

                    # Enable Space-Time and update count in AreaEditor
                    editor.include_spacetime.setChecked(True)
                    editor.count.setValue(32)
                    editor.apply()
                finally:
                    editor.close()

                self.wait_for(lambda: window.sampling.process is None and window.sampling.batches[0]['count'] == 32)
                edited_batch = window.sampling.batches[0]
                self.assertEqual(edited_batch.get('differential_geometry_status'), 'available')
                self.assertEqual(edited_batch.get('spacetime_status'), 'available')

                # 4. Verify CAD trimming & containment and Copy Python loader snippet
                report = window.verify_area_containment('arc_1')
                self.assertIsNotNone(report)
                self.assertTrue(report['containment_verified'])
                self.assertEqual(report['verified_points'], 32)
                self.assertIn('CAD Trimming & Containment Verified: arc_1', window.properties.text())

                snippet = window.copy_python_loader_snippet('arc_1')
                self.assertIn('xr.open_dataset', snippet)
                self.assertIn("'arc_1'", snippet)
                self.assertIn('mean_curvature_coordinate', snippet)
                self.assertIn('times_coordinate', snippet)
                self.assertEqual(QApplication.clipboard().text(), snippet)

                # Clear recent files
                window.clear_recent_files()
                self.assertEqual(window.recent_files(), [])
            finally:
                window.close()


if __name__ == '__main__':
    unittest.main()
