"""Portable CAD/recipe copies without point arrays, and unchanged source workspaces."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import zipfile

from jorvik.geometry import load_geometry
from jorvik.project import save_project,load_project
from jorvik.sampling import sample_geometry
from jorvik.sampling_recipe import request_from_area
from jorvik.regions import put_region
from test_project import FIXTURE,state
from test_affine_transforms import affine_setup


def samples(geometry):
    regions=put_region([],geometry,'inner',[[1,t] for t in range(1,5)],'#41c4b8')
    request={key:geometry[key] for key in ('source','source_hash','document','source_format','import_options')}
    request.update(label='wall',target='region',entities=regions[0]['entities'],boundary_region=regions[0],
                   method='sobol',count=16,seed=42,resolution=32,compute_mode='reproducible',cpu_threads=1)
    return sample_geometry(request),regions


class LightweightProjectTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geometry=load_geometry(FIXTURE); cls.cloud,cls.regions=samples(cls.geometry)

    def test_round_trip_recipe_regeneration_and_normal_save(self):
        geometry,cloud=self.geometry,self.cloud
        settings=state();settings['boundary_regions']=self.regions
        settings['parametrisation']=affine_setup(source_hash=geometry['source_hash'],scope='regions',regions=['wall'])
        settings['isolate']=True;settings['isolate_geometry']=True
        settings['geometry_visibility']['isolatedEntities']=cloud['entities']
        original=deepcopy((cloud,settings))
        with TemporaryDirectory() as directory:
            path=Path(directory)/'light.jorvik'
            save_project(path,geometry,[(cloud,'Generated',False)],settings,include_samples=False)
            manifest,source=load_project(path)
            self.assertEqual(manifest['schema_version'],2);self.assertTrue(manifest['samples_omitted'])
            self.assertEqual(source,FIXTURE.read_bytes())
            recipe=manifest['areas'][0]['area']
            self.assertEqual(manifest['areas'][0]['status'],'Not generated (samples omitted)')
            self.assertFalse(manifest['state']['isolate']);self.assertFalse(manifest['state']['isolate_geometry'])
            self.assertIsNone(manifest['state']['geometry_visibility']['isolatedEntities'])
            self.assertEqual(manifest['state']['parametrisation'],settings['parametrisation'])
            self.assertEqual(manifest['state']['boundary_regions'],self.regions)
            for key in ('coordinates','entity_tags','normals','normal_planes','normal_status'):
                self.assertNotIn(key,recipe)
            with zipfile.ZipFile(path) as archive:
                self.assertFalse(any(name.startswith('areas/') for name in archive.namelist()))
                self.assertTrue(any(name.startswith('geometry/') for name in archive.namelist()))
            regenerated=sample_geometry(request_from_area(recipe,manifest['geometry']))
            self.assertEqual(regenerated['label'],'wall');self.assertEqual(regenerated['count'],16)
            self.assertEqual(regenerated['boundary_region'],cloud['boundary_region'])
            saved=manifest['state']; saved['isolate']=False
            save_project(path,geometry,[(regenerated,'Generated',True)],saved)
            full,_=load_project(path)
            self.assertNotIn('samples_omitted',full)
            self.assertEqual(full['areas'][0]['area']['coordinates'],regenerated['coordinates'])
        self.assertEqual((cloud,settings),original)

    def test_no_numeric_traversal_grid_budget_legacy_and_atomic_failure(self):
        class Unreadable:
            def __deepcopy__(self,memo):raise AssertionError('Copied a point buffer')
            def __array__(self,*args,**kwargs):raise AssertionError('Converted a point buffer')
            def __iter__(self):raise AssertionError('Traversed a point buffer')
        cloud=deepcopy(self.cloud);cloud.update(coordinates=Unreadable(),entity_tags=Unreadable(),normals=Unreadable(),requested_count=64)
        settings=state();settings['boundary_regions']=self.regions
        with TemporaryDirectory() as directory:
            path=Path(directory)/'light.gsproj'
            save_project(path,self.geometry,[(cloud,'Generated',True)],settings,include_samples=False)
            loaded,_=load_project(path); self.assertEqual(loaded['areas'][0]['area']['count'],64)
            self.assertEqual(request_from_area(loaded['areas'][0]['area'],self.geometry)['count'],64)
            before=path.read_bytes()
            with patch('numpy.save',side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):save_project(path,self.geometry,[(cloud,'Generated',True)],settings,include_samples=False)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(list(Path(directory).iterdir())),1)
            legacy=deepcopy(self.geometry);legacy.pop('source_format');legacy.pop('import_options')
            record=deepcopy(self.cloud);record.pop('source_format');record.pop('import_options')
            save_project(path,legacy,[(record,'Generated',True)],settings,include_samples=False)
            self.assertTrue(load_project(path)[0]['samples_omitted'])

    def test_corrupt_recipes_and_hidden_point_payloads_rejected(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'light.jorvik'
            settings=state(); settings['boundary_regions']=self.regions
            save_project(path,self.geometry,[(self.cloud,'Generated',True)],settings,include_samples=False)
            with zipfile.ZipFile(path) as archive: original={name:archive.read(name) for name in archive.namelist()}
            for variant in ('missing_entity','invalid_count','array_payload','generated_status','marker'):
                files=dict(original);manifest=json.loads(files['manifest.json']);area=manifest['areas'][0]['area']
                if variant=='missing_entity':area['entities']=[[1,999]]
                elif variant=='invalid_count':area['count']=0;area['requested_count']=0
                elif variant=='array_payload':files['areas/0_coordinates.npy']=b'not permitted'
                elif variant=='generated_status':manifest['areas'][0]['status']='Generated'
                else:manifest['samples_omitted']='true'
                files['manifest.json']=json.dumps(manifest).encode()
                with zipfile.ZipFile(path,'w') as archive:
                    for name,data in files.items():archive.writestr(name,data)
                with self.subTest(variant=variant),self.assertRaises(ValueError):load_project(path)


class LightweightProjectUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_shell
        test_shell.ShellTest.setUpClass.__func__(cls)

    def wait_for(self,predicate):
        import test_shell
        test_shell.ShellTest.wait_for(self,predicate)

    def window(self,directory):
        from jorvik.app import MainWindow
        from PySide6.QtCore import QSettings
        window=MainWindow(QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat))
        window.confirm_unsaved_changes=lambda:True
        return window

    def test_copy_preserves_active_workspace_and_regenerates_after_open(self):
        with TemporaryDirectory() as directory:
            window=self.window(directory)
            try:
                geometry=load_geometry(FIXTURE);cloud,regions=samples(geometry)
                window.show_geometry(geometry);window.boundary_regions=regions;window.refresh_boundary_regions()
                window.sampling.batches=[cloud];window.sampled_areas.set_area(cloud)
                window.parametrisation_setup=affine_setup(source_hash=geometry['source_hash'],scope='regions',regions=['wall'])
                original_path=Path(directory)/'full.jorvik';window.project_path=original_path
                self.assertTrue(window.save_project())
                full_bytes=original_path.read_bytes();digest=window.saved_digest;before=window.edit_snapshot()
                title=window.windowTitle();copy_path=Path(directory)/'recipes.jorvik'
                with patch('jorvik.project_ui.QFileDialog.getSaveFileName',return_value=(str(copy_path),'')):
                    self.assertTrue(window.save_lightweight_copy())
                self.assertEqual(original_path.read_bytes(),full_bytes)
                self.assertEqual(window.project_path,original_path)
                self.assertEqual(window.saved_digest,digest);self.assertEqual(window.windowTitle(),title)
                self.assertEqual(window.edit_snapshot(),before)
                self.assertEqual(len(window.sampling.batches),1)
                self.assertTrue(window.load_project_file(copy_path,raise_errors=True))
                self.assertEqual(window.sampling.batches,[])
                self.assertEqual(window.sampled_areas.areas['wall'][1],'Not generated (samples omitted)')
                self.assertIn('without points',window.sampling.summary.text())
                self.assertIsNotNone(window.parametrisation_setup)
                window.sampling.count.setValue(7)
                self.assertTrue(window.regenerate_area('wall'));self.wait_for(lambda:window.sampling.process is None)
                self.assertEqual(window.sampling.batches[0]['count'],16)
                self.assertEqual(window.sampling.batches[0]['boundary_region'],regions[0])
                window.undo_edit();self.assertEqual(window.sampling.batches,[])
                window.redo_edit();self.assertEqual(window.sampling.batches[0]['count'],16)
                self.assertTrue(window.save_project())
                self.assertNotIn('samples_omitted',load_project(copy_path)[0])
                self.assertEqual(original_path.read_bytes(),full_bytes)
            finally:window.close();self.app.processEvents()

    def test_copy_cancel_same_path_failure_and_empty_action(self):
        with TemporaryDirectory() as directory:
            window=self.window(directory)
            try:
                self.assertFalse(window.lightweight_copy_action.isEnabled())
                self.assertFalse(window.save_lightweight_copy())
                window.show_geometry(load_geometry(FIXTURE))
                self.assertTrue(window.lightweight_copy_action.isEnabled())
                path=Path(directory)/'existing.jorvik';window.project_path=path;self.assertTrue(window.save_project())
                before=path.read_bytes();digest=window.saved_digest
                with patch('jorvik.project_ui.QFileDialog.getSaveFileName',return_value=('','')):
                    self.assertFalse(window.save_lightweight_copy())
                with patch('jorvik.project_ui.QFileDialog.getSaveFileName',return_value=(str(path),'')),patch('jorvik.project_ui.QMessageBox.warning') as warning:
                    self.assertFalse(window.save_lightweight_copy());warning.assert_called_once()
                with patch('jorvik.project_ui.QFileDialog.getSaveFileName',return_value=(str(FIXTURE),'')),patch('jorvik.project_ui.QMessageBox.warning'):
                    self.assertFalse(window.save_lightweight_copy())
                copy_path=Path(directory)/'copy.jorvik'
                with patch('jorvik.project_ui.QFileDialog.getSaveFileName',return_value=(str(copy_path),'')),patch('jorvik.project_ui.save_project',side_effect=OSError('Disk full')),patch('jorvik.project_ui.QMessageBox.warning'):
                    self.assertFalse(window.save_lightweight_copy())
                self.assertFalse(copy_path.exists());self.assertEqual(path.read_bytes(),before)
                self.assertEqual(window.saved_digest,digest);self.assertEqual(window.project_path,path)
                self.assertTrue(window.lightweight_copy_action.isEnabled())
            finally:window.close();self.app.processEvents()
