"""Unit and integration tests for the 5 famous PINN geometry and sampling paradigms."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import numpy as np
import xarray as xr

from jorvik.adf import attach_adf_to_batch, evaluate_adf_bundle, r_equivalence_join, transfinite_blending_weights
from jorvik.adaptive import (
    adaptive_residual_augment,
    adaptive_residual_resample,
    detect_singular_junctions,
    sample_with_singularity_refinement,
)
from jorvik.differential_geometry import attach_differential_geometry
from jorvik.export import export_samples, to_xarray
from jorvik.geometry import load_geometry
from jorvik.paired import sample_internal_interface, sample_periodic_pair
from jorvik.parametrisation import transform_batches
from jorvik.sampling import sample_geometry
from jorvik.spacetime import causal_window_weights, sample_spacetime
from test_affine_transforms import affine_setup


ROOT = Path(__file__).resolve().parents[1]
ANNULUS = ROOT / 'examples' / 'geometries' / 'cfdbench' / 'annulus_unstructured.geo'
SPHERE = ROOT / 'examples' / 'geometries' / 'cfdbench' / 'sphere_unstructured.geo'


def _req(geometry, label, target, entities, count=96, method='sobol', seed=42):
    base = {
        key: geometry[key]
        for key in ('source', 'source_hash', 'document', 'source_format', 'import_options', 'coordinate_units')
        if key in geometry
    }
    base.update(
        label=label,
        target=target,
        entities=entities,
        method=method,
        count=count,
        seed=seed,
        resolution=32,
        compute_mode='reproducible',
        cpu_threads=1,
    )
    return base


class FamousPinnGeometriesTest(unittest.TestCase):
    def test_hard_bc_r_function_adf_smoothness_and_transfinite_weights(self):
        geometry = load_geometry(ANNULUS)
        regions = [
            {'label': 'inner', 'entities': [[1, 1], [1, 2], [1, 3], [1, 4]]},
            {'label': 'outer', 'entities': [[1, 5], [1, 6], [1, 7], [1, 8]]},
        ]
        # Medial circle of the annulus (r_in = 0.5, r_out = 1.0) is r = 0.75,
        # where exact Euclidean distance has a discontinuous radial derivative jump (+1 -> -1),
        # whereas R-function ADF has a smooth zero gradient and finite-difference agreement.
        eps = 1e-5
        base_pt = np.array([0.75, 0.0, 0.0], dtype=np.float64)
        probe_pts = np.array([
            base_pt,
            base_pt + [eps, 0.0, 0.0],
            base_pt - [eps, 0.0, 0.0],
            [0.5, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.65, 0.20, 0.0],
            [0.65 + eps, 0.20, 0.0],
            [0.65 - eps, 0.20, 0.0],
            [0.65, 0.20 + eps, 0.0],
            [0.65, 0.20 - eps, 0.0],
        ], dtype=np.float64)
        bundle = evaluate_adf_bundle(geometry, probe_pts, regions=regions, order=2.0, transfinite_exponent=2.0)
        global_adf = np.asarray(bundle['global_adf'], dtype=np.float64)
        global_grad = np.asarray(bundle['global_adf_gradient'], dtype=np.float64)
        trans_w = np.asarray(bundle['transfinite_weights'], dtype=np.float64)
        trans_gw = np.asarray(bundle['transfinite_weight_gradients'], dtype=np.float64)

        # At r = 0.75 (equidistant from inner and outer), R-function ADF gradient vanishes smoothly
        self.assertTrue(np.allclose(global_grad[0], [0.0, 0.0, 0.0], atol=1e-3))
        fd_medial_x = (global_adf[1] - global_adf[2]) / (2.0 * eps)
        self.assertAlmostEqual(float(global_grad[0, 0]), float(fd_medial_x), places=4)

        # On inner boundary (0.5, 0, 0) and outer boundary (1.0, 0, 0): ADF == 0 and Kronecker delta weights
        self.assertAlmostEqual(float(global_adf[3]), 0.0, places=7)
        self.assertAlmostEqual(float(global_adf[4]), 0.0, places=7)
        self.assertTrue(np.allclose(trans_w[3], [1.0, 0.0], atol=1e-9))
        self.assertTrue(np.allclose(trans_w[4], [0.0, 1.0], atol=1e-9))

        # Finite-difference verification of analytical ADF gradient and transfinite weight gradient at (0.65, 0.20, 0)
        fd_gx = (global_adf[6] - global_adf[7]) / (2.0 * eps)
        fd_gy = (global_adf[8] - global_adf[9]) / (2.0 * eps)
        self.assertAlmostEqual(float(global_grad[5, 0]), float(fd_gx), places=4)
        self.assertAlmostEqual(float(global_grad[5, 1]), float(fd_gy), places=4)

        fd_wx = (trans_w[6] - trans_w[7]) / (2.0 * eps)
        fd_wy = (trans_w[8] - trans_w[9]) / (2.0 * eps)
        self.assertTrue(np.allclose(trans_gw[5, :, 0], fd_wx, atol=1e-3))
        self.assertTrue(np.allclose(trans_gw[5, :, 1], fd_wy, atol=1e-3))
        self.assertTrue(np.allclose(np.sum(trans_w, axis=1), 1.0, atol=1e-12))

    def test_periodic_twin_sampling_and_conformal_internal_interface_normals(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rect_geo = root / 'rect.geo'
            rect_geo.write_text(
                'Point(1) = {0, 0, 0, 0.2};\n'
                'Point(2) = {3, 0, 0, 0.2};\n'
                'Point(3) = {3, 1.5, 0, 0.2};\n'
                'Point(4) = {0, 1.5, 0, 0.2};\n'
                'Line(1) = {1, 2};\n'
                'Line(2) = {2, 3};\n'
                'Line(3) = {3, 4};\n'
                'Line(4) = {4, 1};\n'
                'Curve Loop(1) = {1, 2, 3, 4};\n'
                'Plane Surface(1) = {1};\n'
            )
            geom = load_geometry(rect_geo)
            left_req = _req(geom, 'left_wall', 'entities', [[1, 4]], count=40)
            left_b, right_b = sample_periodic_pair(left_req, [[1, 2]], target_label='right_wall', translation='auto')
            x_left = np.asarray(left_b['coordinates'], dtype=np.float64)
            x_right = np.asarray(right_b['coordinates'], dtype=np.float64)
            n_left = np.asarray(left_b['normals'], dtype=np.float64)
            n_right = np.asarray(right_b['normals'], dtype=np.float64)
            self.assertTrue(np.allclose(x_right - x_left, [3.0, 0.0, 0.0], atol=1e-7))
            self.assertTrue(np.allclose(n_left + n_right, 0.0, atol=1e-7))
            self.assertEqual(left_b['pairing']['normal_alignment'], 'anti_parallel_verified')

            # Wrong periodic translation must fail tolerance check clearly
            with self.assertRaisesRegex(ValueError, 'Periodic target boundary does not match'):
                sample_periodic_pair(left_req, [[1, 2]], translation=[1.0, 0.0, 0.0], tolerance=1e-3)

            # 3D multi-volume internal interface normal pair
            blocks_geo = root / 'blocks.geo'
            blocks_geo.write_text(
                'SetFactory("OpenCASCADE");\n'
                'Box(1) = {0, 0, 0, 1, 1, 1};\n'
                'Box(2) = {1, 0, 0, 1, 1, 1};\n'
                'BooleanFragments{ Volume{1}; Delete; }{ Volume{2}; Delete; }\n'
            )
            blocks = load_geometry(blocks_geo)
            iface = sample_internal_interface(blocks, _req(blocks, 'interface', 'entities', [], count=32), volumes=(1, 2))
            n12 = np.asarray(iface['normals'], dtype=np.float64)
            n21 = np.asarray(iface['partner_normals'], dtype=np.float64)
            # Outward from Volume 1 ([0,1]^3) into Volume 2 ([1,2]x[0,1]^2) is +x = [1, 0, 0]
            self.assertTrue(np.allclose(n12, [1.0, 0.0, 0.0], atol=1e-6))
            self.assertTrue(np.allclose(n21, [-1.0, 0.0, 0.0], atol=1e-6))

            # Transport interface normals under 90-degree rotation around z
            setup = affine_setup(
                scope='domain',
                source_hash=blocks['source_hash'],
                regions=['interface'],
                rotation_axis=[0.0, 0.0, 1.0],
                rotation_angle=90.0,
            )
            rotated = transform_batches([iface], setup)[0]
            self.assertTrue(np.allclose(rotated['normals'], [0.0, 1.0, 0.0], atol=1e-6))
            self.assertTrue(np.allclose(rotated['partner_normals'], [0.0, -1.0, 0.0], atol=1e-6))

    def test_darboux_tangent_frames_and_exact_cad_curvatures_2d_and_3d(self):
        # 1D circular arcs in 2D annulus (inner radius 0.5 -> curvature +2.0; outer radius 1.0 -> curvature +1.0)
        annulus = load_geometry(ANNULUS)
        inner_b = attach_differential_geometry(
            sample_geometry(_req(annulus, 'inner_curve', 'entities', [[1, 1], [1, 2], [1, 3], [1, 4]], count=48))
        )
        t1_in = np.asarray(inner_b['tangent_u'], dtype=np.float64)
        n_in = np.asarray(inner_b['normals'], dtype=np.float64)
        self.assertTrue(np.allclose(np.einsum('ij,ij->i', t1_in, n_in), 0.0, atol=1e-12))
        self.assertTrue(np.allclose(np.abs(inner_b['mean_curvature']), 2.0, rtol=1e-3, atol=1e-3))

        # 2D sphere surface in 3D
        sphere = load_geometry(SPHERE)
        faces = [[e['dimension'], e['tag']] for e in sphere['entities'] if e['dimension'] == 2]
        sph_b = attach_differential_geometry(
            sample_geometry(_req(sphere, 'sphere_surf', 'entities', faces, count=64))
        )
        t1 = np.asarray(sph_b['tangent_u'], dtype=np.float64)
        t2 = np.asarray(sph_b['tangent_v'], dtype=np.float64)
        n_s = np.asarray(sph_b['normals'], dtype=np.float64)
        self.assertTrue(np.allclose(np.linalg.norm(t1, axis=1), 1.0, atol=1e-12))
        self.assertTrue(np.allclose(np.linalg.norm(t2, axis=1), 1.0, atol=1e-12))
        self.assertTrue(np.allclose(np.einsum('ij,ij->i', t1, t2), 0.0, atol=1e-12))
        self.assertTrue(np.allclose(np.cross(t1, t2), n_s, atol=1e-12))
        r_sph = float(np.mean(np.linalg.norm(np.asarray(sph_b['coordinates'], dtype=np.float64), axis=1)))
        self.assertTrue(np.allclose(sph_b['mean_curvature'], 1.0 / r_sph, rtol=1e-3, atol=1e-3))
        self.assertTrue(np.allclose(sph_b['gaussian_curvature'], 1.0 / (r_sph ** 2), rtol=1e-3, atol=1e-3))

        # Uniform 2x scaling halves mean curvature and quarters Gaussian curvature while keeping (t1, t2, n) orthonormal
        scale_setup = affine_setup(
            scope='domain',
            source_hash=sphere['source_hash'],
            regions=['sphere_surf'],
            scale=[2.0, 2.0, 2.0],
        )
        scaled_sph = transform_batches([sph_b], scale_setup)[0]
        self.assertEqual(scaled_sph['curvature_status'], 'available')
        self.assertTrue(np.allclose(scaled_sph['mean_curvature'], 0.5 / r_sph, rtol=1e-3, atol=1e-3))
        self.assertTrue(np.allclose(scaled_sph['gaussian_curvature'], 0.25 / (r_sph ** 2), rtol=1e-3, atol=1e-3))

        # Anisotropic scaling preserves orthonormal tangent frame (t1', t2', n') and marks curvature unavailable
        aniso_setup = affine_setup(
            scope='domain',
            source_hash=sphere['source_hash'],
            regions=['sphere_surf'],
            scale=[2.0, 1.0, 0.5],
        )
        aniso_sph = transform_batches([sph_b], aniso_setup)[0]
        self.assertEqual(aniso_sph['differential_geometry_status'], 'available')
        self.assertEqual(aniso_sph['curvature_status'], 'unavailable')
        t1_a = np.asarray(aniso_sph['tangent_u'], dtype=np.float64)
        t2_a = np.asarray(aniso_sph['tangent_v'], dtype=np.float64)
        n_a = np.asarray(aniso_sph['normals'], dtype=np.float64)
        self.assertTrue(np.allclose(np.cross(t1_a, t2_a), n_a, atol=1e-12))

    def test_singularity_refinement_and_rad_rar_adaptive_resampling(self):
        annulus = load_geometry(ANNULUS)
        regions = [
            {'label': 'inner_left', 'entities': [[1, 2], [1, 3]]},
            {'label': 'inner_right', 'entities': [[1, 1], [1, 4]]},
        ]
        sing = detect_singular_junctions(annulus, regions=regions)
        self.assertEqual(len(sing['region_junction_entities']), 2)
        sing_batch = sample_with_singularity_refinement(
            annulus, _req(annulus, 'sing_int', 'interior', [], count=128), regions=regions, bias=6.0, decay=0.08
        )
        self.assertEqual(sing_batch['adaptive_mode'], 'geometric_singularity')
        self.assertAlmostEqual(float(np.sum(sing_batch['weights'])), float(sing_batch['domain_measure']), places=6)

        # RAD adaptive resampling concentrates points near a localized residual hotspot while conserving measure
        uniform_batch = sample_geometry(_req(annulus, 'uni', 'interior', [], count=256))
        probes = np.asarray(uniform_batch['coordinates'], dtype=np.float64)
        hotspot = np.array([0.75, 0.0, 0.0])
        dists_uni = np.linalg.norm(probes - hotspot, axis=1)
        residuals = np.exp(-(dists_uni / 0.12) ** 2)
        rad_batch = adaptive_residual_resample(
            _req(annulus, 'rad', 'interior', [], count=256), probes, residuals, k=2.0, c=0.1, pool_multiplier=6
        )
        rad_pts = np.asarray(rad_batch['coordinates'], dtype=np.float64)
        dists_rad = np.linalg.norm(rad_pts - hotspot, axis=1)
        # Median distance to hotspot shrinks significantly under RAD importance sampling
        self.assertLess(float(np.median(dists_rad)), float(np.median(dists_uni)))
        self.assertAlmostEqual(float(np.sum(rad_batch['weights'])), float(rad_batch['domain_measure']), places=6)

        # RAR-G greedy augmentation
        augmented = adaptive_residual_augment(uniform_batch, rad_batch, rad_batch['interpolated_residuals'], add_count=24)
        self.assertEqual(augmented['count'], 256 + 24)
        self.assertAlmostEqual(float(np.sum(augmented['weights'])), float(uniform_batch['domain_measure']), places=6)

    def test_causal_spacetime_sampling_and_end_to_end_demo(self):
        annulus = load_geometry(ANNULUS)
        st_batch, ic_batch = sample_spacetime(
            _req(annulus, 'st_domain', 'interior', [], count=120),
            time_bounds=(0.0, 3.0),
            causal_windows=6,
            include_initial=True,
            initial_count=48,
        )
        times = np.asarray(st_batch['times'], dtype=np.float64)
        self.assertTrue(np.all((times >= 0.0) & (times <= 3.0)))
        self.assertTrue(np.all(np.diff(times) >= 0.0))
        self.assertAlmostEqual(
            float(np.sum(st_batch['spacetime_weights'])),
            3.0 * float(st_batch['domain_measure']),
            places=6,
        )
        self.assertTrue(np.allclose(ic_batch['times'], 0.0))

        sched = causal_window_weights(st_batch, [0.05, 0.2, 0.8, 1.5, 2.5, 4.0], epsilon=1.5)
        w_win = sched['window_weights']
        self.assertAlmostEqual(w_win[0], 1.0, places=12)
        for i in range(len(w_win) - 1):
            self.assertGreater(w_win[i], w_win[i + 1])

        with TemporaryDirectory() as tmp:
            nc_path = Path(tmp) / 'spacetime.nc'
            export_samples([st_batch, ic_batch], nc_path)
            with xr.open_dataset(nc_path, engine='scipy') as ds:
                self.assertIn('_gs_times_0', ds.coords)
                self.assertIn('_gs_time_window_index_0', ds.coords)
                self.assertIn('_gs_spacetime_weights_0', ds.coords)

        from examples.famous_pinn_paradigms import run_all_famous_pinn_demos
        summary = run_all_famous_pinn_demos()
        self.assertTrue(summary['adf_transfinite_sum_ok'])
        self.assertTrue(summary['rad_measure_conserved'])
        self.assertEqual(summary['channel_junction_count'], 4)


if __name__ == '__main__':
    unittest.main()
