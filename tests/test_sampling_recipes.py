"""Settings-only sampling recipes, strict CAD bindings and GUI undo/redo."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jorvik.geometry import load_geometry
from jorvik.regions import put_region
from jorvik.sampling_recipe import create_recipe, save_recipe, load_recipe, bind_recipe, validate_recipe
from jorvik.sampling import sample_geometry
from test_project import FIXTURE


def request(geometry, **changes):
    data={key:deepcopy(geometry[key]) for key in ('source','source_hash','document','source_format','import_options')}
    data.update(target='selection',entities=[[1,1],[1,2]],method='sobol',count=16,seed=42,
                label='wall / λ',resolution=32,compute_mode='reproducible',cpu_threads=1)
    data.update(changes)
    return data


class SamplingRecipeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.geometry=load_geometry(FIXTURE)

    def test_round_trip_bind_moved_source_and_sampling(self):
        original=request(self.geometry); recipe=create_recipe(original)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'recipe.json'; save_recipe(recipe,path)
            self.assertEqual(load_recipe(path),recipe)
            self.assertNotIn('source',recipe['geometry']); self.assertNotIn('document',recipe['geometry'])
            moved=Path(directory)/'same-model.geo'; moved.write_bytes(FIXTURE.read_bytes())
            geometry=load_geometry(moved); bound=bind_recipe(recipe,geometry)
            self.assertEqual(bound['document'],geometry['document'])
            self.assertEqual(bound['source'],str(moved.resolve()))
            cloud=sample_geometry(bound)
            self.assertEqual(cloud['count'],16); self.assertEqual(cloud['label'],'wall / λ')
            self.assertEqual(cloud['entities'],[[1,1],[1,2]])
            before=path.read_bytes()
            with patch('pathlib.Path.write_text',side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):save_recipe(recipe,path)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),['recipe.json','same-model.geo'])
        self.assertEqual(original,request(self.geometry))

    def test_regions_groups_interior_and_changed_bindings(self):
        geometry=self.geometry
        regions=put_region([],geometry,'inner',[[1,t] for t in range(1,5)],'#41c4b8')
        region=create_recipe(request(geometry,target='region',entities=regions[0]['entities'],boundary_region=regions[0]))
        self.assertEqual(bind_recipe(region,geometry,regions)['boundary_region'],regions[0])
        replaced=put_region([],geometry,'inner',regions[0]['entities'],'#ffc857')
        self.assertNotEqual(replaced[0]['id'],regions[0]['id'])
        self.assertEqual(bind_recipe(region,geometry,replaced)['boundary_region']['id'],replaced[0]['id'])
        with self.assertRaisesRegex(ValueError,'region'):bind_recipe(region,geometry)
        changed=deepcopy(regions); changed[0]['entities']=[[1,1]]
        with self.assertRaisesRegex(ValueError,'membership'):bind_recipe(region,geometry,changed)
        group=next(item for item in geometry['groups'] if item['dimension']==1)
        recipe=create_recipe(request(geometry,target='group',entities=[[1,t] for t in group['entities']]))
        self.assertEqual(bind_recipe(recipe,geometry)['target'],'group')
        missing=deepcopy(geometry); missing['groups']=[]
        with self.assertRaisesRegex(ValueError,'group'):bind_recipe(recipe,missing)
        interior=create_recipe(request(geometry,target='interior'))
        self.assertEqual(interior['sampling']['entities'],[])
        self.assertEqual(bind_recipe(interior,geometry)['target'],'interior')
        for field,value in [('source_hash','0'*64),('source_format','step'),('import_options',{})]:
            changed=deepcopy(geometry); changed[field]=value
            with self.assertRaisesRegex(ValueError,'differ'):bind_recipe(interior,changed)
        missing=deepcopy(geometry); missing['entities']=[item for item in geometry['entities'] if not(item['dimension']==1 and item['tag']==1)]
        with self.assertRaisesRegex(ValueError,'missing'):bind_recipe(create_recipe(request(geometry)),missing)

    def test_invalid_payload_settings_and_schema(self):
        recipe=create_recipe(request(self.geometry))
        for field,value in [('count',True),('count',50001),('seed',-1),('resolution',33),('method','eval'),
                            ('cpu_threads',2),('compute_mode','gpu'),('label',''),('entities',[[1,1],[2,1]])]:
            bad=deepcopy(recipe); bad['sampling'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):validate_recipe(bad)
        for where in ('root','sampling','geometry'):
            bad=deepcopy(recipe); (bad if where=='root' else bad[where])['coordinates']=[[1,2,3]]
            with self.assertRaises(ValueError):validate_recipe(bad)
        bad=deepcopy(recipe); bad['schema_version']=99
        with self.assertRaises(ValueError):validate_recipe(bad)
        with TemporaryDirectory() as directory:
            path=Path(directory)/'bad.json'; path.write_text('{broken')
            with self.assertRaises(ValueError):load_recipe(path)


class SamplingRecipeUiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_shell
        test_shell.ShellTest.setUpClass.__func__(cls)

    def window(self,directory):
        from jorvik.app import MainWindow
        from PySide6.QtCore import QSettings
        window=MainWindow(QSettings(str(Path(directory)/'settings.ini'),QSettings.Format.IniFormat))
        window.show_geometry(load_geometry(FIXTURE))
        window.confirm_unsaved_changes=lambda:True
        return window

    def test_gui_save_load_no_generation_and_undo_redo(self):
        with TemporaryDirectory() as directory:
            window=self.window(directory); panel=window.sampling
            try:
                panel.target.setCurrentIndex(panel.target.findData('interior'))
                panel.method.setCurrentIndex(panel.method.findData('sobol')); panel.count.setValue(32)
                panel.compute_mode.setCurrentIndex(panel.compute_mode.findData('reproducible'))
                panel.label.setText('interior recipe')
                before=panel.recipe_control_state(); path=Path(directory)/'settings.json'
                with patch('jorvik.recipe_ui.QFileDialog.getSaveFileName',return_value=(str(path),'')),patch.object(panel,'generate_request') as generate:
                    panel.save_recipe_button.click(); generate.assert_not_called()
                self.assertEqual(panel.recipe_control_state(),before)
                panel.count.setValue(7); panel.label.setText('different'); altered=panel.recipe_control_state()
                with patch('jorvik.recipe_ui.QFileDialog.getOpenFileName',return_value=(str(path),'')),patch.object(panel,'generate_request') as generate:
                    panel.load_recipe_button.click(); generate.assert_not_called()
                self.assertEqual(panel.count.value(),32); self.assertEqual(panel.label.text(),'interior recipe')
                self.assertEqual(panel.batches,[]); self.assertIn('Review',panel.summary.text())
                loaded=panel.recipe_control_state(); window.undo_edit()
                self.assertEqual(panel.recipe_control_state(),altered)
                window.redo_edit(); self.assertEqual(panel.recipe_control_state(),loaded)
                self.assertEqual(panel.batches,[])
            finally:window.close();self.app.processEvents()

    def test_invalid_load_is_atomic_and_selection_recipe_restores_entities(self):
        with TemporaryDirectory() as directory:
            window=self.window(directory); panel=window.sampling
            try:
                recipe=create_recipe(request(window.geometry))
                before=panel.recipe_control_state(); entries=len(window._history_entries)
                for field,value in [('seed',2**31),('resolution',16),('cpu_threads',64)]:
                    if field=='cpu_threads' and panel.cpu_threads.maximum()==64:continue
                    bad=deepcopy(recipe); bad['sampling'][field]=value
                    if field=='cpu_threads':bad['sampling']['compute_mode']='multicore'
                    with self.subTest(field=field),self.assertRaises(ValueError):panel.apply_sampling_recipe(bad)
                    self.assertEqual(panel.recipe_control_state(),before)
                    self.assertEqual(len(window._history_entries),entries)
                panel.apply_sampling_recipe(recipe)
                self.assertEqual(window.selection,{(1,1),(1,2)})
                self.assertEqual(window.view.mode,'edge')
                self.assertEqual(panel.build_request()['entities'],[[1,1],[1,2]])
                window.undo_edit(); self.assertEqual(panel.recipe_control_state(),before)
                panel.set_busy(True)
                self.assertFalse(panel.save_recipe_button.isEnabled());self.assertFalse(panel.load_recipe_button.isEnabled())
                panel.set_busy(False);window.show_geometry(None)
                self.assertFalse(panel.save_recipe_button.isEnabled());self.assertFalse(panel.load_recipe_button.isEnabled())
            finally:window.close();self.app.processEvents()

    def test_loaded_region_generates_new_dataset_without_overwriting_existing_points(self):
        import test_shell
        with TemporaryDirectory() as directory:
            window=self.window(directory); panel=window.sampling
            try:
                window.boundary_regions=put_region([],window.geometry,'inner',[[1,t] for t in range(1,5)],'#41c4b8')
                window.refresh_boundary_regions()
                original=request(window.geometry,target='region',entities=window.boundary_regions[0]['entities'],
                                 boundary_region=window.boundary_regions[0],label='inner')
                recipe=create_recipe(original)
                panel.apply_sampling_recipe(recipe)
                self.assertEqual(panel.target.currentText(),'Region: inner')
                panel.start(); test_shell.ShellTest.wait_for(self,lambda:panel.process is None)
                self.assertEqual(len(panel.batches),1)
                before=deepcopy(panel.batches[0])
                panel.apply_sampling_recipe(recipe)
                self.assertEqual(panel.batches[0],before)
                panel.start(); test_shell.ShellTest.wait_for(self,lambda:panel.process is None)
                self.assertEqual([item['label'] for item in panel.batches],['inner','inner_1'])
                self.assertEqual(panel.batches[0],before)
                self.assertEqual(panel.batches[1]['boundary_region']['label'],'inner')
            finally:window.close();self.app.processEvents()
