"""Tests for xarray.DataTree construction and consolidated single-file sweep bundles."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import xarray as xr

from jorvik.export import export_samples, to_xarray
from jorvik.parameter_design import generate_configurations
from jorvik.sweep_container import export_consolidated_sweep, load_consolidated_sweep, to_datatree
from jorvik.sweep_export import export_sweep
from jorvik.sweep_results import iter_sweep_datasets
from test_affine_transforms import affine_setup
from test_export import batch


class SweepDataTreeTest(unittest.TestCase):
    def _sample_batches(self):
        b1 = batch('inlet', 5, 1)
        b1['weights'] = [0.2] * 5
        b1['domain_measure'] = 1.0
        b1['weight_status'] = 'available'
        b1['boundary_distances'] = [0.1 * i for i in range(5)]
        b1['distance_status'] = 'available'

        b2 = batch('interior patch', 8, 6)
        b2['weights'] = [0.125] * 8
        b2['domain_measure'] = 1.0
        b2['weight_status'] = 'available'
        return [b1, b2]

    def _design(self):
        return {
            'schema_version': 1,
            'method': 'cartesian',
            'seed': 42,
            'count': 3,
            'parameters': [{
                'name': 'shift_x',
                'target': 'translation_x',
                'mode': 'values',
                'values': [0.0, 0.5, 1.0],
                'reference': 0.0,
                'unit': 'source',
            }],
        }

    def test_uniform_sweep_datatree_and_consolidated_bundles_nc_and_npz(self):
        batches = self._sample_batches()
        design = self._design()
        base = affine_setup(source_hash=batches[0]['source_hash'], design=design)
        records = generate_configurations(base, design)

        with tempfile.TemporaryDirectory() as directory:
            for fmt in ('nc', 'npz'):
                sweep_dir = Path(directory) / f'sweep_{fmt}'
                report = export_sweep(batches, base, records, sweep_dir, file_format=fmt, consolidated=True)
                self.assertEqual(report['status'], 'complete')
                bundle_path = sweep_dir / report['consolidated_bundle']
                self.assertTrue(bundle_path.is_file())

                tree = to_datatree(sweep_dir)
                self.assertIsInstance(tree, xr.DataTree)
                self.assertEqual(tree.attrs['uniform_counts'], 1)
                np.testing.assert_allclose(tree['parameters'].ds['shift_x'].values, [0.0, 0.5, 1.0])

                inlet_ds = tree['regions/inlet'].ds
                self.assertEqual(inlet_ds['coordinates'].shape, (3, 5, 3))
                self.assertIn('weights', inlet_ds)
                self.assertIn('boundary_distances', inlet_ds)

                loaded_tree = load_consolidated_sweep(bundle_path)
                self.assertIsInstance(loaded_tree, xr.DataTree)
                np.testing.assert_allclose(loaded_tree['parameters'].ds['shift_x'].values, [0.0, 0.5, 1.0])
                np.testing.assert_allclose(
                    loaded_tree['regions/inlet'].ds['coordinates'].values,
                    inlet_ds['coordinates'].values,
                )
                np.testing.assert_allclose(
                    loaded_tree['regions/inlet'].ds['weights'].values,
                    inlet_ds['weights'].values,
                )
                np.testing.assert_allclose(
                    loaded_tree['regions/inlet'].ds['boundary_distances'].values,
                    inlet_ds['boundary_distances'].values,
                )

    def test_ragged_point_count_sweep_datatree_and_consolidated_round_trip(self):
        batches = self._sample_batches()
        design = self._design()
        base = affine_setup(source_hash=batches[0]['source_hash'], design=design)
        records = generate_configurations(base, design)[:2]

        with tempfile.TemporaryDirectory() as directory:
            sweep_dir = Path(directory) / 'ragged_sweep'
            export_sweep(batches, base, records, sweep_dir, file_format='nc')

            # Simulate a dimensional CAD regeneration shard with a 7-point inlet batch
            from jorvik.parametrisation import export_configuration
            from jorvik.sweep_integrity import file_integrity
            for rec in records:
                rec['setup']['geo_parameters'] = [{'name': 'r_in', 'default': 0.4}]
                rec['setup']['geo_overrides'] = {'r_in': 0.5}
            base['geo_parameters'] = [{'name': 'r_in', 'default': 0.4}]
            plan_path = sweep_dir / 'configurations.json'
            plan_data = json.loads(plan_path.read_text(encoding='utf-8'))
            for rec in plan_data['configurations']:
                rec['setup']['geo_parameters'] = [{'name': 'r_in', 'default': 0.4}]
                rec['setup']['geo_overrides'] = {'r_in': 0.5}
            plan_path.write_text(json.dumps(plan_data), encoding='utf-8')
            manifest_path = sweep_dir / 'manifest.json'
            manifest_data = json.loads(manifest_path.read_text(encoding='utf-8'))
            manifest_data['base_setup']['geo_parameters'] = [{'name': 'r_in', 'default': 0.4}]
            manifest_data['integrity']['plan'] = file_integrity(plan_path)
            manifest_path.write_text(json.dumps(manifest_data), encoding='utf-8')

            shard_1 = sweep_dir / 'configuration_000001.nc'
            ds1 = to_xarray(batches)
            meta1 = json.loads(ds1.attrs['metadata_json'])
            meta1['parametrisation'] = records[0]['setup']
            ds1.attrs['metadata_json'] = json.dumps(meta1)
            params1 = records[0]['setup']['design_configuration']['parameters']
            names1 = list(params1)
            ds1 = ds1.assign_coords({
                '_gs_design_parameter': names1,
                '_gs_design_values': ('_gs_design_parameter', [float(params1[k]) for k in names1]),
                '_gs_design_units': ('_gs_design_parameter', [records[0]['setup']['design_configuration']['units'][k] for k in names1]),
            })
            shard_1.unlink()
            ds1.to_netcdf(shard_1, engine='scipy', format='NETCDF3_64BIT')
            ds1.close()

            longer_inlet = deepcopy(batches[0])
            longer_inlet['count'] = 7
            longer_inlet['requested_count'] = 7
            longer_inlet['coordinates'] = [[i / 10.0, 0.0, 0.0] for i in range(7)]
            longer_inlet['entity_tags'] = [1] * 7
            longer_inlet['weights'] = [1.0 / 7.0] * 7
            longer_inlet['boundary_distances'] = [0.1 * i for i in range(7)]
            shard_2 = sweep_dir / 'configuration_000002.nc'
            shard_2.unlink()
            # Write directly with export_samples using the updated parametrisation metadata
            ds = to_xarray([longer_inlet, batches[1]])
            meta = json.loads(ds.attrs['metadata_json'])
            meta['parametrisation'] = records[1]['setup']
            ds.attrs['metadata_json'] = json.dumps(meta)
            params = records[1]['setup']['design_configuration']['parameters']
            names = list(params)
            ds = ds.assign_coords({
                '_gs_design_parameter': names,
                '_gs_design_values': ('_gs_design_parameter', [float(params[k]) for k in names]),
                '_gs_design_units': ('_gs_design_parameter', [records[1]['setup']['design_configuration']['units'][k] for k in names]),
            })
            ds.to_netcdf(shard_2, engine='scipy', format='NETCDF3_64BIT')
            ds.close()
            results_path = sweep_dir / 'results.jsonl'
            lines = [json.loads(line) for line in results_path.read_text(encoding='utf-8').splitlines() if line.strip()]
            for entry in lines:
                if entry['id'] == 'configuration_000001':
                    entry['integrity'] = file_integrity(shard_1)
                if entry['id'] == 'configuration_000002':
                    entry['point_counts']['inlet'] = 7
                    entry['integrity'] = file_integrity(shard_2)
            results_path.write_text(''.join(json.dumps(e) + '\n' for e in lines), encoding='utf-8')

            tree = to_datatree(sweep_dir)
            self.assertEqual(tree.attrs['uniform_counts'], 0)
            self.assertEqual(tree['configurations/configuration_000001/regions/inlet'].ds['coordinates'].shape, (5, 3))
            self.assertEqual(tree['configurations/configuration_000002/regions/inlet'].ds['coordinates'].shape, (7, 3))

            for ext in ('nc', 'npz'):
                bundle = export_consolidated_sweep(sweep_dir, Path(directory) / f'ragged_bundle.{ext}')
                restored = load_consolidated_sweep(bundle)
                self.assertEqual(restored.attrs['uniform_counts'], 0)
                self.assertEqual(
                    restored['configurations/configuration_000001/regions/inlet'].ds['coordinates'].shape,
                    (5, 3),
                )
                self.assertEqual(
                    restored['configurations/configuration_000002/regions/inlet'].ds['coordinates'].shape,
                    (7, 3),
                )


if __name__ == '__main__':
    unittest.main()
