"""Smooth R-function Approximate Distance Functions (ADFs) and transfinite BC blending weights for hard-constrained PINNs."""
from copy import deepcopy
import math
import numpy as np


def _entity_closest_points_and_distances(gmsh, dim, tag, points):
    """Evaluate smooth closest-point distance d_e(x) and unit gradient grad d_e(x) to one CAD entity."""
    from scipy.spatial import cKDTree
    points = np.asarray(points, dtype=np.float64)
    tag = int(tag)
    if dim == 1:
        low, high = gmsh.model.getParametrizationBounds(1, tag)
        t_grid = np.linspace(float(low[0]), float(high[0]), 4097)
        curve = np.asarray(gmsh.model.getValue(1, tag, t_grid), dtype=np.float64).reshape(-1, 3)
        tangents = np.asarray(gmsh.model.getDerivative(1, tag, t_grid), dtype=np.float64).reshape(-1, 3)
        tree = cKDTree(curve)
        _, idx = tree.query(points, k=1)
        t = t_grid[idx].copy()
        span = max(float(high[0] - low[0]), 1e-12)
        dt = span / 4096.0
        # Two vectorized Newton projection steps on the smooth curve parameter t
        for _ in range(2):
            c = np.asarray(gmsh.model.getValue(1, tag, t), dtype=np.float64).reshape(-1, 3)
            dc = np.asarray(gmsh.model.getDerivative(1, tag, t), dtype=np.float64).reshape(-1, 3)
            t_plus = np.clip(t + 0.5 * dt, float(low[0]), float(high[0]))
            t_minus = np.clip(t - 0.5 * dt, float(low[0]), float(high[0]))
            dc_plus = np.asarray(gmsh.model.getDerivative(1, tag, t_plus), dtype=np.float64).reshape(-1, 3)
            dc_minus = np.asarray(gmsh.model.getDerivative(1, tag, t_minus), dtype=np.float64).reshape(-1, 3)
            ddc = (dc_plus - dc_minus) / np.maximum(t_plus - t_minus, 1e-15)[:, None]
            diff = c - points
            f_prime = np.einsum('ij,ij->i', diff, dc)
            f_second = np.einsum('ij,ij->i', dc, dc) + np.einsum('ij,ij->i', diff, ddc)
            step = np.where(np.abs(f_second) > 1e-14, f_prime / np.where(np.abs(f_second) > 1e-14, f_second, 1.0), 0.0)
            t = np.clip(t - np.clip(step, -2.0 * dt, 2.0 * dt), float(low[0]), float(high[0]))
        closest = np.asarray(gmsh.model.getValue(1, tag, t), dtype=np.float64).reshape(-1, 3)
        tangents = np.asarray(gmsh.model.getDerivative(1, tag, t), dtype=np.float64).reshape(-1, 3)
        vec = points - closest
        dist = np.linalg.norm(vec, axis=1)
        t_norm = np.linalg.norm(tangents, axis=1, keepdims=True)
        unit_t = tangents / np.maximum(t_norm, 1e-15)
        fallback = np.column_stack([-unit_t[:, 1], unit_t[:, 0], np.zeros(len(points))])
        fb_norm = np.linalg.norm(fallback, axis=1, keepdims=True)
        fallback = np.where(fb_norm > 1e-12, fallback / np.maximum(fb_norm, 1e-15), np.array([1.0, 0.0, 0.0]))
        grad = np.where(dist[:, None] > 1e-12, vec / np.maximum(dist[:, None], 1e-15), fallback)
        return dist, grad
    if dim == 2:
        low, high = gmsh.model.getParametrizationBounds(2, tag)
        u_lin = np.linspace(float(low[0]), float(high[0]), 65)
        v_lin = np.linspace(float(low[1]), float(high[1]), 65)
        uu, vv = np.meshgrid(u_lin, v_lin, indexing='ij')
        uv_grid = np.column_stack([uu.ravel(), vv.ravel()])
        surf_pts = np.asarray(gmsh.model.getValue(2, tag, uv_grid.ravel()), dtype=np.float64).reshape(-1, 3)
        tree = cKDTree(surf_pts)
        _, idx = tree.query(points, k=1)
        uv = uv_grid[idx].copy()
        du = max(float(high[0] - low[0]), 1e-12) / 64.0
        dv = max(float(high[1] - low[1]), 1e-12) / 64.0
        for _ in range(3):
            c = np.asarray(gmsh.model.getValue(2, tag, uv.ravel()), dtype=np.float64).reshape(-1, 3)
            jac = np.asarray(gmsh.model.getDerivative(2, tag, uv.ravel()), dtype=np.float64).reshape(-1, 2, 3)
            ru, rv = jac[:, 0, :], jac[:, 1, :]
            diff = points - c
            gu = np.einsum('ij,ij->i', diff, ru)
            gv = np.einsum('ij,ij->i', diff, rv)
            g11 = np.einsum('ij,ij->i', ru, ru)
            g12 = np.einsum('ij,ij->i', ru, rv)
            g22 = np.einsum('ij,ij->i', rv, rv)
            det = g11 * g22 - g12 * g12
            safe_det = np.where(np.abs(det) > 1e-18, det, 1.0)
            step_u = np.where(np.abs(det) > 1e-18, (g22 * gu - g12 * gv) / safe_det, 0.0)
            step_v = np.where(np.abs(det) > 1e-18, (-g12 * gu + g11 * gv) / safe_det, 0.0)
            uv[:, 0] = np.clip(uv[:, 0] + np.clip(step_u, -2.0 * du, 2.0 * du), float(low[0]), float(high[0]))
            uv[:, 1] = np.clip(uv[:, 1] + np.clip(step_v, -2.0 * dv, 2.0 * dv), float(low[1]), float(high[1]))
        closest = np.asarray(gmsh.model.getValue(2, tag, uv.ravel()), dtype=np.float64).reshape(-1, 3)
        normals = np.asarray(gmsh.model.getNormal(tag, uv.ravel()), dtype=np.float64).reshape(-1, 3)
        vec = points - closest
        dist = np.linalg.norm(vec, axis=1)
        grad = np.where(dist[:, None] > 1e-12, vec / np.maximum(dist[:, None], 1e-15), normals)
        return dist, grad
    raise ValueError(f'Unsupported boundary entity dimension for ADF evaluation: {dim}.')


def r_equivalence_join(distances_list, gradients_list, *, order=2.0):
    """Join component distance fields d_m(x) and gradients via Rvachev R-equivalence of order p."""
    p = float(order)
    if not math.isfinite(p) or p < 1.0 or p > 64.0:
        raise ValueError('R-function equivalence order p must be between 1 and 64.')
    d_stack = np.stack([np.asarray(d, dtype=np.float64) for d in distances_list], axis=1)  # (N, M)
    g_stack = np.stack([np.asarray(g, dtype=np.float64) for g in gradients_list], axis=1)  # (N, M, 3)
    if d_stack.shape[1] == 1:
        return d_stack[:, 0].copy(), g_stack[:, 0, :].copy()
    d_min = np.min(d_stack, axis=1, keepdims=True)  # (N, 1)
    zero_mask = (d_min[:, 0] <= 1e-14)
    safe_min = np.where(d_min > 1e-14, d_min, 1.0)
    safe_d = np.where(d_stack > 1e-14, d_stack, 1.0)
    # Factor out d_min for numerical stability: (sum (d_min / d_m)^p)^(-1/p)
    ratios = np.where(d_stack > 1e-14, safe_min / safe_d, 0.0)
    powered = np.power(ratios, p)
    sum_powered = np.sum(powered, axis=1, keepdims=True)
    adf = d_min[:, 0] * np.power(np.maximum(sum_powered[:, 0], 1e-30), -1.0 / p)
    # Exact gradient: sum_m (adf / d_m)^(p + 1) grad d_m
    adf_ratio = np.where(d_stack > 1e-14, adf[:, None] / safe_d, 0.0)
    weights = np.power(adf_ratio, p + 1.0)  # (N, M)
    grad = np.einsum('nm,nmj->nj', weights, g_stack)
    if np.any(zero_mask):
        argmin = np.argmin(d_stack, axis=1)
        adf[zero_mask] = 0.0
        grad[zero_mask] = g_stack[zero_mask, argmin[zero_mask], :]
    return adf, grad


def transfinite_blending_weights(region_adfs, region_gradients=None, *, exponent=2.0):
    """Compute Sukumar-Srivastava transfinite inverse-distance weights w_k(x) and optional gradients."""
    mu = float(exponent)
    if not math.isfinite(mu) or mu <= 0.0 or mu > 32.0:
        raise ValueError('Transfinite blending exponent mu must be in (0, 32].')
    d_stack = np.stack([np.asarray(d, dtype=np.float64) for d in region_adfs], axis=1)  # (N, K)
    n_pts, k_reg = d_stack.shape
    if k_reg == 0:
        raise ValueError('At least one boundary region is required for transfinite weights.')
    if k_reg == 1:
        w = np.ones((n_pts, 1), dtype=np.float64)
        if region_gradients is None:
            return w
        return w, np.zeros((n_pts, 1, 3), dtype=np.float64)
    d_min = np.min(d_stack, axis=1, keepdims=True)
    zero_mask = d_stack <= 1e-14
    any_zero = np.any(zero_mask, axis=1)
    safe_min = np.where(d_min > 1e-14, d_min, 1.0)
    safe_d = np.where(d_stack > 1e-14, d_stack, 1.0)
    inv_pow = np.power(safe_min / safe_d, mu)
    denom = np.sum(inv_pow, axis=1, keepdims=True)
    w = inv_pow / np.maximum(denom, 1e-30)
    if np.any(any_zero):
        z_counts = np.sum(zero_mask[any_zero], axis=1, keepdims=True)
        w[any_zero] = zero_mask[any_zero].astype(np.float64) / np.maximum(z_counts, 1.0)
    if region_gradients is None:
        return w
    g_stack = np.stack([np.asarray(g, dtype=np.float64) for g in region_gradients], axis=1)  # (N, K, 3)
    inv_d = np.where(~any_zero[:, None] & (d_stack > 1e-12), 1.0 / safe_d, 0.0)
    scaled_g = g_stack * inv_d[:, :, None]  # grad(d_k) / d_k
    mean_scaled_g = np.einsum('nk,nkj->nj', w, scaled_g)  # sum_j w_j grad(d_j) / d_j
    grad_w = -mu * w[:, :, None] * (scaled_g - mean_scaled_g[:, None, :])
    if np.any(any_zero):
        grad_w[any_zero] = 0.0
    return w, grad_w


def evaluate_adf_bundle(geometry, points, regions=None, *, order=2.0, transfinite_exponent=2.0):
    """Evaluate per-region and global R-function ADFs, spatial gradients, and transfinite weights."""
    from .sampling import _load_geometry
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise ValueError('Points for ADF evaluation must be a finite non-empty N by 3 array.')
    active_regions = regions if regions is not None else geometry.get('regions', [])
    if not active_regions:
        dim = int(geometry['dimension'])
        b_dim = 1 if dim == 2 else 2
        entities = [[e['dimension'], e['tag']] for e in geometry.get('entities', []) if e['dimension'] == b_dim]
        active_regions = [{'label': 'boundary', 'entities': entities}]
    gmsh = _load_geometry(geometry['source'], geometry['source_hash'])
    try:
        entity_cache = {}
        region_labels = []
        region_adfs = []
        region_grads = []
        for reg in active_regions:
            label = str(reg['label'])
            ents = [tuple(int(v) for v in e) for e in reg['entities']]
            if not ents:
                raise ValueError(f'Boundary region {label!r} has no CAD entities.')
            d_list, g_list = [], []
            for dim, tag in ents:
                if (dim, tag) not in entity_cache:
                    entity_cache[(dim, tag)] = _entity_closest_points_and_distances(gmsh, dim, tag, points)
                d_e, g_e = entity_cache[(dim, tag)]
                d_list.append(d_e)
                g_list.append(g_e)
            d_stack = np.stack(d_list, axis=1)
            g_stack = np.stack(g_list, axis=1)
            argmin = np.argmin(d_stack, axis=1)
            idx_rows = np.arange(len(points))
            r_adf = d_stack[idx_rows, argmin]
            r_grad = g_stack[idx_rows, argmin, :]
            region_labels.append(label)
            region_adfs.append(r_adf)
            region_grads.append(r_grad)
        global_adf, global_grad = r_equivalence_join(region_adfs, region_grads, order=order)
        weights, weight_grads = transfinite_blending_weights(
            region_adfs, region_grads, exponent=transfinite_exponent
        )
        return {
            'status': 'available',
            'order': float(order),
            'transfinite_exponent': float(transfinite_exponent),
            'region_labels': region_labels,
            'global_adf': global_adf.tolist(),
            'global_adf_gradient': global_grad.tolist(),
            'region_adfs': np.stack(region_adfs, axis=1).tolist(),
            'region_adf_gradients': np.stack(region_grads, axis=1).tolist(),
            'transfinite_weights': weights.tolist(),
            'transfinite_weight_gradients': weight_grads.tolist(),
        }
    finally:
        gmsh.finalize()


def attach_adf_to_batch(batch, geometry, regions=None, *, order=2.0, transfinite_exponent=2.0):
    """Return a copy of batch enriched with R-function ADFs, gradients and transfinite weights."""
    record = deepcopy(batch)
    bundle = evaluate_adf_bundle(
        geometry, record['coordinates'], regions=regions,
        order=order, transfinite_exponent=transfinite_exponent,
    )
    record['adf_status'] = 'available'
    record['adf_order'] = bundle['order']
    record['adf_transfinite_exponent'] = bundle['transfinite_exponent']
    record['adf_region_labels'] = bundle['region_labels']
    record['adf_distances'] = bundle['global_adf']
    record['adf_gradients'] = bundle['global_adf_gradient']
    record['region_adf_distances'] = bundle['region_adfs']
    record['region_adf_gradients'] = bundle['region_adf_gradients']
    record['transfinite_weights'] = bundle['transfinite_weights']
    record['transfinite_weight_gradients'] = bundle['transfinite_weight_gradients']
    return record
