"""Analytic affine geometry, inverse-transpose normals and versioned exports."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import xarray as xr

from test_export import batch
from test_parametrisation import setup as translation_setup
from jorvik.parametrisation import (
    affine_transform, export_configuration, load_setup, preview_payload,
    save_setup, transform_batches, validate_setup,
)


def affine_setup(**changes):
    config = dict(translation_setup(), schema_version=2, translation=[0., 0., 0.],
                  rotation_axis=[0., 0., 1.], rotation_angle=0., angle_units='degrees',
                  scale=[1., 1., 1.], pivot=[0., 0., 0.],
                  order=['scale', 'rotate', 'translate'], frame='world')
    config.update(changes)
    return config


def point_batch(label='boundary'):
    record = batch(label)
    record['coordinates'] = [[2., 1., 0.], [1., 2., 1.], [0., 0., -1.]]
    root_half = 1 / math.sqrt(2)
    record['normals'] = [[root_half, root_half, 0.], [0., 0., 1.], [1., 0., 0.]]
    record['normal_status'] = 'available'
    record['normal_kind'] = 'outward_surface'
    return record


class AffineTransformTest(unittest.TestCase):
    def test_pivot_order_and_inverse_transpose_normals(self):
        original = point_batch()
        untouched = deepcopy(original)
        config = affine_setup(scale=[2., 3., 4.], rotation_angle=90.,
                              pivot=[1., 1., 0.], translation=[3., -2., 1.])
        expected_linear = np.asarray([[0., -3., 0.], [2., 0., 0.], [0., 0., 4.]])
        linear, offset = affine_transform(config)
        np.testing.assert_allclose(linear, expected_linear, atol=1e-14)
        np.testing.assert_allclose(offset, [7., -3., 1.], atol=1e-14)
        for _ in range(2):
            transformed = transform_batches([original], config)[0]
            np.testing.assert_allclose(transformed['coordinates'],
                                       [[4., 1., 1.], [1., -1., 5.], [7., -3., -3.]], atol=1e-14)
            expected_normals = [[-2/math.sqrt(13), 3/math.sqrt(13), 0.],
                                [0., 0., 1.], [0., 1., 0.]]
            np.testing.assert_allclose(transformed['normals'], expected_normals, atol=1e-14)
            np.testing.assert_allclose(np.linalg.norm(transformed['normals'], axis=1), 1.)
            # The transported normal remains perpendicular to the transported tangent.
            tangent = linear @ np.asarray([1., -1., 0.])
            self.assertAlmostEqual(float(np.dot(transformed['normals'][0], tangent)), 0.)
            self.assertEqual(transformed['entity_tags'], original['entity_tags'])
            self.assertEqual(transformed['normal_status'], 'available')
            self.assertEqual(transformed['parametrisation']['operation'], 'affine')
            provenance = transformed['parametrisation']
            self.assertEqual(provenance['schema_version'], 2)
            self.assertEqual(provenance['setup'], config)
            self.assertEqual(provenance['order'], ['scale', 'rotate', 'translate'])
            self.assertEqual(provenance['frame'], 'world')
            self.assertEqual(provenance['domain_consistency'], 'consistent affine transform')
            self.assertTrue(provenance['density_note'])
            np.testing.assert_allclose(np.asarray(provenance['matrix'])[:3, :3], linear)
            np.testing.assert_allclose(np.asarray(provenance['matrix'])[:3, 3], offset)
        self.assertEqual(original, untouched)

    def test_normalized_axis_radians_and_preview_matrix(self):
        record = batch()
        record['coordinates'] = np.eye(3).tolist()
        degrees = affine_setup(rotation_axis=[7., 7., 7.], rotation_angle=120.)
        radians = affine_setup(rotation_axis=[1., 1., 1.], rotation_angle=2*math.pi/3,
                               angle_units='radians')
        expected = [[0., 1., 0.], [0., 0., 1.], [1., 0., 0.]]
        tiny_axis = dict(degrees, rotation_axis=[1e-300, 1e-300, 1e-300])
        huge_axis = dict(degrees, rotation_axis=[1e300, 1e300, 1e300])
        for config in (degrees, radians, tiny_axis, huge_axis):
            points = transform_batches([record], config)[0]['coordinates']
            np.testing.assert_allclose(points, expected, atol=1e-14)
            payload = preview_payload(config)
            matrix = np.asarray(payload['matrix']).reshape((4, 4), order='F')
            transformed = np.c_[record['coordinates'], np.ones(3)] @ matrix.T
            np.testing.assert_allclose(transformed[:, :3], points, atol=1e-14)
            np.testing.assert_array_equal(matrix[3], [0., 0., 0., 1.])
            self.assertEqual(payload['scope'], config['scope'])
        linear, _ = affine_transform(degrees)
        self.assertAlmostEqual(float(np.linalg.det(linear)), 1.)

    def test_identity_translation_region_scope_and_unavailable_normals(self):
        original = point_batch()
        identity = transform_batches([original], affine_setup())[0]
        np.testing.assert_array_equal(identity['coordinates'], original['coordinates'])
        np.testing.assert_allclose(identity['normals'], original['normals'], atol=1e-15)
        moved = transform_batches([original], affine_setup(translation=[2., -3., .5]))[0]
        np.testing.assert_array_equal(moved['coordinates'],
                                      np.asarray(original['coordinates']) + [2., -3., .5])
        np.testing.assert_allclose(moved['normals'], original['normals'], atol=1e-15)
        other = batch('other', 7, 1)
        other.update(normal_status='unavailable', normal_note='Open shell; orientation unknown.')
        originals = deepcopy([original, other])
        config = affine_setup(scope='regions', regions=['boundary'], scale=[2., 1., .5], rotation_angle=45.)
        result = transform_batches([original, other], config)
        self.assertEqual(result[1]['coordinates'], other['coordinates'])
        self.assertEqual(result[1]['entity_tags'], other['entity_tags'])
        self.assertNotIn('normals', result[1])
        self.assertEqual(result[1]['normal_note'], other['normal_note'])
        self.assertFalse(result[1]['parametrisation']['affected'])
        self.assertEqual(result[0]['parametrisation']['domain_consistency'], 'unverified')
        self.assertEqual([original, other], originals)

    def test_oblique_planar_conormals_are_not_guessed_after_anisotropic_scale(self):
        # Original tangent t=(1,1,-2) and co-normal n=(1,-1,0) both lie
        # in the oblique plane x+y+z=0. Ambient inverse-transpose n alone
        # would leave the transformed plane under scale=(2,3,4).
        original = batch()
        original.update(entity_dimension=1, entities=[[1, 6]],
                        coordinates=[[0., 0., 0.], [1., 1., -2.], [2., 2., -4.]],
                        normals=[[1/math.sqrt(2), -1/math.sqrt(2), 0.]] * 3,
                        normal_status='available', normal_kind='outward_conormal')
        before = deepcopy(original)
        anisotropic = affine_setup(scale=[2., 3., 4.])
        for source in (original, {key: value for key, value in original.items() if key != 'normal_kind'}):
            transformed = transform_batches([source], anisotropic)[0]
            np.testing.assert_allclose(transformed['coordinates'],
                                       np.asarray(original['coordinates']) * [2., 3., 4.])
            self.assertNotIn('normals', transformed)
            self.assertNotIn('normal_kind', transformed)
            self.assertEqual(transformed['normal_status'], 'unavailable')
            self.assertTrue(transformed['normal_note'])
        uniform = affine_setup(scale=[2., 2., 2.], rotation_angle=90.)
        transformed = transform_batches([original], uniform)[0]
        np.testing.assert_allclose(transformed['normals'],
                                   [[1/math.sqrt(2), 1/math.sqrt(2), 0.]] * 3, atol=1e-14)
        self.assertEqual(transformed['normal_kind'], 'outward_conormal')
        other = point_batch('other')
        selected = dict(anisotropic, scope='regions', regions=['other'])
        unaffected = transform_batches([original, other], selected)[0]
        self.assertEqual(unaffected['normals'], original['normals'])
        self.assertEqual(unaffected['normal_status'], 'available')
        self.assertEqual(original, before)
        with tempfile.TemporaryDirectory() as directory:
            for extension in ('nc', 'npz'):
                path = Path(directory) / ('conormal.' + extension)
                export_configuration([original], anisotropic, path)
                if extension == 'nc':
                    with xr.open_dataset(path, engine='scipy') as data:
                        manifest = json.loads(data.attrs['metadata_json'])
                        self.assertNotIn('normals_coordinate', data['boundary'].attrs)
                else:
                    with np.load(path, allow_pickle=False) as data:
                        manifest = json.loads(data['_gs_metadata'].item())
                        self.assertFalse(any(key.startswith('_gs_normals_') for key in data.files))
                self.assertNotIn('normals', manifest['datasets'][0])
                self.assertEqual(manifest['datasets'][0]['metadata']['normal_status'], 'unavailable')

    def test_invalid_transforms_and_overflow_do_not_mutate_originals(self):
        invalid = [
            {'scale': [0., 1., 1.]}, {'scale': [-1., 1., 1.]},
            {'scale': [1., float('inf'), 1.]}, {'scale': [1., 1.]},
            {'rotation_axis': [0., 0., 0.]}, {'rotation_axis': [1., 0.]},
            {'rotation_axis': [float('nan'), 0., 1.]},
            {'rotation_angle': float('inf')}, {'rotation_angle': True}, {'angle_units': 'turns'},
            {'scale': [1e-9, 1., 1.]}, {'scale': [1e9, 1., 1.]},
            {'pivot': [0., float('nan'), 0.]}, {'pivot': [0., 0.]},
            {'translation': [float('inf'), 0., 0.]},
            {'order': ['translate', 'rotate', 'scale']}, {'frame': 'local'},
        ]
        for changes in invalid:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_setup(affine_setup(**changes), [point_batch()])
        for field in ('rotation_axis', 'rotation_angle', 'angle_units', 'scale', 'pivot', 'order', 'frame'):
            config = affine_setup(); del config[field]
            with self.subTest(missing=field), self.assertRaises(ValueError):
                validate_setup(config, [point_batch()])
        original = point_batch()
        original['coordinates'][0][0] = 1e308
        before = deepcopy(original)
        # The setup itself is supported, but mapping a very large finite source
        # point must reject arithmetic overflow without changing its input.
        config = affine_setup(scale=[1e8, 1., 1.])
        validate_setup(config, [original])
        with np.errstate(over='ignore', invalid='ignore'), self.assertRaises(ValueError):
            transform_batches([original], config)
        self.assertEqual(original, before)

    def test_setup_and_export_round_trips_with_legacy_compatibility(self):
        configs = [translation_setup(), affine_setup(scale=[2., 1., .5], rotation_angle=35.,
                                                    pivot=[.3, -.4, .2], translation=[1., 2., 3.])]
        batches = [point_batch('wall / 任意'), batch('interior', 7, 1)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for number, config in enumerate(configs):
                setup_path = root / f'setup-{number}.json'
                save_setup(config, setup_path)
                self.assertEqual(load_setup(setup_path), config)
                if number:
                    before_setup = setup_path.read_bytes()
                    with self.assertRaises(ValueError):
                        save_setup(dict(config, scale=[0., 1., 1.]), setup_path)
                    self.assertEqual(setup_path.read_bytes(), before_setup)
                expected = transform_batches(batches, config)
                for extension in ('nc', 'npz'):
                    path = root / f'configuration-{number}.{extension}'
                    export_configuration(batches, config, path)
                    if extension == 'nc':
                        with xr.open_dataset(path, engine='scipy') as data:
                            manifest = json.loads(data.attrs['metadata_json'])
                            for item, record in zip(manifest['datasets'], expected):
                                np.testing.assert_allclose(data[item['array']], record['coordinates'], atol=1e-14)
                                if 'normals' in item:
                                    np.testing.assert_allclose(data[item['normals']], record['normals'], atol=1e-14)
                            np.testing.assert_array_equal(data['_gs_translation'], config['translation'])
                            if number:
                                self.assertEqual(data['_gs_transform_matrix'].dims,
                                                 ('_gs_matrix_row', '_gs_matrix_column'))
                                matrix = data['_gs_transform_matrix'].values.copy()
                    else:
                        with np.load(path, allow_pickle=False) as data:
                            manifest = json.loads(data['_gs_metadata'].item())
                            for item, record in zip(manifest['datasets'], expected):
                                np.testing.assert_allclose(data[item['array']], record['coordinates'], atol=1e-14)
                                if 'normals' in item:
                                    np.testing.assert_allclose(data[item['normals']], record['normals'], atol=1e-14)
                            np.testing.assert_array_equal(data['_gs_translation'], config['translation'])
                            if number: matrix = data['_gs_transform_matrix'].copy()
                    self.assertEqual(manifest['parametrisation'], config)
                    self.assertEqual([item['label'] for item in manifest['datasets']], ['wall / 任意', 'interior'])
                    if number:
                        linear, offset = affine_transform(config)
                        np.testing.assert_allclose(matrix[:3, :3], linear)
                        np.testing.assert_allclose(matrix[:3, 3], offset)
                        np.testing.assert_array_equal(matrix[3], [0., 0., 0., 1.])
                    before = path.read_bytes()
                    writer = 'xarray.Dataset.to_netcdf' if extension == 'nc' else 'numpy.savez_compressed'
                    with patch(writer, side_effect=OSError('Disk full')):
                        with self.assertRaises(OSError): export_configuration(batches, config, path)
                    self.assertEqual(path.read_bytes(), before)
