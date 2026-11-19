"""Tests for per-point measure weights, Jacobian weight transport, boundary distances and spatial refinement."""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
import xarray as xr

from jorvik.export import export_samples, to_xarray
from jorvik.geometry import load_geometry
from jorvik.parametrisation import transform_batches
from jorvik.project import load_project, save_project
from jorvik.regions import put_region
from jorvik.sampled_areas import area_details
from jorvik.sampling import sample_geometry
from jorvik.sampling_recipe import bind_recipe, create_recipe
from test_affine_transforms import affine_setup
from test_project import state

FIXTURES = Path(__file__).resolve().parents[1] / 'examples/geometries'


class WeightsAndRefinementTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.annulus = load_geometry(FIXTURES / 'cfdbench/annulus_unstructured.geo')
        cls.sphere = load_geometry(FIXTURES / 'cfdbench/sphere_unstructured.geo')
        cls.cylinder = load_geometry(FIXTURES / 'cad/cylinder.brep')

    def _request(self, geometry, **overrides):
        req = {k: deepcopy(geometry[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
        if 'coordinate_units' in geometry:
            req['coordinate_units'] = geometry['coordinate_units']
        req.update(target='selection', entities=[[1, 1]], method='sobol', count=256,
                   seed=42, label='region', resolution=32, compute_mode='reproducible', cpu_threads=1)
        req.update(overrides)
        return req

    def test_measure_weights_across_1d_2d_3d_domains(self):
        # 1D outer circle of radius 1 (edges 5..8): analytic circumference = 2*pi
        outer = sample_geometry(self._request(
            self.annulus, entities=[[1, 5], [1, 6], [1, 7], [1, 8]], count=256, label='outer'
        ))
        self.assertEqual(outer['weight_status'], 'available')
        self.assertAlmostEqual(outer['domain_measure'], 2.0 * math.pi, places=4)
        self.assertAlmostEqual(float(np.sum(outer['weights'])), outer['domain_measure'], places=10)
        tangents = np.asarray(outer['tangents'])
        self.assertEqual(tangents.shape, (256, 3))
        np.testing.assert_allclose(np.linalg.norm(tangents, axis=1), 1.0, atol=1e-7)

        # 2D planar annulus interior (0.5 <= r <= 1.0): analytic area = 0.75*pi
        interior_2d = sample_geometry(self._request(
            self.annulus, target='interior', entities=[], count=512, label='annulus_interior'
        ))
        self.assertEqual(interior_2d['weight_status'], 'available')
        self.assertAlmostEqual(interior_2d['domain_measure'], 0.75 * math.pi, delta=0.03)
        self.assertAlmostEqual(float(np.sum(interior_2d['weights'])), interior_2d['domain_measure'], places=10)
        self.assertIn('surface_plane_normal', interior_2d)
        self.assertAlmostEqual(abs(interior_2d['surface_plane_normal'][2]), 1.0, places=6)

        # 2D curved sphere surface (faces 1..8): analytic area = 4*pi
        faces = [[2, e['tag']] for e in self.sphere['entities'] if e['dimension'] == 2]
        sphere_surf = sample_geometry(self._request(
            self.sphere, entities=faces, count=512, label='sphere_surface'
        ))
        self.assertAlmostEqual(sphere_surf['domain_measure'], 4.0 * math.pi, delta=0.15)
        self.assertAlmostEqual(float(np.sum(sphere_surf['weights'])), sphere_surf['domain_measure'], places=10)

        # 3D solid interior (cylinder.brep)
        vol = sample_geometry(self._request(
            self.cylinder, target='interior', entities=[], count=256, label='cylinder_vol', resolution=16
        ))
        self.assertEqual(vol['entity_dimension'], 3)
        self.assertGreater(vol['domain_measure'], 0.0)
        self.assertAlmostEqual(float(np.sum(vol['weights'])), vol['domain_measure'], places=10)

    def test_affine_jacobian_weight_transport_1d_2d_3d(self):
        # 1D circle scaled into an ellipse with semi-axes a=3, b=2
        outer = sample_geometry(self._request(
            self.annulus, entities=[[1, 5], [1, 6], [1, 7], [1, 8]], count=512, label='outer'
        ))
        setup_1d = affine_setup(source_hash=self.annulus['source_hash'], scale=[3.0, 2.0, 1.0],
                                rotation_axis=[0.0, 0.0, 1.0], rotation_angle=25.0)
        mapped_1d = transform_batches([outer], setup_1d)[0]
        a, b = 3.0, 2.0
        ramanujan_perimeter = math.pi * (3.0 * (a + b) - math.sqrt((3.0 * a + b) * (a + 3.0 * b)))
        self.assertAlmostEqual(mapped_1d['domain_measure'], ramanujan_perimeter, delta=0.05)
        self.assertAlmostEqual(float(np.sum(mapped_1d['weights'])), mapped_1d['domain_measure'], places=10)

        # 2D planar interior under anisotropic scaling [2, 3, 5] in XY plane -> area scales by 2 * 3 = 6
        interior_2d = sample_geometry(self._request(
            self.annulus, target='interior', entities=[], count=256, label='annulus_interior'
        ))
        mapped_2d = transform_batches([interior_2d], affine_setup(
            source_hash=self.annulus['source_hash'], scale=[2.0, 3.0, 5.0],
            rotation_axis=[1.0, 1.0, 0.0], rotation_angle=40.0
        ))[0]
        self.assertAlmostEqual(mapped_2d['domain_measure'], 6.0 * interior_2d['domain_measure'], places=7)
        np.testing.assert_allclose(mapped_2d['weights'], 6.0 * np.asarray(interior_2d['weights']), rtol=1e-7)

        # 3D volume under anisotropic scaling [2, 3, 4] -> volume scales by 2 * 3 * 4 = 24
        vol = sample_geometry(self._request(
            self.cylinder, target='interior', entities=[], count=128, label='cylinder_vol', resolution=16
        ))
        mapped_3d = transform_batches([vol], affine_setup(
            source_hash=self.cylinder['source_hash'], scale=[2.0, 3.0, 4.0],
            rotation_axis=[0.0, 1.0, 1.0], rotation_angle=33.0
        ))[0]
        self.assertAlmostEqual(mapped_3d['domain_measure'], 24.0 * vol['domain_measure'], places=7)
        np.testing.assert_allclose(mapped_3d['weights'], 24.0 * np.asarray(vol['weights']), rtol=1e-7)

    def test_boundary_distance_and_importance_weighted_refinement(self):
        inner_edges = [[1, 1], [1, 2], [1, 3], [1, 4]]
        # Unbiased interior sampling with exact boundary distance to inner hole (r = 0.5)
        unbiased = sample_geometry(self._request(
            self.annulus, target='interior', entities=[], count=512, label='unbiased',
            refinement={'entities': inner_edges, 'bias': 0.0, 'decay': 0.15, 'include_distance': True}
        ))
        self.assertEqual(unbiased['distance_status'], 'available')
        coords = np.asarray(unbiased['coordinates'])
        radii = np.linalg.norm(coords[:, :2], axis=1)
        expected_dist = radii - 0.5
        np.testing.assert_allclose(unbiased['boundary_distances'], expected_dist, atol=2e-3)

        # Biased interior sampling toward the inner hole (bias = 6.0, decay = 0.05)
        biased = sample_geometry(self._request(
            self.annulus, target='interior', entities=[], count=512, label='biased',
            refinement={'entities': inner_edges, 'bias': 6.0, 'decay': 0.05, 'include_distance': True}
        ))
        self.assertEqual(biased['distance_status'], 'available')
        self.assertLess(float(np.mean(biased['boundary_distances'])),
                        float(np.mean(unbiased['boundary_distances'])) * 0.8)
        # Importance-corrected quadrature weights still sum to total domain measure
        self.assertAlmostEqual(float(np.sum(biased['weights'])), biased['domain_measure'], places=10)
        # Quadrature of f(r) = r^2 over annulus [0.5, 1.0]: analytic integral = (pi / 2) * (1 - 0.5^4) = 15*pi/32
        biased_r2 = np.sum(np.asarray(biased['coordinates'])[:, :2] ** 2, axis=1)
        integral_est = float(np.sum(np.asarray(biased['weights']) * biased_r2))
        analytic_integral = 15.0 * math.pi / 32.0
        self.assertAlmostEqual(integral_est, analytic_integral, delta=0.08)

        # Uniform scaling [2, 2, 2] scales boundary_distances by 2; anisotropic scaling marks distance unavailable
        uniform_mapped = transform_batches([biased], affine_setup(
            source_hash=self.annulus['source_hash'], scale=[2.0, 2.0, 2.0], translation=[1.0, -2.0, 0.5]
        ))[0]
        self.assertEqual(uniform_mapped['distance_status'], 'available')
        np.testing.assert_allclose(uniform_mapped['boundary_distances'],
                                   2.0 * np.asarray(biased['boundary_distances']), rtol=1e-7)

        aniso_mapped = transform_batches([biased], affine_setup(
            source_hash=self.annulus['source_hash'], scale=[2.0, 3.0, 1.0]
        ))[0]
        self.assertEqual(aniso_mapped['distance_status'], 'unavailable')
        self.assertNotIn('boundary_distances', aniso_mapped)

    def test_export_project_and_recipe_round_trip(self):
        inner_edges = [[1, 1], [1, 2], [1, 3], [1, 4]]
        req = self._request(
            self.annulus, target='interior', entities=[], count=64, label='refined_interior',
            refinement={'mode': 'boundary_distance', 'entities': inner_edges, 'bias': 2.5, 'decay': 0.15, 'include_distance': True}
        )
        recipe = create_recipe(req)
        regions = put_region([], self.annulus, 'inner_wall', inner_edges, '#41c4b8')
        bound = bind_recipe(recipe, self.annulus, regions)
        self.assertEqual(bound['refinement'], req['refinement'])

        batch = sample_geometry(req)
        edge_batch = sample_geometry(self._request(
            self.annulus, entities=inner_edges, count=32, label='inner_curve'
        ))
        details = '\n'.join(area_details(batch, 'Generated'))
        self.assertIn('Measure:', details)
        self.assertIn('Boundary distance: available', details)

        with tempfile.TemporaryDirectory() as directory:
            nc_path = export_samples([batch, edge_batch], Path(directory) / 'out.nc')
            with xr.open_dataset(nc_path, engine='scipy') as ds:
                self.assertIn('_gs_weights_0', ds.coords)
                self.assertIn('_gs_boundary_distances_0', ds.coords)
                self.assertIn('_gs_tangents_1', ds.coords)
                np.testing.assert_allclose(ds.coords['_gs_weights_0'].values, batch['weights'])
                np.testing.assert_allclose(ds.coords['_gs_boundary_distances_0'].values, batch['boundary_distances'])
                np.testing.assert_allclose(ds.coords['_gs_tangents_1'].values, edge_batch['tangents'])

            npz_path = export_samples([batch, edge_batch], Path(directory) / 'out.npz')
            with np.load(npz_path, allow_pickle=False) as arrays:
                meta = json.loads(arrays['_gs_metadata'].item())
                self.assertEqual(meta['datasets'][0]['weights'], '_gs_weights_0')
                self.assertEqual(meta['datasets'][0]['boundary_distances'], '_gs_boundary_distances_0')
                self.assertEqual(meta['datasets'][1]['tangents'], '_gs_tangents_1')
                np.testing.assert_allclose(arrays['_gs_weights_0'], batch['weights'])
                np.testing.assert_allclose(arrays['_gs_boundary_distances_0'], batch['boundary_distances'])

            proj_path = Path(directory) / 'weights.jorvik'
            saved = state()
            saved['selected_area'] = 'refined_interior'
            save_project(proj_path, self.annulus, [(batch, 'Generated', True), (edge_batch, 'Generated', True)], saved)
            loaded, _ = load_project(proj_path)
            restored_0 = loaded['areas'][0]['area']
            restored_1 = loaded['areas'][1]['area']
            np.testing.assert_allclose(restored_0['weights'], batch['weights'])
            np.testing.assert_allclose(restored_0['boundary_distances'], batch['boundary_distances'])
            np.testing.assert_allclose(restored_1['tangents'], edge_batch['tangents'])


if __name__ == '__main__':
    unittest.main()
