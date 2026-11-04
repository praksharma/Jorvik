"""Parameter designs persist independently of their selected spatial configuration."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import xarray as xr

from jorvik.geometry import load_geometry
from jorvik.parameter_design import generate_configurations
from jorvik.parametrisation import export_configuration, load_setup, save_setup, validate_setup
from jorvik.project import load_project, save_project
from test_affine_transforms import affine_setup
from test_export import batch
from test_parametrisation import setup as translation_setup
from test_project import FIXTURE, state


def design():
    return {'schema_version': 1, 'method': 'paired', 'seed': 17, 'count': 2,
            'parameters': [
                {'name': 'μ / angle', 'target': 'rotation_angle', 'mode': 'values',
                 'values': [0., 90.], 'reference': 0., 'unit': 'degrees'},
                {'name': 'shift amount', 'target': 'translation_x', 'mode': 'values',
                 'values': [0., 2.], 'reference': 0., 'unit': 'source'}]}


class DesignIntegrationTest(unittest.TestCase):
    def test_design_round_trip_json_and_project_without_generated_clouds(self):
        geometry = load_geometry(FIXTURE)
        base = affine_setup(source_hash=geometry['source_hash'], design=design())
        original = deepcopy(base)
        saved_state = state(); saved_state['selected_area'] = None
        saved_state['parametrisation'] = base
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            setup_path = directory / 'design.json'
            save_setup(base, setup_path)
            self.assertEqual(load_setup(setup_path), original)
            project_path = directory / 'design.jorvik'
            save_project(project_path, geometry, [], saved_state)
            manifest, source = load_project(project_path)
            self.assertEqual(manifest['state']['parametrisation'], original)
            self.assertEqual(manifest['areas'], [])
            self.assertEqual(source, FIXTURE.read_bytes())
            # Selection can be regenerated from stored scalar metadata alone.
            selected = generate_configurations(manifest['state']['parametrisation'], design())[1]
            self.assertEqual(selected['id'], 'configuration_000002')
            self.assertNotIn('design', selected['setup'])
            self.assertEqual(selected['setup']['rotation_angle'], 90.)
            self.assertEqual(selected['setup']['translation'], [2., 0., 0.])
            self.assertEqual(base, original)
            before = project_path.read_bytes()
            for problem in ('binding', 'source'):
                broken = deepcopy(saved_state)
                if problem == 'binding':
                    broken['parametrisation']['design']['parameters'][1]['target'] = 'rotation_angle'
                else:
                    broken['parametrisation']['source_hash'] = 'another-geometry'
                with self.assertRaises(ValueError): save_project(project_path, geometry, [], broken)
                self.assertEqual(project_path.read_bytes(), before)

    def test_selected_configuration_export_keeps_named_values_and_unequal_counts(self):
        base = affine_setup(design=design())
        records = generate_configurations(base, design())
        selected = deepcopy(records[1]['setup'])
        selected['design'] = deepcopy(base['design'])
        clouds = [batch('wall / 面', 3), batch('interior', 7, 1)]
        originals = deepcopy(clouds)
        names = list(records[1]['parameters'])
        values = list(records[1]['parameters'].values())
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ('nc', 'npz'):
                path = Path(directory) / ('selected.' + suffix)
                export_configuration(clouds, selected, path)
                if suffix == 'nc':
                    with xr.open_dataset(path, engine='scipy') as data:
                        manifest = json.loads(data.attrs['metadata_json'])
                        arrays = {name: var.values.copy() for name, var in data.variables.items()}
                else:
                    with np.load(path, allow_pickle=False) as data:
                        manifest = json.loads(data['_gs_metadata'].item())
                        arrays = {name: data[name].copy() for name in data.files}
                self.assertEqual(arrays['_gs_design_parameter'].tolist(), names)
                np.testing.assert_array_equal(arrays['_gs_design_values'], values)
                self.assertEqual(manifest['parametrisation']['design_configuration']['id'], records[1]['id'])
                self.assertEqual(manifest['parametrisation']['design_configuration']['parameters'], records[1]['parameters'])
                self.assertEqual(manifest['parametrisation']['design_configuration']['units'],
                                 {'μ / angle': 'degrees', 'shift amount': 'source'})
                self.assertEqual(manifest['configuration_count'], 1)
                self.assertEqual(manifest['parametrisation']['design'], design())
                for saved, original in zip(manifest['datasets'], clouds):
                    expected = np.asarray(original['coordinates'])
                    expected = np.column_stack((2 - expected[:, 1], expected[:, 0], expected[:, 2]))
                    self.assertEqual(arrays[saved['array']].shape, (original['count'], 3))
                    np.testing.assert_allclose(arrays[saved['array']], expected, atol=1e-14)
                    np.testing.assert_array_equal(arrays[saved['entity_tags']], original['entity_tags'])
                    provenance = saved['metadata']['parametrisation']
                    self.assertNotIn('design', provenance['setup'])
                    self.assertEqual(provenance['setup']['design_configuration'], selected['design_configuration'])
            self.assertEqual(clouds, originals)
            self.assertEqual(base['design'], design())

    def test_invalid_design_or_changed_source_preserves_existing_destination(self):
        base = affine_setup(design=design())
        invalid = deepcopy(base)
        invalid['design']['parameters'][1]['target'] = 'rotation_angle'  # Ambiguous binding.
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            setup_path = directory / 'setup.json'
            save_setup(base, setup_path)
            before = setup_path.read_bytes()
            with self.assertRaises(ValueError): save_setup(invalid, setup_path)
            self.assertEqual(setup_path.read_bytes(), before)
            for suffix in ('nc', 'npz'):
                path = directory / ('existing.' + suffix); path.write_bytes(b'previous export')
                for config in (invalid, dict(base, source_hash='other-source')):
                    with self.assertRaises(ValueError): export_configuration([batch()], config, path)
                    self.assertEqual(path.read_bytes(), b'previous export')
            # A failed writer also leaves a valid selected export intact.
            selected = generate_configurations(base, design())[0]['setup']
            output = directory / 'selected.npz'
            export_configuration([batch()], selected, output)
            before = output.read_bytes()
            with patch('numpy.savez_compressed', side_effect=OSError('Disk full')):
                with self.assertRaises(OSError): export_configuration([batch()], selected, output)
            self.assertEqual(output.read_bytes(), before)

    def test_legacy_setups_and_single_configuration_exports_remain_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            for version, config in [(1, translation_setup()), (2, affine_setup())]:
                path = directory / f'legacy-{version}.json'
                save_setup(config, path)
                self.assertEqual(load_setup(path), config)
                validate_setup(config, [batch()])
                output = directory / f'legacy-{version}.npz'
                export_configuration([batch()], config, output)
                with np.load(output, allow_pickle=False) as arrays:
                    metadata = json.loads(arrays['_gs_metadata'].item())
                    self.assertEqual(metadata['parametrisation'], config)
                    self.assertNotIn('_gs_design_values', arrays.files)
                    self.assertNotIn('design_configuration', metadata['parametrisation'])
