"""The actual offline scene applies the same affine matrix as dataset export."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import numpy as np
from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import QMessageBox
from jorvik.app import MainWindow
from jorvik.geometry import load_geometry
from jorvik.parametrisation import affine_transform
from test_export import batch
from test_project import FIXTURE
import test_shell


class AffinePreviewTest(unittest.TestCase):
    setUpClass=classmethod(test_shell.ShellTest.setUpClass.__func__)
    wait_for=test_shell.ShellTest.wait_for
    javascript=test_shell.ShellTest.javascript

    def diagnostics(self, window):
        return json.loads(self.javascript(window,'JSON.stringify(window.geoViewer.diagnostics())'))

    def test_affine_preview_matches_backend_and_restores_original_buffers(self):
        with tempfile.TemporaryDirectory() as directory:
            window=MainWindow(QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat))
            window.show()
            try:
                self.wait_for(lambda:window.view.is_ready)
                geometry=load_geometry(FIXTURE);window.show_geometry(geometry)
                boundary=batch('wall')
                boundary.update(source=geometry['source'],source_hash=geometry['source_hash'],
                                document=geometry['document'],entities=[[2,1]],entity_tags=[1]*3,
                                coordinates=[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]])
                other=deepcopy(boundary);other['label']='hidden'
                for record in (boundary,other):
                    window.sampling.batches.append(record);window.sampled_areas.set_area(record)
                window.sampled_areas.items['hidden'].setCheckState(0,Qt.CheckState.Unchecked)
                window.pick_entity(2,1,False)
                window.hide_faces([1]);window.sampling.refresh_preview()
                originals=deepcopy(window.sampling.batches)
                original_geometry=deepcopy(window.geometry)
                original_visibility=deepcopy(window.view.geometry_visibility)
                baseline=self.diagnostics(window)
                camera_before=json.loads(self.javascript(window,'JSON.stringify(window.geoViewer.cameraState())'))
                config={'schema_version':2,'scope':'domain','regions':[],
                        'source_hash':geometry['source_hash'],'translation':[5.,-2.,3.],
                        'rotation_axis':[0.,0.,1.],'rotation_angle':90.,'angle_units':'degrees',
                        'scale':[2.,3.,4.],'pivot':[.5,-.5,1.],
                        'order':['scale','rotate','translate'],'frame':'world'}
                linear,offset=affine_transform(config)
                first=np.asarray(boundary['coordinates'][0])
                expected=linear@first+offset
                for _ in range(3):window.view.set_translation_preview(config)
                current=self.diagnostics(window)
                for label in ('wall','hidden'):
                    np.testing.assert_allclose(current['firstSamplePoints'][label],expected,atol=1e-6)
                    matrix=np.asarray(current['sampleMatrices'][label]).reshape(4,4,order='F')
                    np.testing.assert_allclose(matrix[:3,:3],linear,atol=1e-12)
                    np.testing.assert_allclose(matrix[:3,3],offset,atol=1e-12)
                key=next(iter(current['firstGeometryPoints']))
                np.testing.assert_allclose(current['firstGeometryPoints'][key],
                                           linear@np.asarray(baseline['firstGeometryPoints'][key])+offset,atol=1e-6)
                self.assertEqual(current['selectionCount'],baseline['selectionCount'])
                self.assertEqual(current['visibleSampleCount'],baseline['visibleSampleCount'])
                region=dict(config,scope='regions',regions=['wall'])
                window.view.set_translation_preview(region)
                current=self.diagnostics(window)
                np.testing.assert_allclose(current['firstSamplePoints']['wall'],expected,atol=1e-6)
                np.testing.assert_allclose(current['firstSamplePoints']['hidden'],first)
                self.assertEqual(current['geometryMatrix'],np.eye(4).flatten(order='F').tolist())
                # Rebuilding sample objects during an active preview uses original
                # buffers and reapplies just one matrix, retaining hidden status.
                window.sampling.refresh_preview()
                np.testing.assert_allclose(self.diagnostics(window)['firstSamplePoints']['wall'],expected,atol=1e-6)
                window.view.set_translation_preview(None)
                restored=self.diagnostics(window)
                for label in ('wall','hidden'):
                    np.testing.assert_allclose(restored['firstSamplePoints'][label],first)
                for key in baseline['firstGeometryPoints']:
                    np.testing.assert_allclose(restored['firstGeometryPoints'][key],baseline['firstGeometryPoints'][key])
                camera_after=json.loads(self.javascript(window,'JSON.stringify(window.geoViewer.cameraState())'))
                for name in ('position','target','up','near','far'):
                    np.testing.assert_allclose(camera_after[name],camera_before[name],atol=1e-6)
                self.assertEqual(window.geometry,original_geometry)
                self.assertEqual(window.sampling.batches,originals)
                self.assertEqual(window.view.geometry_visibility,original_visibility)
                self.assertEqual(window.sampled_areas.hidden_labels(),['hidden'])
            finally:
                with patch('jorvik.project_ui.QMessageBox.question',return_value=QMessageBox.StandardButton.Discard):window.close()

    def test_picking_uses_transformed_faces_and_edges(self):
        from test_picking_ui import PickingUiTest, face, edge
        with tempfile.TemporaryDirectory() as directory:
            window=MainWindow(QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat))
            window.show()
            try:
                self.wait_for(lambda:window.view.is_ready)
                PickingUiTest.show(self,window,[face(1,.5,0),edge(1,(-.8,.8,0),(.8,.8,0))])
                config={'schema_version':2,'scope':'domain','regions':[],
                        'source_hash':'fixture','translation':[1.3,.2,0.],
                        'rotation_axis':[0.,0.,1.],'rotation_angle':90.,'angle_units':'degrees',
                        'scale':[2.,1.,1.],'pivot':[0.,0.,0.],
                        'order':['scale','rotate','translate'],'frame':'world'}
                window.view.set_translation_preview(config)
                PickingUiTest.camera(self,window,6)
                window.set_selection_mode('face')
                self.assertEqual(PickingUiTest.click(self,window,(1.3,.2,0),6),{(2,1)})
                self.assertEqual(PickingUiTest.click(self,window,(0,0,0),6),set())
                window.set_selection_mode('edge')
                self.assertEqual(PickingUiTest.click(self,window,(.5,.2,0),6),{(1,1)})
                self.assertEqual(PickingUiTest.click(self,window,(0,.8,0),6),set())
                window.view.set_translation_preview(None)
                PickingUiTest.camera(self,window,6)
                self.assertEqual(PickingUiTest.click(self,window,(0,.8,0),6),{(1,1)})
            finally:window.close()

    def test_legacy_preview_queued_before_renderer_ready(self):
        from test_picking_ui import face
        with tempfile.TemporaryDirectory() as directory:
            window=MainWindow(QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat))
            try:
                geometry={'document':'early-preview','name':'Early preview','source':'early.geo',
                          'source_hash':'fixture','dimension':2,'groups':[],'entities':[face(1,.5,0)]}
                window.show_geometry(geometry)
                setup={'schema_version':1,'scope':'domain','regions':[],
                       'source_hash':'fixture','translation':[2.,3.,4.]}
                window.view.set_translation_preview(setup)
                self.assertEqual(window.view.translation_preview,setup)
                window.show();self.wait_for(lambda:window.view.is_ready)
                current=self.diagnostics(window)
                self.assertEqual(current['geometryOffset'],[2,3,4])
                np.testing.assert_allclose(current['firstGeometryPoints']['2:1'],[1.5,2.5,4.])
                window.view.set_translation_preview(None)
                np.testing.assert_allclose(self.diagnostics(window)['firstGeometryPoints']['2:1'],[-.5,-.5,0.])
            finally:window.close()
