"""Automated GUI and WebEngine tests for Wave-2 CAE/SciML UI upgrades."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import patch

import numpy as np
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QMessageBox

from jorvik.app import MainWindow
from jorvik.geometry import load_geometry
from jorvik.graphics import configure_graphics
from jorvik.parametrisation_panel import ParametrisationDialog
from jorvik.regions_panel import BoundaryRegionsDialog
from jorvik.theme import configure_theme


ROOT = Path(__file__).resolve().parents[1]
ANNULUS = ROOT / 'examples' / 'geometries' / 'cfdbench' / 'annulus_unstructured.geo'


class UiWave2Test(unittest.TestCase):
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

    def diagnostics(self, window):
        return json.loads(self.javascript(window, 'JSON.stringify(window.geoViewer.diagnostics())'))

    def test_ortho_projection_bbox_hud_regions_dialog_periodic_singularity_and_sweep_controls(self):
        with TemporaryDirectory() as directory:
            tmp = Path(directory)
            settings = QSettings(str(tmp / 'settings.ini'), QSettings.Format.IniFormat)
            window = MainWindow(settings)
            window.show()
            try:
                self.wait_for(lambda: window.view.is_ready or not window.view.error.isHidden())
                self.assertTrue(window.view.is_ready, window.view.error.text())

                # Create a 2D periodic channel with physical groups for auto-region creation & periodic pairing
                channel_geo = tmp / 'channel_groups.geo'
                channel_geo.write_text(
                    'Point(1) = {2, 1, 0, 0.2};\n'
                    'Point(2) = {6, 1, 0, 0.2};\n'
                    'Point(3) = {6, 3, 0, 0.2};\n'
                    'Point(4) = {2, 3, 0, 0.2};\n'
                    'Line(1) = {1, 2};\n'
                    'Line(2) = {2, 3};\n'
                    'Line(3) = {3, 4};\n'
                    'Line(4) = {4, 1};\n'
                    'Curve Loop(1) = {1, 2, 3, 4};\n'
                    'Plane Surface(1) = {1};\n'
                    'Physical Curve("inlet", 101) = {4};\n'
                    'Physical Curve("outlet", 102) = {2};\n'
                    'Physical Curve("walls", 103) = {1, 3};\n'
                )
                channel = load_geometry(channel_geo)
                window.show_geometry(channel)
                self.wait_for(lambda: self.diagnostics(window)['visibleFaces'] == 1)

                # 1. Orthographic vs. Perspective camera toggle & Bounding-box cage + HUD
                window.ortho_action.setChecked(True)
                self.wait_for(lambda: self.diagnostics(window)['projectionMode'] == 'orthographic')
                self.assertEqual(window.projection_combo.currentData(), 'orthographic')
                window.ortho_action.setChecked(False)
                self.wait_for(lambda: self.diagnostics(window)['projectionMode'] == 'perspective')

                window.bounding_box_action.setChecked(True)
                self.wait_for(lambda: self.diagnostics(window)['showBoundingBox'] is True)
                bbox_dims = self.diagnostics(window)['boundingBoxDimensions']
                self.assertAlmostEqual(bbox_dims[0], 4.0, places=3)
                self.assertAlmostEqual(bbox_dims[1], 2.0, places=3)
                bbox_hud_display = self.javascript(window, "document.getElementById('bbox-hud').style.display")
                self.assertEqual(bbox_hud_display, 'block')
                bbox_hud_text = self.javascript(window, "document.getElementById('bbox-hud').textContent")
                self.assertIn('Bounds: 4 × 2', bbox_hud_text)

                # 2. BoundaryRegionsDialog: entity filter, Check visible / Uncheck all, and Auto-create from groups
                reg_dlg = BoundaryRegionsDialog(window)
                try:
                    reg_dlg.kind.setCurrentIndex(reg_dlg.kind.findData(1))
                    reg_dlg.entity_filter.setText('Edge 4')
                    self.assertFalse(reg_dlg.entities.item(3).isHidden())
                    self.assertTrue(reg_dlg.entities.item(0).isHidden())
                    reg_dlg.check_visible_entities()
                    self.assertEqual(reg_dlg.entities.item(3).checkState().name, 'Checked')
                    self.assertEqual(reg_dlg.entities.item(0).checkState().name, 'Unchecked')
                    reg_dlg.uncheck_all_entities()
                    self.assertEqual(reg_dlg.entities.item(3).checkState().name, 'Unchecked')
                    reg_dlg.entity_filter.clear()

                    reg_dlg.auto_create_regions()
                    self.assertEqual([r['label'] for r in window.boundary_regions], ['inlet', 'outlet', 'walls'])
                finally:
                    reg_dlg.close()

                # 3. GUI Periodic Twin Sampling via SamplingPanel
                inlet_reg_id = next(r['id'] for r in window.boundary_regions if r['label'] == 'inlet')
                outlet_reg_id = next(r['id'] for r in window.boundary_regions if r['label'] == 'outlet')
                window.sampling.target.setCurrentIndex(window.sampling.target.findData({'region_id': inlet_reg_id}))
                window.sampling.periodic_target.setCurrentIndex(
                    window.sampling.periodic_target.findData({'region_id': outlet_reg_id})
                )
                window.sampling.count.setValue(24)
                window.sampling.label.setText('inlet_twin')
                window.sampling.start()
                self.wait_for(lambda: window.sampling.process is None and len(window.sampling.batches) == 2, seconds=30)
                self.assertEqual([b['label'] for b in window.sampling.batches], ['inlet_twin', 'outlet'])
                b_in, b_out = window.sampling.batches
                self.assertEqual(b_in['pairing']['pair_kind'], 'periodic')
                self.assertEqual(b_out['pairing']['pair_kind'], 'periodic')
                pts_in = np.asarray(b_in['coordinates'], dtype=np.float64)
                pts_out = np.asarray(b_out['coordinates'], dtype=np.float64)
                self.assertTrue(np.allclose(pts_out - pts_in, [4.0, 0.0, 0.0], atol=1e-6))

                # 4. GUI Auto-detect BC junctions & sharp corners singularity refinement
                window.sampling.periodic_target.setCurrentIndex(0)  # None
                window.sampling.target.setCurrentIndex(window.sampling.target.findData('interior'))
                window.sampling.refinement_target.setCurrentIndex(
                    window.sampling.refinement_target.findData({'singularity': True})
                )
                window.sampling.refinement_bias.setValue(5.0)
                window.sampling.count.setValue(48)
                window.sampling.label.setText('corner_interior')
                window.sampling.start()
                self.wait_for(lambda: window.sampling.process is None and len(window.sampling.batches) == 3, seconds=30)
                sing_b = window.sampling.batches[-1]
                self.assertEqual(sing_b.get('adaptive_mode'), 'geometric_singularity')
                self.assertTrue(np.isclose(np.sum(sing_b['weights']), sing_b['domain_measure'], rtol=1e-9))

                # 5. Per-point 3D Hover Inspection in status bar
                pt0 = b_in['coordinates'][0]
                window.on_point_hovered('inlet_twin', 0, pt0[0], pt0[1], pt0[2])
                msg = window.statusBar().currentMessage()
                self.assertIn('Point #0 (inlet_twin)', msg)
                self.assertIn('tag=4', msg)
                self.assertIn('w=', msg)
                self.assertIn('n=(', msg)

                # 6. ParametrisationDialog Center Pivot & SweepExportPanel float32 + multi-worker export
                param_dlg = ParametrisationDialog(window)
                try:
                    param_dlg.center_pivot_on_geometry()
                    # Channel bounds are X:[2, 6], Y:[1, 3], Z:[0, 0] -> center is (4.0, 2.0, 0.0)
                    piv_vals = [spin.value() for spin in param_dlg.pivots]
                    self.assertAlmostEqual(piv_vals[0], 4.0, places=4)
                    self.assertAlmostEqual(piv_vals[1], 2.0, places=4)
                    self.assertAlmostEqual(piv_vals[2], 0.0, places=4)

                    param_dlg.design_panel.apply_design({
                        'schema_version': 1,
                        'method': 'cartesian',
                        'seed': 42,
                        'count': 2,
                        'parameters': [{
                            'name': 'dx',
                            'target': 'translation_x',
                            'mode': 'range',
                            'start': 0.0,
                            'stop': 0.5,
                            'count': 2,
                            'reference': 0.0,
                            'unit': 'source',
                        }],
                    })
                    param_dlg.design_panel.generate()

                    sweep_out = tmp / 'float32_parallel_sweep'
                    param_dlg.sweep_panel.file_format.setCurrentIndex(param_dlg.sweep_panel.file_format.findData('npz'))
                    param_dlg.sweep_panel.float_dtype.setCurrentIndex(
                        param_dlg.sweep_panel.float_dtype.findData('float32')
                    )
                    param_dlg.sweep_panel.max_workers.setValue(2)
                    started = param_dlg.sweep_panel.start_export(sweep_out)
                    self.assertTrue(started)
                    self.wait_for(lambda: not param_dlg.sweep_panel.running, seconds=30)
                    self.assertEqual(param_dlg.sweep_panel.last_result['status'], 'complete')
                    with np.load(sweep_out / 'configuration_000001.npz') as npz_data:
                        self.assertEqual(npz_data['inlet_twin'].dtype, np.float32)
                finally:
                    param_dlg.close()

                # 7. GUI Shared internal interfaces (paired ±n) target on a 3D two-volume solid
                two_vol_geo = tmp / 'two_boxes.geo'
                two_vol_geo.write_text(
                    'SetFactory("OpenCASCADE");\n'
                    'Box(1) = {0, 0, 0, 1, 1, 1};\n'
                    'Box(2) = {1, 0, 0, 1, 1, 1};\n'
                    'BooleanFragments{ Volume{1}; Delete; }{ Volume{2}; Delete; }\n'
                )
                two_vol = load_geometry(two_vol_geo)
                window.show_geometry(two_vol)
                self.wait_for(lambda: self.diagnostics(window)['visibleFaces'] > 0)
                iface_idx = next(
                    i for i in range(window.sampling.target.count())
                    if isinstance(window.sampling.target.itemData(i), dict)
                    and window.sampling.target.itemData(i).get('internal_interface')
                )
                window.sampling.target.setCurrentIndex(iface_idx)
                window.sampling.refinement_target.setCurrentIndex(0)
                window.sampling.count.setValue(24)
                window.sampling.label.setText('shared_wall')
                window.sampling.start()
                self.wait_for(lambda: window.sampling.process is None and len(window.sampling.batches) == 1, seconds=30)
                iface_b = window.sampling.batches[0]
                self.assertEqual(iface_b.get('normal_kind'), 'interface_outward_pair')
                self.assertIn('partner_normals', iface_b)
            finally:
                window.close()


if __name__ == '__main__':
    unittest.main()
