"""Bounded shard export, cancellation and explicit incomplete-run records."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import xarray as xr

from jorvik.parameter_design import generate_configurations
from jorvik.parametrisation import export_configuration, transform_batches
from jorvik.sweep_export import export_sweep
from test_affine_transforms import affine_setup
from test_export import batch


def design():
    return {'schema_version': 1, 'method': 'paired', 'seed': 42, 'count': 3,
            'parameters': [
                {'name': 'angle / μ', 'target': 'rotation_angle', 'mode': 'values',
                 'values': [0., 45., 90.], 'reference': 0., 'unit': 'degrees'},
                {'name': 'shift', 'target': 'translation_x', 'mode': 'values',
                 'values': [0., 1., 2.], 'reference': 0., 'unit': 'source'}]}


def inputs():
    clouds = [batch('wall / 面', 3), batch('interior', 7, 1)]
    clouds[0].update(normals=[[0., 0., 1.]] * 3,
                     normal_status='available', normal_kind='outward_surface')
    base = affine_setup(scope='regions', regions=['wall / 面'], design=design())
    configurations = generate_configurations(base, base['design'])
    return clouds, base, configurations


def read_json(path):
    return json.loads(path.read_text())


def results(path):
    return [json.loads(line) for line in (path / 'results.jsonl').read_text().splitlines()]


class SweepExportTest(unittest.TestCase):
    def test_nc_and_npz_round_trip_unequal_labels_normals_and_scope(self):
        clouds, base, configurations = inputs()
        original = deepcopy((clouds, base, configurations))
        with TemporaryDirectory() as directory:
            for suffix in ('nc', 'npz'):
                path = Path(directory) / suffix
                progress = []
                manifest = export_sweep(clouds, base, configurations, path,
                                        file_format=suffix, progress=progress.append)
                self.assertEqual(read_json(path / 'manifest.json'), manifest)
                self.assertEqual(manifest['status'], 'complete')
                self.assertTrue(manifest['all_outputs_complete'])
                self.assertEqual((manifest['attempted_count'], manifest['succeeded_count'],
                                  manifest['failed_count'], manifest['pending_count']), (3, 3, 0, 0))
                self.assertEqual(manifest['base_setup'], base)
                self.assertEqual(manifest['source_hash'], base['source_hash'])
                self.assertEqual(manifest['datasets'][0]['label'], clouds[0]['label'])
                self.assertNotIn('coordinates', manifest['datasets'][0])
                self.assertGreaterEqual(manifest['elapsed_seconds'], 0)
                self.assertEqual(progress[0]['attempted_count'], 0)
                self.assertEqual(progress[-1]['status'], 'complete')
                self.assertTrue(all('base_setup' not in item and 'datasets' not in item for item in progress))
                plan = read_json(path / 'configurations.json')['configurations']
                self.assertEqual([record['id'] for record in plan], [record['id'] for record in configurations])
                self.assertTrue(all('design' not in record['setup'] for record in plan))
                journal = results(path)
                self.assertEqual([record['status'] for record in journal], ['complete'] * 3)
                for configuration, record in zip(configurations, journal):
                    shard = path / record['filename']
                    if suffix == 'nc':
                        with xr.open_dataset(shard, engine='scipy') as dataset:
                            metadata = json.loads(dataset.attrs['metadata_json'])
                            arrays = {name: var.values.copy() for name, var in dataset.variables.items()}
                    else:
                        with np.load(shard, allow_pickle=False) as saved:
                            metadata = json.loads(saved['_gs_metadata'].item())
                            arrays = {name: saved[name].copy() for name in saved.files}
                    expected = transform_batches(clouds, configuration['setup'])
                    self.assertEqual(metadata['parametrisation']['design_configuration']['id'], configuration['id'])
                    self.assertEqual(metadata['parametrisation']['design_configuration']['parameters'], configuration['parameters'])
                    self.assertNotIn('design', metadata['parametrisation'])
                    for saved, data in zip(metadata['datasets'], expected):
                        np.testing.assert_allclose(arrays[saved['array']], data['coordinates'])
                        np.testing.assert_array_equal(arrays[saved['entity_tags']], data['entity_tags'])
                        self.assertEqual(arrays[saved['array']].shape, (data['count'], 3))
                        if 'normals' in data:
                            np.testing.assert_allclose(arrays[saved['normals']], data['normals'])
                    self.assertEqual(record['point_counts'], {'wall / 面': 3, 'interior': 7})
                self.assertFalse(any(path.glob('.*.tmp')))
        self.assertEqual((clouds, base, configurations), original)

    def test_cancellation_finishes_one_file_and_marks_remaining_pending(self):
        clouds, base, configurations = inputs()
        with TemporaryDirectory() as directory:
            stopped = False
            def progress(event):
                nonlocal stopped
                if event['attempted_count'] == 1: stopped = True
            path = Path(directory) / 'cancelled'
            manifest = export_sweep(clouds, base, configurations, path, file_format='npz',
                                    cancelled=lambda: stopped, progress=progress)
            self.assertEqual(manifest['status'], 'cancelled')
            self.assertFalse(manifest['all_outputs_complete'])
            self.assertEqual(manifest['succeeded_count'], 1)
            self.assertEqual(manifest['pending_count'], 2)
            self.assertEqual(len(list(path.glob('*.npz'))), 1)
            self.assertEqual(len(results(path)), 1)
            empty = Path(directory) / 'pre-cancelled'
            manifest = export_sweep(clouds, base, configurations, empty, cancelled=lambda: True)
            self.assertEqual(manifest['status'], 'cancelled')
            self.assertEqual(manifest['attempted_count'], 0)
            self.assertEqual(results(empty), [])

    def test_fail_fast_and_continue_policy_keep_good_shards_and_error_records(self):
        clouds, base, configurations = inputs()
        with TemporaryDirectory() as directory:
            for fail_fast, expected in [(True, (2, 1, 1, 1, 'failed')),
                                        (False, (3, 2, 1, 0, 'complete_with_errors'))]:
                path = Path(directory) / str(fail_fast)
                calls = []
                def export(originals, setup, destination):
                    self.assertIs(originals, clouds)
                    self.assertNotIn('design', setup)
                    calls.append(setup['design_configuration']['id'])
                    if calls[-1] == 'configuration_000002': raise OSError('Disk quota reached')
                    return export_configuration(originals, setup, destination)
                with patch('jorvik.sweep_export.export_configuration', side_effect=export):
                    manifest = export_sweep(clouds, base, configurations, path,
                                            file_format='npz', fail_fast=fail_fast)
                self.assertEqual(tuple(manifest[key] for key in ('attempted_count', 'succeeded_count',
                                 'failed_count', 'pending_count', 'status')), expected)
                self.assertFalse(manifest['all_outputs_complete'])
                journal = results(path)
                self.assertEqual(journal[1]['status'], 'failed')
                self.assertIn('Disk quota', journal[1]['error'])
                self.assertNotIn('filename', journal[1])
                self.assertTrue((path / 'configuration_000001.npz').is_file())
                self.assertFalse((path / 'configuration_000002.npz').exists())
                self.assertEqual((path / 'configuration_000003.npz').exists(), not fail_fast)

    def test_atomic_writer_failure_leaves_no_failed_shard_or_temporary_file(self):
        clouds, base, configurations = inputs()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'disk-full'
            with patch('numpy.savez_compressed', side_effect=OSError('Disk full')):
                manifest = export_sweep(clouds, base, configurations, path,
                                        file_format='npz', fail_fast=False)
            self.assertEqual(manifest['status'], 'complete_with_errors')
            self.assertEqual(manifest['failed_count'], 3)
            self.assertEqual(list(path.glob('*.npz')), [])
            self.assertFalse(any(path.glob('.*.tmp')))
            self.assertTrue(all(record['status'] == 'failed' for record in results(path)))

    def test_existing_destination_and_invalid_inputs_are_untouched(self):
        clouds, base, configurations = inputs()
        with TemporaryDirectory() as directory:
            directory = Path(directory)
            existing = directory / 'existing'; existing.mkdir()
            sentinel = existing / 'keep.bin'; sentinel.write_bytes(b'original')
            with self.assertRaises(FileExistsError): export_sweep(clouds, base, configurations, existing)
            self.assertEqual(list(existing.iterdir()), [sentinel]); self.assertEqual(sentinel.read_bytes(), b'original')
            bad_records = []
            bad = deepcopy(configurations); bad[0]['id'] = '../escape'; bad_records.append(bad)
            bad = deepcopy(configurations); bad.append(bad[0]); bad_records.append(bad)
            bad = deepcopy(configurations); bad[0]['parameters']['shift'] = 999.; bad_records.append(bad)
            bad = deepcopy(configurations); bad[0]['setup']['source_hash'] = 'changed'; bad_records.append(bad)
            bad = deepcopy(configurations); bad[0]['setup']['scale'][0] = 0.; bad_records.append(bad)
            bad_records.append([])
            for index, records in enumerate(bad_records):
                path = directory / f'bad-{index}'
                with self.subTest(index=index), self.assertRaises(ValueError): export_sweep(clouds, base, records, path)
                self.assertFalse(path.exists())
            path = directory / 'wrong-format'
            with self.assertRaises(ValueError): export_sweep(clouds, base, configurations, path, file_format='pickle')
            self.assertFalse(path.exists())
            with self.assertRaises(ValueError): export_sweep(clouds, dict(base, source_hash='changed'), configurations, path)
            self.assertFalse(path.exists())

    def test_base_frame_provenance_and_unbound_values_cannot_drift(self):
        clouds, base, configurations = inputs()
        with TemporaryDirectory() as directory:
            changes = [lambda setup: setup.update(scope='domain'),
                       lambda setup: setup['pivot'].__setitem__(0, 1.),
                       lambda setup: setup['rotation_axis'].__setitem__(0, 1.),
                       lambda setup: setup['translation'].__setitem__(1, 99.),
                       lambda setup: setup['scale'].__setitem__(2, 2.),
                       lambda setup: setup['design_configuration'].update(method='random'),
                       lambda setup: setup['design_configuration'].update(seed=99)]
            for index, change in enumerate(changes):
                altered = deepcopy(configurations); change(altered[0]['setup'])
                path = Path(directory) / str(index)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    export_sweep(clouds, base, altered, path)
                self.assertFalse(path.exists())
            # A failed attempt exposes the actionable error through progress, too.
            events = []
            with patch('jorvik.sweep_export.export_configuration', side_effect=OSError('Quota exhausted')):
                export_sweep(clouds, base, configurations, Path(directory)/'error', progress=events.append)
            self.assertTrue(any(event['current_error'] == 'Quota exhausted' for event in events))

    def test_large_value_definitions_are_not_revalidated_for_every_row(self):
        from jorvik.parametrisation import validate_setup
        clouds = [batch()]
        definition = {'schema_version': 1, 'method': 'cartesian', 'seed': 42, 'count': 16,
                      'parameters': [{'name': 'offset', 'target': 'translation_x', 'mode': 'values',
                                      'values': list(range(40)), 'reference': 0., 'unit': 'source'}]}
        base = affine_setup(design=definition)
        configurations = generate_configurations(base, definition)
        seen_designs = []
        def validate(setup, batches=None):
            if 'design' in setup: seen_designs.append(setup)
            return validate_setup(setup, batches)
        def export(originals, setup, destination):
            self.assertIs(originals, clouds)
            self.assertNotIn('design', setup)
            destination.write_bytes(b'mock shard')
        with TemporaryDirectory() as directory, patch('jorvik.sweep_export.validate_setup', side_effect=validate), patch(
                'jorvik.sweep_export.export_configuration', side_effect=export):
            manifest = export_sweep(clouds, base, configurations, Path(directory)/'bounded')
        self.assertEqual(manifest['succeeded_count'], 40)
        self.assertEqual(len(seen_designs), 1)

    def test_callback_failure_marks_incomplete_run_without_losing_successful_data(self):
        clouds, base, configurations = inputs()
        with TemporaryDirectory() as directory:
            path = Path(directory) / 'callback-failed'
            def progress(event):
                if event['attempted_count'] == 1: raise RuntimeError('Observer failed')
            with self.assertRaisesRegex(RuntimeError, 'Observer failed'):
                export_sweep(clouds, base, configurations, path, file_format='npz', progress=progress)
            manifest = read_json(path / 'manifest.json')
            self.assertEqual(manifest['status'], 'failed')
            self.assertFalse(manifest['all_outputs_complete'])
            self.assertTrue((path / 'configuration_000001.npz').exists())
            self.assertEqual(results(path)[0]['status'], 'complete')
