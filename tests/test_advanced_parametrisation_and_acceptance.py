"""Tests for per-region multi-transform bindings, domain consistency checks, batch sampling reuse,
bounded multi-worker sweep concurrency, float32/float64 precision, cross-platform graphics config,
and the end-to-end parametric PINN workflow.
"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import xarray as xr

from jorvik.export import export_samples
from jorvik.geometry import load_geometry
from jorvik.graphics import configure_graphics, nvidia_wayland_available
from jorvik.parameter_design import generate_configurations, validate_design
from jorvik.parametrisation import transform_batches, verify_domain_consistency
from jorvik.project import load_project, save_project
from jorvik.sampling import sample_geometry, sample_geometry_batch
from jorvik.sweep_container import load_consolidated_sweep
from jorvik.sweep_export import export_sweep
from jorvik.sweep_results import iter_sweep_datasets
from test_affine_transforms import affine_setup
from test_project import state

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'examples/geometries'


class AdvancedParametrisationAndAcceptanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.annulus = load_geometry(FIXTURES / 'cfdbench/annulus_unstructured.geo')

    def _requests(self):
        base = {k: deepcopy(self.annulus[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
        return [
            {**base, 'target': 'selection', 'entities': [[1, 1], [1, 2], [1, 3], [1, 4]],
             'method': 'sobol', 'count': 32, 'seed': 42, 'label': 'inner', 'resolution': 16},
            {**base, 'target': 'selection', 'entities': [[1, 5], [1, 6], [1, 7], [1, 8]],
             'method': 'sobol', 'count': 32, 'seed': 42, 'label': 'outer', 'resolution': 16},
            {**base, 'target': 'interior', 'entities': [],
             'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'interior', 'resolution': 16},
        ]

    def test_batch_sampling_reuses_prepared_geometry_identically(self):
        reqs = self._requests()
        batch_results = sample_geometry_batch(reqs)
        single_results = [sample_geometry(r) for r in reqs]
        self.assertEqual(len(batch_results), 3)
        for b_res, s_res in zip(batch_results, single_results):
            np.testing.assert_allclose(b_res['coordinates'], s_res['coordinates'])
            np.testing.assert_array_equal(b_res['entity_tags'], s_res['entity_tags'])
            np.testing.assert_allclose(b_res['weights'], s_res['weights'])

    def test_per_region_multi_transforms_and_domain_consistency_checks(self):
        batches = sample_geometry_batch(self._requests())
        # Add an overlapping boundary dataset sharing Edge 1 with 'inner'
        overlap = deepcopy(batches[0])
        overlap['label'] = 'inner_overlap'
        all_batches = [*batches, overlap]

        # 1. Consistent whole-domain transform
        whole_setup = affine_setup(source_hash=self.annulus['source_hash'], scale=[1.5, 1.5, 1.0], translation=[0.5, 0.0, 0.0])
        whole_check = verify_domain_consistency(all_batches, whole_setup)
        self.assertEqual(whole_check['status'], 'consistent')
        self.assertEqual(whole_check['shared_boundary_status'], 'consistent')

        # 2. Multiple sequential transformations on 'inner' + shared-entity conflict with 'inner_overlap'
        multi_setup = affine_setup(source_hash=self.annulus['source_hash'])
        multi_setup['region_transforms'] = [
            {
                'regions': ['inner'],
                'translation': [0.05, 0.0, 0.0],
                'rotation_axis': [0.0, 0.0, 1.0],
                'rotation_angle': 15.0,
                'angle_units': 'degrees',
                'scale': [1.0, 1.0, 1.0],
                'pivot': [0.0, 0.0, 0.0],
                'order': ['scale', 'rotate', 'translate'],
                'frame': 'world',
            },
            {
                'regions': ['inner'],
                'translation': [0.05, 0.0, 0.0],
                'rotation_axis': [0.0, 0.0, 1.0],
                'rotation_angle': 0.0,
                'angle_units': 'degrees',
                'scale': [1.0, 1.0, 1.0],
                'pivot': [0.0, 0.0, 0.0],
                'order': ['scale', 'rotate', 'translate'],
                'frame': 'world',
            },
        ]
        transformed = transform_batches(all_batches, multi_setup)
        self.assertEqual(transformed[0]['parametrisation']['applied_transform_steps'], 3)
        check = transformed[0]['parametrisation']['consistency_check']
        self.assertEqual(check['status'], 'unverified')
        self.assertEqual(check['shared_boundary_status'], 'inconsistent_shared_entity')

        # 3. Outside-domain detection when 'inner' is shifted outside the interior annulus bounding box [-1, 1]
        outside_setup = affine_setup(source_hash=self.annulus['source_hash'])
        outside_setup['region_transforms'] = [{
            'regions': ['inner'],
            'translation': [5.0, 0.0, 0.0],
            'rotation_axis': [0.0, 0.0, 1.0],
            'rotation_angle': 0.0,
            'angle_units': 'degrees',
            'scale': [1.0, 1.0, 1.0],
            'pivot': [0.0, 0.0, 0.0],
            'order': ['scale', 'rotate', 'translate'],
            'frame': 'world',
        }]
        out_check = verify_domain_consistency(batches, outside_setup)
        self.assertEqual(out_check['status'], 'outside_domain')
        self.assertEqual(out_check['outside_regions'], ['inner'])

        # 4. Region-scoped parameter design targets ('region:inner:translation_x')
        design = {
            'schema_version': 1,
            'method': 'cartesian',
            'seed': 42,
            'count': 2,
            'parameters': [{
                'name': 'inner_dx',
                'target': 'region:inner:translation_x',
                'mode': 'values',
                'values': [0.1, 0.2],
                'reference': 0.0,
                'unit': 'source',
            }],
        }
        base = affine_setup(source_hash=self.annulus['source_hash'], design=design)
        validate_design(design, base)
        configs = generate_configurations(base, design)
        self.assertEqual(len(configs), 2)
        mapped = transform_batches(batches, configs[1]['setup'])
        np.testing.assert_allclose(
            np.asarray(mapped[0]['coordinates'])[:, 0],
            np.asarray(batches[0]['coordinates'])[:, 0] + 0.2,
            atol=1e-7,
        )
        np.testing.assert_allclose(mapped[1]['coordinates'], batches[1]['coordinates'])

    def test_float32_precision_within_file_cancellation_and_bounded_multi_worker_sweep(self):
        batches = sample_geometry_batch(self._requests())
        design = {
            'schema_version': 1,
            'method': 'cartesian',
            'seed': 42,
            'count': 4,
            'parameters': [{
                'name': 'shift_x',
                'target': 'translation_x',
                'mode': 'values',
                'values': [0.0, 0.25, 0.5, 0.75],
                'reference': 0.0,
                'unit': 'source',
            }],
        }
        base = affine_setup(source_hash=self.annulus['source_hash'], design=design)
        records = generate_configurations(base, design)

        with tempfile.TemporaryDirectory() as directory:
            # 1. Float32 export_samples and within-file cancellation checkpoint
            nc_32 = export_samples(batches, Path(directory) / 'f32.nc', float_dtype='float32')
            with xr.open_dataset(nc_32, engine='scipy') as ds:
                self.assertEqual(ds['inner'].dtype, np.float32)
                self.assertEqual(ds.coords['_gs_weights_0'].dtype, np.float32)

            with self.assertRaises(InterruptedError):
                export_samples(batches, Path(directory) / 'cancelled.nc', cancelled=lambda: True)
            self.assertFalse((Path(directory) / 'cancelled.nc').exists())

            # 2. Multi-worker sweep export (max_workers=2) with float32 and consolidated bundle
            sweep_dir = Path(directory) / 'parallel_sweep'
            manifest = export_sweep(
                batches, base, records, sweep_dir,
                file_format='nc', consolidated=True, max_workers=2, float_dtype='float32',
            )
            self.assertEqual(manifest['status'], 'complete')
            self.assertEqual(manifest['succeeded_count'], 4)
            loaded_ids = [item['id'] for item in iter_sweep_datasets(sweep_dir, require_integrity=True)]
            self.assertEqual(loaded_ids, [r['id'] for r in records])

            tree = load_consolidated_sweep(sweep_dir / manifest['consolidated_bundle'])
            self.assertEqual(tree['regions/inner'].ds['coordinates'].dtype, np.float32)
            self.assertEqual(tree['regions/inner'].ds['coordinates'].shape, (4, 32, 3))

    def test_as_numpy_project_loading_and_cross_platform_graphics_modes(self):
        batches = sample_geometry_batch(self._requests())
        with tempfile.TemporaryDirectory() as directory:
            proj = Path(directory) / 'numpy_fast.jorvik'
            saved = state()
            saved['selected_area'] = 'inner'
            save_project(proj, self.annulus, [(b, 'Generated', True) for b in batches], saved)
            manifest, _ = load_project(proj, as_numpy=True)
            area0 = manifest['areas'][0]['area']
            self.assertIsInstance(area0['coordinates'], np.ndarray)
            self.assertIsInstance(area0['entity_tags'], np.ndarray)
            self.assertIsInstance(area0['weights'], np.ndarray)

        # Cross-platform graphics configuration checks (Windows, macOS, Linux X11, Linux Wayland)
        env_sw = {'QTWEBENGINE_CHROMIUM_FLAGS': '--use-gl=desktop --enable-logging'}
        self.assertEqual(configure_graphics('software', env_sw), 'software')
        self.assertEqual(env_sw['QT_OPENGL'], 'software')
        self.assertIn('--use-angle=swiftshader', env_sw['QTWEBENGINE_CHROMIUM_FLAGS'])
        self.assertNotIn('--use-gl=desktop', env_sw['QTWEBENGINE_CHROMIUM_FLAGS'])

        for platform_name in ('win32', 'darwin'):
            with patch.object(sys, 'platform', platform_name):
                self.assertFalse(nvidia_wayland_available({'WAYLAND_DISPLAY': 'wayland-0'}))
                env_auto = {}
                self.assertEqual(configure_graphics('auto', env_auto), 'auto')
                self.assertEqual(env_auto, {})

    def test_end_to_end_parametric_pinn_workflow_example(self):
        sys.path.insert(0, str(ROOT / 'examples'))
        try:
            import pinn_parametric_workflow
            with tempfile.TemporaryDirectory() as directory:
                report = pinn_parametric_workflow.run(Path(directory) / 'pinn_out', boundary_count=64, interior_count=128)
                self.assertEqual(report['configurations'], 3)
                self.assertTrue(report['topology_rejection_verified'])
                self.assertEqual(report['datatree_inner_shape'], [3, 64, 3])
                self.assertEqual(report['datatree_interior_shape'], [3, 128, 3])
                for residual in report['harmonic_flux_residuals']:
                    self.assertLess(residual, 0.05)
        finally:
            sys.path.pop(0)


if __name__ == '__main__':
    unittest.main()
