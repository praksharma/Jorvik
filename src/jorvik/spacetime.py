"""Causal space-time QMC sampling, temporal windowing and Wang-Sankaran-Perdikaris (2024) causal weights."""
from copy import deepcopy
import math
import numpy as np


def attach_spacetime(
    batch,
    time_bounds=(0.0, 1.0),
    *,
    causal_windows=8,
    time_method=None,
    seed=None,
    sort_by_time=True,
):
    """Attach low-discrepancy timestamps, causal window indices and space-time quadrature weights to a batch."""
    from .sampling import unit_points

    if (not isinstance(time_bounds, (list, tuple)) or len(time_bounds) != 2
            or any(type(v) is bool or type(v) not in (int, float) or not math.isfinite(float(v)) for v in time_bounds)):
        raise ValueError('time_bounds must be a finite (t_start, t_end) pair.')
    t0, t1 = float(time_bounds[0]), float(time_bounds[1])
    if t1 <= t0:
        raise ValueError('time_bounds requires t_end > t_start.')
    m_win = int(causal_windows)
    if type(causal_windows) is bool or m_win < 1 or m_win > 4096:
        raise ValueError('causal_windows must be an integer between 1 and 4096.')

    record = deepcopy(batch)
    n_pts = int(record['count'])
    method = str(time_method or record.get('method', 'sobol'))
    if method == 'grid':
        method = 'sobol'
    base_seed = int(seed if seed is not None else record.get('seed', 42))

    # Independent scrambled QMC stream + deterministic permutation avoids spatial-temporal diagonal correlation
    u_t = unit_points(method, n_pts, 1, base_seed + 104729).reshape(-1)
    rng = np.random.default_rng(base_seed + 224737)
    u_t = u_t[rng.permutation(n_pts)]
    times = t0 + u_t * (t1 - t0)

    if sort_by_time:
        order = np.argsort(times, kind='mergesort')
        times = times[order]
        record['coordinates'] = np.asarray(record['coordinates'], dtype=np.float64)[order].tolist()
        record['entity_tags'] = np.asarray(record['entity_tags'], dtype=int)[order].tolist()
        for arr_field in (
            'normals', 'partner_normals', 'weights', 'boundary_distances',
            'adf_distances', 'adf_gradients', 'region_adf_distances',
            'region_adf_gradients', 'transfinite_weights', 'transfinite_weight_gradients',
            'tangent_u', 'tangent_v', 'principal_curvature_1', 'principal_curvature_2',
            'mean_curvature', 'gaussian_curvature',
        ):
            if arr_field in record and record[arr_field] is not None:
                record[arr_field] = np.asarray(record[arr_field])[order].tolist()

    dt = (t1 - t0) / m_win
    win_idx = np.clip(np.floor((times - t0) / max(dt, 1e-30)).astype(int), 0, m_win - 1)
    edges = np.linspace(t0, t1, m_win + 1, dtype=np.float64)

    spatial_measure = float(record.get('domain_measure', float(n_pts)))
    st_measure = (t1 - t0) * spatial_measure
    if 'weights' in record and record.get('weight_status') == 'available':
        st_weights = (t1 - t0) * np.asarray(record['weights'], dtype=np.float64)
    else:
        st_weights = np.full(n_pts, st_measure / max(n_pts, 1), dtype=np.float64)

    record['spacetime_status'] = 'available'
    record['spacetime_role'] = 'spacetime_evolution'
    record['time_bounds'] = [t0, t1]
    record['causal_window_count'] = m_win
    record['causal_window_edges'] = edges.tolist()
    record['times'] = times.tolist()
    record['time_window_index'] = win_idx.tolist()
    record['spacetime_measure'] = st_measure
    record['spacetime_weights'] = st_weights.tolist()
    return record


def sample_spacetime(
    request,
    time_bounds=(0.0, 1.0),
    *,
    causal_windows=8,
    include_initial=False,
    initial_count=None,
    time_method=None,
    sort_by_time=True,
):
    """Sample a space-time cylinder [t_0, T] x Omega (or boundary) and optional {t_0} x Omega initial slice."""
    from .sampling import sample_geometry

    spatial_batch = sample_geometry(request)
    st_batch = attach_spacetime(
        spatial_batch,
        time_bounds=time_bounds,
        causal_windows=causal_windows,
        time_method=time_method,
        seed=request.get('seed', 42),
        sort_by_time=sort_by_time,
    )
    if not include_initial:
        return st_batch

    init_req = deepcopy(request)
    init_req['label'] = f"{request['label']}_initial"
    if initial_count is not None:
        init_req['count'] = int(initial_count)
    init_req['seed'] = int(request.get('seed', 42)) + 31337
    init_batch = sample_geometry(init_req)
    t0, t1 = float(time_bounds[0]), float(time_bounds[1])
    n_init = int(init_batch['count'])
    init_batch['spacetime_status'] = 'available'
    init_batch['spacetime_role'] = 'initial_condition'
    init_batch['time_bounds'] = [t0, t1]
    init_batch['causal_window_count'] = int(causal_windows)
    init_batch['causal_window_edges'] = st_batch['causal_window_edges']
    init_batch['times'] = np.full(n_init, t0, dtype=np.float64).tolist()
    init_batch['time_window_index'] = np.zeros(n_init, dtype=int).tolist()
    init_batch['spacetime_measure'] = float(init_batch.get('domain_measure', float(n_init)))
    init_batch['spacetime_weights'] = list(init_batch.get('weights', np.ones(n_init, dtype=np.float64).tolist()))
    return st_batch, init_batch


def causal_window_weights(batch, window_losses, *, epsilon=1.0):
    """Compute Wang-Sankaran-Perdikaris (2024) causal temporal weights w_m = exp(-eps * sum_{k < m} L_k)."""
    if batch.get('spacetime_status') != 'available' or 'time_window_index' not in batch:
        raise ValueError('Batch does not contain space-time causal window metadata.')
    m_win = int(batch['causal_window_count'])
    losses = np.asarray(window_losses, dtype=np.float64)
    if losses.shape != (m_win,) or not np.isfinite(losses).all() or np.any(losses < 0):
        raise ValueError(f'window_losses must be a finite non-negative 1D array of length {m_win}.')
    eps = float(epsilon)
    if not math.isfinite(eps) or eps <= 0.0:
        raise ValueError('Causal steepness parameter epsilon must be positive and finite.')

    cumulative_prev = np.concatenate([[0.0], np.cumsum(losses[:-1])])
    win_weights = np.exp(-np.clip(eps * cumulative_prev, 0.0, 700.0))
    win_idx = np.asarray(batch['time_window_index'], dtype=int)
    st_weights = np.asarray(batch['spacetime_weights'], dtype=np.float64)
    point_causal = win_weights[win_idx]
    point_weighted = point_causal * st_weights
    return {
        'epsilon': eps,
        'window_weights': win_weights.tolist(),
        'min_window_weight': float(np.min(win_weights)),
        'point_causal_factors': point_causal.tolist(),
        'point_causal_quadrature_weights': point_weighted.tolist(),
    }
