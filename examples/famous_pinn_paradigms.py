"""End-to-end showcase of the 5 most influential PINN geometry & sampling paradigms in Jorvik.

Paradigms demonstrated (excluding finite-basis / domain-decomposition PINNs):
1. Hard-Constrained Boundary PINNs (Sukumar & Srivastava 2022; Berg & Nystrom 2018; Lagaris et al. 1998):
   Smooth R-function Approximate Distance Functions (ADFs), spatial gradients, and transfinite blending weights.
2. Periodic & Conformal Interface PINNs (Raissi, Perdikaris & Karniadakis 2019; Dong & Ni 2021):
   Pointwise-paired twin periodic boundary sampling and 3D multi-volume internal interface normal pairs.
3. Manifold, Tangential-BC & Curvature PINNs (Delta-PINNs / Laplace-Beltrami — Costabal, Perdikaris & Kuhl 2024):
   Right-handed orthonormal Darboux tangent frames (t_1, t_2, n) and exact CAD principal/mean/Gaussian curvatures.
4. Residual-Based Adaptive & Singularity-Refined PINNs (RAR / RAD / gPINN — Lu et al. 2021 DeepXDE; Wu et al. 2023):
   Automatic BC-junction & sharp-corner singularity refinement, plus RAD importance resampling and RAR-G greedy augmentation.
5. Causal Space-Time PINNs (Wang, Sankaran & Perdikaris 2024):
   Joint low-discrepancy space-time QMC sampling on [t_0, T] x Omega, initial condition slices, and causal temporal window weights.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
import numpy as np

from jorvik.adf import attach_adf_to_batch, evaluate_adf_bundle
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
from jorvik.sampling import sample_geometry
from jorvik.spacetime import causal_window_weights, sample_spacetime


ROOT = Path(__file__).resolve().parents[1]
ANNULUS = ROOT / 'examples' / 'geometries' / 'cfdbench' / 'annulus_unstructured.geo'
SPHERE = ROOT / 'examples' / 'geometries' / 'cfdbench' / 'sphere_unstructured.geo'


def _base_request(geometry, label, target, entities, count=128, method='sobol', seed=42):
    req = {
        key: geometry[key]
        for key in ('source', 'source_hash', 'document', 'source_format', 'import_options', 'coordinate_units')
        if key in geometry
    }
    req.update(
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
    return req


def run_all_famous_pinn_demos(work_dir=None):
    with TemporaryDirectory() as tmp:
        out = Path(work_dir) if work_dir is not None else Path(tmp)
        out.mkdir(parents=True, exist_ok=True)

        # ------------------------------------------------------------------
        # 1. Hard-Constrained Boundary PINNs (R-function ADFs & Transfinite)
        # ------------------------------------------------------------------
        annulus = load_geometry(ANNULUS)
        regions = [
            {'label': 'inner', 'entities': [[1, 1], [1, 2], [1, 3], [1, 4]]},
            {'label': 'outer', 'entities': [[1, 5], [1, 6], [1, 7], [1, 8]]},
        ]
        int_batch = sample_geometry(_base_request(annulus, 'interior', 'interior', [], count=128))
        adf_batch = attach_adf_to_batch(int_batch, annulus, regions=regions, order=2.0, transfinite_exponent=2.0)
        trans_w = np.asarray(adf_batch['transfinite_weights'], dtype=np.float64)
        # Verify partition of unity and hard-BC extension u(x) = sum_k w_k(x) g_k + d_Omega(x) N_theta(x)
        assert np.allclose(np.sum(trans_w, axis=1), 1.0, atol=1e-12)
        boundary_eval = evaluate_adf_bundle(
            annulus,
            np.array([[0.5, 0.0, 0.0], [1.0, 0.0, 0.0]], dtype=np.float64),
            regions=regions,
        )
        assert np.allclose(boundary_eval['global_adf'], [0.0, 0.0], atol=1e-9)
        assert np.allclose(boundary_eval['transfinite_weights'], [[1.0, 0.0], [0.0, 1.0]], atol=1e-9)

        # ------------------------------------------------------------------
        # 2. Periodic & Conformal Interface PINNs (Twin & Interface Pairs)
        # ------------------------------------------------------------------
        periodic_geo = out / 'periodic_channel.geo'
        periodic_geo.write_text(
            'Point(1) = {0, 0, 0, 0.2};\n'
            'Point(2) = {2, 0, 0, 0.2};\n'
            'Point(3) = {2, 1, 0, 0.2};\n'
            'Point(4) = {0, 1, 0, 0.2};\n'
            'Line(1) = {1, 2};\n'
            'Line(2) = {2, 3};\n'
            'Line(3) = {3, 4};\n'
            'Line(4) = {4, 1};\n'
            'Curve Loop(1) = {1, 2, 3, 4};\n'
            'Plane Surface(1) = {1};\n'
        )
        channel = load_geometry(periodic_geo)
        left_req = _base_request(channel, 'inlet_periodic', 'entities', [[1, 4]], count=48)
        left_batch, right_batch = sample_periodic_pair(left_req, [[1, 2]], target_label='outlet_periodic', translation='auto')
        pts_left = np.asarray(left_batch['coordinates'], dtype=np.float64)
        pts_right = np.asarray(right_batch['coordinates'], dtype=np.float64)
        assert np.allclose(pts_right - pts_left, [2.0, 0.0, 0.0], atol=1e-7)
        assert left_batch['pairing']['normal_alignment'] == 'anti_parallel_verified'

        two_vol_geo = out / 'conjugate_interface.geo'
        two_vol_geo.write_text(
            'SetFactory("OpenCASCADE");\n'
            'Box(1) = {0, 0, 0, 1, 1, 1};\n'
            'Box(2) = {1, 0, 0, 1, 1, 1};\n'
            'BooleanFragments{ Volume{1}; Delete; }{ Volume{2}; Delete; }\n'
        )
        two_vol = load_geometry(two_vol_geo)
        iface_req = _base_request(two_vol, 'interface_12', 'entities', [], count=64)
        iface_batch = sample_internal_interface(two_vol, iface_req)
        n12 = np.asarray(iface_batch['normals'], dtype=np.float64)
        n21 = np.asarray(iface_batch['partner_normals'], dtype=np.float64)
        assert np.allclose(n12 + n21, 0.0, atol=1e-12)

        # ------------------------------------------------------------------
        # 3. Manifold, Tangential-BC & Curvature PINNs (Darboux & Curvature)
        # ------------------------------------------------------------------
        sphere = load_geometry(SPHERE)
        sphere_faces = [[e['dimension'], e['tag']] for e in sphere['entities'] if e['dimension'] == 2]
        surf_batch = sample_geometry(_base_request(sphere, 'sphere_surface', 'entities', sphere_faces, count=96))
        dg_batch = attach_differential_geometry(surf_batch)
        t1 = np.asarray(dg_batch['tangent_u'], dtype=np.float64)
        t2 = np.asarray(dg_batch['tangent_v'], dtype=np.float64)
        n_surf = np.asarray(dg_batch['normals'], dtype=np.float64)
        assert np.allclose(np.einsum('ij,ij->i', t1, n_surf), 0.0, atol=1e-12)
        assert np.allclose(np.einsum('ij,ij->i', t2, n_surf), 0.0, atol=1e-12)
        assert np.allclose(np.cross(t1, t2), n_surf, atol=1e-12)
        # Unit sphere R = 0.5 in CFDBench fixture -> H = 1/R = 2.0, K = 1/R^2 = 4.0
        radius = float(np.mean(np.linalg.norm(np.asarray(dg_batch['coordinates'], dtype=np.float64), axis=1)))
        assert np.allclose(dg_batch['mean_curvature'], 1.0 / radius, rtol=1e-3, atol=1e-3)
        assert np.allclose(dg_batch['gaussian_curvature'], 1.0 / (radius ** 2), rtol=1e-3, atol=1e-3)

        # ------------------------------------------------------------------
        # 4. Residual-Adaptive & Singularity-Refined PINNs (RAR / RAD)
        # ------------------------------------------------------------------
        channel_regions = [
            {'label': 'inlet', 'entities': [[1, 4]]},
            {'label': 'walls', 'entities': [[1, 1], [1, 3]]},
            {'label': 'outlet', 'entities': [[1, 2]]},
        ]
        junctions = detect_singular_junctions(channel, regions=channel_regions)
        assert len(junctions['region_junction_entities']) == 4
        sing_batch = sample_with_singularity_refinement(
            channel,
            _base_request(channel, 'corner_refined', 'interior', [], count=96),
            regions=channel_regions,
            bias=6.0,
            decay=0.1,
        )
        assert np.isclose(np.sum(sing_batch['weights']), sing_batch['domain_measure'], rtol=1e-10)

        # Synthetic PDE residual shock peak at (0.75, 0.0, 0.0) on the annulus
        probe_coords = np.asarray(int_batch['coordinates'], dtype=np.float64)
        shock_center = np.array([0.75, 0.0, 0.0])
        synthetic_residuals = np.exp(-np.sum((probe_coords - shock_center) ** 2, axis=1) / (0.08 ** 2))
        rad_batch = adaptive_residual_resample(
            _base_request(annulus, 'rad_interior', 'interior', [], count=128),
            probe_coords,
            synthetic_residuals,
            k=2.0,
            c=0.2,
        )
        assert np.isclose(np.sum(rad_batch['weights']), rad_batch['domain_measure'], rtol=1e-10)
        rar_batch = adaptive_residual_augment(int_batch, rad_batch, rad_batch['interpolated_residuals'], add_count=16)
        assert rar_batch['count'] == int_batch['count'] + 16

        # ------------------------------------------------------------------
        # 5. Causal Space-Time PINNs (Space-Time QMC & Causal Window Slabs)
        # ------------------------------------------------------------------
        st_batch, ic_batch = sample_spacetime(
            _base_request(annulus, 'spacetime_interior', 'interior', [], count=128),
            time_bounds=(0.0, 2.0),
            causal_windows=4,
            include_initial=True,
            initial_count=64,
        )
        causal_sched = causal_window_weights(st_batch, [0.01, 0.25, 1.2, 3.0], epsilon=2.0)
        assert np.isclose(causal_sched['window_weights'][0], 1.0)
        assert all(
            causal_sched['window_weights'][i] > causal_sched['window_weights'][i + 1]
            for i in range(3)
        )

        # Verify xarray Dataset and NetCDF/NPZ export of enriched PINN batches
        ds = to_xarray([adf_batch, st_batch, ic_batch])
        assert '_gs_adf_distances_0' in ds.coords
        assert '_gs_transfinite_weights_0' in ds.coords
        assert '_gs_times_1' in ds.coords
        export_samples([adf_batch, st_batch, ic_batch], out / 'famous_pinn_bundle.nc')
        export_samples([dg_batch], out / 'darboux_sphere.npz')

        return {
            'adf_transfinite_sum_ok': True,
            'periodic_max_residual': left_batch['pairing']['max_projection_residual'],
            'interface_volumes': iface_batch['interface_volumes'],
            'sphere_mean_curvature': float(np.mean(dg_batch['mean_curvature'])),
            'sphere_gaussian_curvature': float(np.mean(dg_batch['gaussian_curvature'])),
            'channel_junction_count': len(junctions['region_junction_entities']),
            'rad_measure_conserved': bool(np.isclose(np.sum(rad_batch['weights']), rad_batch['domain_measure'])),
            'causal_window_weights': causal_sched['window_weights'],
        }


if __name__ == '__main__':
    summary = run_all_famous_pinn_demos()
    for key, value in summary.items():
        print(f'{key}: {value}')
