"""Exact CAD Darboux tangent frames (t_1, t_2, n) and principal/mean/Gaussian curvatures for manifold & tangential-BC PINNs."""
from copy import deepcopy
import math
import numpy as np


def _orthonormal_frame_from_normal(normals, hint_tangent):
    """Construct right-handed orthonormal (t_1, t_2) given unit normals (N,3) and a tangent hint (N,3)."""
    n = np.asarray(normals, dtype=np.float64)
    n = n / np.maximum(np.linalg.norm(n, axis=1, keepdims=True), 1e-15)
    t1 = hint_tangent - np.einsum('ij,ij->i', hint_tangent, n)[:, None] * n
    t1_norm = np.linalg.norm(t1, axis=1, keepdims=True)
    # Fallback basis vector for any degenerate tangent hint (e.g. spherical pole)
    deg = (t1_norm[:, 0] <= 1e-10)
    if np.any(deg):
        axis = np.where(np.abs(n[deg, 0:1]) < 0.9, np.array([[1.0, 0.0, 0.0]]), np.array([[0.0, 1.0, 0.0]]))
        fb = np.cross(n[deg], axis)
        fb /= np.maximum(np.linalg.norm(fb, axis=1, keepdims=True), 1e-15)
        t1[deg] = fb
        t1_norm = np.linalg.norm(t1, axis=1, keepdims=True)
    t1 = t1 / np.maximum(t1_norm, 1e-15)
    t2 = np.cross(n, t1)
    t2 = t2 / np.maximum(np.linalg.norm(t2, axis=1, keepdims=True), 1e-15)
    return t1, t2


def evaluate_differential_geometry(batch):
    """Compute orthonormal Darboux tangent vectors (t_1, t_2) and CAD curvatures (kappa_1, kappa_2, H, K)."""
    from .sampling import _load_geometry

    dim = int(batch.get('entity_dimension', 0))
    if batch.get('target') == 'interior' or dim not in (1, 2):
        return {
            'differential_geometry_status': 'not_applicable',
            'differential_geometry_note': 'Darboux tangent frames and surface curvatures apply only to 1D/2D boundary datasets.',
        }
    points = np.asarray(batch['coordinates'], dtype=np.float64)
    tags = np.asarray(batch['entity_tags'], dtype=int)
    n_pts = len(points)
    has_normals = batch.get('normal_status') == 'available' and 'normals' in batch
    oriented_normals = np.asarray(batch['normals'], dtype=np.float64) if has_normals else np.zeros((n_pts, 3), dtype=np.float64)

    gmsh = _load_geometry(
        batch['source'],
        batch['source_hash'],
        batch.get('resolution', 32),
        batch.get('compute_mode', 'reproducible'),
        batch.get('cpu_threads', 1),
    )
    try:
        tangent_u = np.zeros((n_pts, 3), dtype=np.float64)
        tangent_v = np.zeros((n_pts, 3), dtype=np.float64)
        kappa_1 = np.zeros(n_pts, dtype=np.float64)
        kappa_2 = np.zeros(n_pts, dtype=np.float64)
        mean_H = np.zeros(n_pts, dtype=np.float64)
        gauss_K = np.zeros(n_pts, dtype=np.float64)

        for tag in np.unique(tags):
            mask = tags == tag
            xyz = points[mask]
            tag_int = int(tag)
            if dim == 1:
                low, high = gmsh.model.getParametrizationBounds(1, tag_int)
                span = max(float(high[0] - low[0]), 1e-12)
                h = span * 1e-5
                t = np.asarray(gmsh.model.getParametrization(1, tag_int, xyz.ravel()), dtype=np.float64)
                t_Clamped = np.clip(t, float(low[0]) + h, float(high[0]) - h)
                dr = np.asarray(gmsh.model.getDerivative(1, tag_int, t_Clamped), dtype=np.float64).reshape(-1, 3)
                dr_plus = np.asarray(gmsh.model.getDerivative(1, tag_int, t_Clamped + h), dtype=np.float64).reshape(-1, 3)
                dr_minus = np.asarray(gmsh.model.getDerivative(1, tag_int, t_Clamped - h), dtype=np.float64).reshape(-1, 3)
                ddr = (dr_plus - dr_minus) / (2.0 * h)
                speed2 = np.einsum('ij,ij->i', dr, dr)
                speed = np.sqrt(np.maximum(speed2, 1e-30))
                t1 = dr / speed[:, None]
                if has_normals:
                    n_loc = oriented_normals[mask]
                    plane_dict = batch.get('normal_planes', {})
                    if str(tag_int) in plane_dict:
                        p_norm = np.broadcast_to(np.asarray(plane_dict[str(tag_int)], dtype=np.float64), n_loc.shape).copy()
                    else:
                        p_norm = np.cross(t1, n_loc)
                        p_len = np.linalg.norm(p_norm, axis=1, keepdims=True)
                        p_norm = np.where(p_len > 1e-12, p_norm / np.maximum(p_len, 1e-15), np.array([0.0, 0.0, 1.0]))
                    # Ensure (t1, t2=p_norm, n_loc) is right-handed: t1 x t2 = n_loc => t1 = t2 x n_loc
                    t1_rh = np.cross(p_norm, n_loc)
                    t1_rh /= np.maximum(np.linalg.norm(t1_rh, axis=1, keepdims=True), 1e-15)
                    t1 = t1_rh
                    t2 = p_norm
                    k1 = -np.einsum('ij,ij->i', ddr, n_loc) / np.maximum(speed2, 1e-30)
                else:
                    cross_val = np.linalg.norm(np.cross(dr, ddr), axis=1)
                    k1 = cross_val / np.maximum(speed ** 3, 1e-30)
                    t2 = np.zeros_like(t1)
                tangent_u[mask] = t1
                tangent_v[mask] = t2
                kappa_1[mask] = k1
                kappa_2[mask] = 0.0
                mean_H[mask] = k1
                gauss_K[mask] = 0.0
            else:
                low, high = gmsh.model.getParametrizationBounds(2, tag_int)
                span_u = max(float(high[0] - low[0]), 1e-12)
                span_v = max(float(high[1] - low[1]), 1e-12)
                hu = span_u * 1e-5
                hv = span_v * 1e-5
                uv = np.asarray(gmsh.model.getParametrization(2, tag_int, xyz.ravel()), dtype=np.float64).reshape(-1, 2)
                # Keep interior margin from coordinate singular boundaries (such as poles)
                margin_u = max(2.0 * hu, span_u * 1e-4)
                margin_v = max(2.0 * hv, span_v * 1e-4)
                uv[:, 0] = np.clip(uv[:, 0], float(low[0]) + margin_u, float(high[0]) - margin_u)
                uv[:, 1] = np.clip(uv[:, 1], float(low[1]) + margin_v, float(high[1]) - margin_v)

                jac = np.asarray(gmsh.model.getDerivative(2, tag_int, uv.ravel()), dtype=np.float64).reshape(-1, 2, 3)
                ru, rv = jac[:, 0, :], jac[:, 1, :]
                uv_pu = uv.copy(); uv_pu[:, 0] += hu
                uv_mu = uv.copy(); uv_mu[:, 0] -= hu
                uv_pv = uv.copy(); uv_pv[:, 1] += hv
                uv_mv = uv.copy(); uv_mv[:, 1] -= hv

                jac_pu = np.asarray(gmsh.model.getDerivative(2, tag_int, uv_pu.ravel()), dtype=np.float64).reshape(-1, 2, 3)
                jac_mu = np.asarray(gmsh.model.getDerivative(2, tag_int, uv_mu.ravel()), dtype=np.float64).reshape(-1, 2, 3)
                jac_pv = np.asarray(gmsh.model.getDerivative(2, tag_int, uv_pv.ravel()), dtype=np.float64).reshape(-1, 2, 3)
                jac_mv = np.asarray(gmsh.model.getDerivative(2, tag_int, uv_mv.ravel()), dtype=np.float64).reshape(-1, 2, 3)

                ruu = (jac_pu[:, 0, :] - jac_mu[:, 0, :]) / (2.0 * hu)
                rvv = (jac_pv[:, 1, :] - jac_mv[:, 1, :]) / (2.0 * hv)
                ruv = (jac_pv[:, 0, :] - jac_mv[:, 0, :]) / (2.0 * hv)

                if has_normals:
                    n_loc = oriented_normals[mask]
                else:
                    n_loc = np.asarray(gmsh.model.getNormal(tag_int, uv.ravel()), dtype=np.float64).reshape(-1, 3)
                    n_loc /= np.maximum(np.linalg.norm(n_loc, axis=1, keepdims=True), 1e-15)

                t1, t2 = _orthonormal_frame_from_normal(n_loc, ru)

                E = np.einsum('ij,ij->i', ru, ru)
                F = np.einsum('ij,ij->i', ru, rv)
                G = np.einsum('ij,ij->i', rv, rv)
                det_I = E * G - F * F
                safe_det_I = np.where(det_I > 1e-20, det_I, 1.0)

                # Outward normal sign convention: convex sphere of radius R with outward n has kappa = +1/R
                L = -np.einsum('ij,ij->i', ruu, n_loc)
                M = -np.einsum('ij,ij->i', ruv, n_loc)
                N = -np.einsum('ij,ij->i', rvv, n_loc)

                K = np.where(det_I > 1e-20, (L * N - M * M) / safe_det_I, 0.0)
                H = np.where(det_I > 1e-20, (E * N - 2.0 * F * M + G * L) / (2.0 * safe_det_I), 0.0)
                disc = np.sqrt(np.maximum(H * H - K, 0.0))
                k1 = H + disc
                k2 = H - disc

                tangent_u[mask] = t1
                tangent_v[mask] = t2
                kappa_1[mask] = k1
                kappa_2[mask] = k2
                mean_H[mask] = H
                gauss_K[mask] = K

        return {
            'differential_geometry_status': 'available',
            'curvature_status': 'available',
            'tangent_u': tangent_u.tolist(),
            'tangent_v': tangent_v.tolist(),
            'principal_curvature_1': kappa_1.tolist(),
            'principal_curvature_2': kappa_2.tolist(),
            'mean_curvature': mean_H.tolist(),
            'gaussian_curvature': gauss_K.tolist(),
        }
    finally:
        gmsh.finalize()


def attach_differential_geometry(batch):
    """Return a copy of batch enriched with Darboux tangent frames and CAD surface curvatures."""
    record = deepcopy(batch)
    record.update(evaluate_differential_geometry(record))
    return record


def transport_differential_geometry(batch, linear, transformed_normals=None):
    """Transport Darboux tangent vectors and curvatures under x' = A x + b."""
    if batch.get('differential_geometry_status') != 'available' or 'tangent_u' not in batch:
        return {}
    linear = np.asarray(linear, dtype=np.float64)
    t1_orig = np.asarray(batch['tangent_u'], dtype=np.float64)
    t2_orig = np.asarray(batch['tangent_v'], dtype=np.float64)
    mapped_t1 = t1_orig @ linear.T
    if transformed_normals is not None:
        t1_new, t2_new = _orthonormal_frame_from_normal(transformed_normals, mapped_t1)
    else:
        t1_norm = np.linalg.norm(mapped_t1, axis=1, keepdims=True)
        t1_new = mapped_t1 / np.maximum(t1_norm, 1e-15)
        mapped_t2 = t2_orig @ linear.T
        t2_proj = mapped_t2 - np.einsum('ij,ij->i', mapped_t2, t1_new)[:, None] * t1_new
        t2_norm = np.linalg.norm(t2_proj, axis=1, keepdims=True)
        t2_new = np.where(t2_norm > 1e-12, t2_proj / np.maximum(t2_norm, 1e-15), 0.0)

    updates = {
        'differential_geometry_status': 'available',
        'tangent_u': t1_new.tolist(),
        'tangent_v': t2_new.tolist(),
    }
    if batch.get('curvature_status') == 'available' and 'mean_curvature' in batch:
        gram = linear.T @ linear
        scale2 = float(gram[0, 0])
        is_uniform = scale2 > 0 and np.allclose(gram, scale2 * np.eye(3), rtol=1e-6, atol=1e-8)
        if is_uniform:
            s = math.sqrt(scale2)
            k1 = np.asarray(batch['principal_curvature_1'], dtype=np.float64) / s
            k2 = np.asarray(batch['principal_curvature_2'], dtype=np.float64) / s
            H = np.asarray(batch['mean_curvature'], dtype=np.float64) / s
            K = np.asarray(batch['gaussian_curvature'], dtype=np.float64) / (s * s)
            updates.update(
                curvature_status='available',
                principal_curvature_1=k1.tolist(),
                principal_curvature_2=k2.tolist(),
                mean_curvature=H.tolist(),
                gaussian_curvature=K.tolist(),
            )
        else:
            updates.update(
                curvature_status='unavailable',
                curvature_note='Anisotropic scaling changes surface fundamental forms non-uniformly; regenerate or re-evaluate CAD curvature.',
                principal_curvature_1=None,
                principal_curvature_2=None,
                mean_curvature=None,
                gaussian_curvature=None,
            )
    return updates
