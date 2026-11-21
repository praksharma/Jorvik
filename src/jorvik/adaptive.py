"""Geometric junction/corner singularity detection and Residual-Adaptive Distribution (RAD / RAR-D & RAR-G) resampling."""
from copy import deepcopy
import math
import numpy as np


def detect_singular_junctions(geometry, regions=None, *, angle_threshold_deg=30.0):
    """Detect BC-region junction entities and sharp corner/crease entities on a 2D or 3D CAD model."""
    from .sampling import _load_geometry

    dim = int(geometry['dimension'])
    active_regions = regions if regions is not None else geometry.get('regions', [])
    threshold_rad = math.radians(float(angle_threshold_deg))
    cos_thresh = math.cos(threshold_rad)

    gmsh = _load_geometry(geometry['source'], geometry['source_hash'])
    try:
        if dim == 2:
            # 1D boundary curves meet at 0D vertices
            vertex_regions = {}
            for reg in active_regions:
                r_label = str(reg['label'])
                for e_dim, e_tag in reg.get('entities', []):
                    if int(e_dim) != 1:
                        continue
                    _, down = gmsh.model.getAdjacencies(1, int(e_tag))
                    for v_tag in down:
                        vertex_regions.setdefault(int(v_tag), set()).add(r_label)
            region_junctions = sorted(
                [0, v_tag] for v_tag, r_set in vertex_regions.items() if len(r_set) >= 2
            )

            # Check tangent deflection angle at every 0D vertex shared by 1D edges
            vertex_tangents = {}
            for _, e_tag in gmsh.model.getEntities(1):
                e_tag = int(e_tag)
                _, down = gmsh.model.getAdjacencies(1, e_tag)
                if len(down) < 1:
                    continue
                low, high = gmsh.model.getParametrizationBounds(1, e_tag)
                for idx_end, t_param in enumerate((float(low[0]), float(high[0]))):
                    if idx_end >= len(down):
                        break
                    v_tag = int(down[idx_end])
                    deriv = np.asarray(gmsh.model.getDerivative(1, e_tag, [t_param]), dtype=np.float64).reshape(3)
                    norm = float(np.linalg.norm(deriv))
                    if norm > 1e-12:
                        # Orient tangent pointing away from the vertex into the curve interior
                        t_unit = (deriv / norm) if idx_end == 0 else (-deriv / norm)
                        vertex_tangents.setdefault(v_tag, []).append(t_unit)

            sharp_corners = []
            for v_tag, t_list in sorted(vertex_tangents.items()):
                is_sharp = False
                for i in range(len(t_list)):
                    for j in range(i + 1, len(t_list)):
                        # For a smooth continuation across the vertex, outgoing tangents point in opposite directions (dot == -1)
                        # Deflection angle from 180 deg is acos(-dot)
                        dot = float(np.clip(np.dot(t_list[i], t_list[j]), -1.0, 1.0))
                        if -dot < cos_thresh:
                            is_sharp = True
                            break
                    if is_sharp:
                        break
                if is_sharp:
                    sharp_corners.append([0, int(v_tag)])

            all_tags = sorted({item[1] for item in (region_junctions + sharp_corners)})
            all_entities = [[0, t] for t in all_tags]
            coords = []
            for v_tag in all_tags:
                bb = gmsh.model.getBoundingBox(0, int(v_tag))
                coords.append([0.5 * (bb[0] + bb[3]), 0.5 * (bb[1] + bb[4]), 0.5 * (bb[2] + bb[5])])
            if not coords:
                # Fallback to all 0D boundary vertices if curves form smooth single-region loops
                for _, v_tag in sorted(gmsh.model.getEntities(0)):
                    bb = gmsh.model.getBoundingBox(0, int(v_tag))
                    all_entities.append([0, int(v_tag)])
                    coords.append([0.5 * (bb[0] + bb[3]), 0.5 * (bb[1] + bb[4]), 0.5 * (bb[2] + bb[5])])
            return {
                'dimension': 2,
                'junction_dimension': 0,
                'region_junction_entities': region_junctions,
                'sharp_corner_entities': sharp_corners,
                'all_singular_entities': all_entities,
                'singular_coordinates': coords,
            }

        # 3D: 2D boundary faces meet along 1D curves
        edge_regions = {}
        for reg in active_regions:
            r_label = str(reg['label'])
            for e_dim, f_tag in reg.get('entities', []):
                if int(e_dim) != 2:
                    continue
                _, down = gmsh.model.getAdjacencies(2, int(f_tag))
                for c_tag in down:
                    edge_regions.setdefault(int(c_tag), set()).add(r_label)
        region_junctions = sorted(
            [1, c_tag] for c_tag, r_set in edge_regions.items() if len(r_set) >= 2
        )

        sharp_creases = []
        for _, c_tag in sorted(gmsh.model.getEntities(1)):
            c_tag = int(c_tag)
            upward, _ = gmsh.model.getAdjacencies(1, c_tag)
            faces = [int(f) for f in upward]
            if len(faces) < 2:
                continue
            low, high = gmsh.model.getParametrizationBounds(1, c_tag)
            t_mid = 0.5 * (float(low[0]) + float(high[0]))
            mid_xyz = np.asarray(gmsh.model.getValue(1, c_tag, [t_mid]), dtype=np.float64).reshape(3)
            f_normals = []
            for f_tag in faces[:2]:
                uv = gmsh.model.getParametrization(2, f_tag, mid_xyz)
                n_vec = np.asarray(gmsh.model.getNormal(f_tag, uv), dtype=np.float64).reshape(3)
                n_len = float(np.linalg.norm(n_vec))
                if n_len > 1e-12:
                    f_normals.append(n_vec / n_len)
            if len(f_normals) == 2:
                if abs(float(np.dot(f_normals[0], f_normals[1]))) < cos_thresh:
                    sharp_creases.append([1, c_tag])

        all_tags = sorted({item[1] for item in (region_junctions + sharp_creases)})
        if not all_tags:
            all_tags = sorted(int(t) for _, t in gmsh.model.getEntities(1))
        all_entities = [[1, t] for t in all_tags]
        coords = []
        for c_tag in all_tags:
            low, high = gmsh.model.getParametrizationBounds(1, int(c_tag))
            ts = np.linspace(float(low[0]), float(high[0]), 65)
            pts = np.asarray(gmsh.model.getValue(1, int(c_tag), ts), dtype=np.float64).reshape(-1, 3)
            coords.extend(pts.tolist())
        return {
            'dimension': 3,
            'junction_dimension': 1,
            'region_junction_entities': region_junctions,
            'sharp_corner_entities': sharp_creases,
            'all_singular_entities': all_entities,
            'singular_coordinates': coords,
        }
    finally:
        gmsh.finalize()


def _importance_subsample(pool_batch, unnormalized_density, target_count, seed):
    """Draw target_count points without replacement from pool_batch weighted by unnormalized_density."""
    q = np.asarray(unnormalized_density, dtype=np.float64)
    if q.ndim != 1 or len(q) != pool_batch['count'] or not np.isfinite(q).all() or np.any(q <= 0):
        raise ValueError('Importance density must be finite and strictly positive for all candidate points.')
    probs = q / np.sum(q)
    rng = np.random.default_rng(int(seed) + 7919)
    chosen = np.sort(rng.choice(len(q), size=int(target_count), replace=False, p=probs))

    result = deepcopy(pool_batch)
    result['count'] = int(target_count)
    result['coordinates'] = np.asarray(pool_batch['coordinates'], dtype=np.float64)[chosen].tolist()
    result['entity_tags'] = np.asarray(pool_batch['entity_tags'], dtype=int)[chosen].tolist()
    if 'normals' in pool_batch and pool_batch.get('normal_status') == 'available':
        result['normals'] = np.asarray(pool_batch['normals'], dtype=np.float64)[chosen].tolist()
    if 'boundary_distances' in pool_batch and pool_batch.get('distance_status') == 'available':
        result['boundary_distances'] = np.asarray(pool_batch['boundary_distances'], dtype=np.float64)[chosen].tolist()

    domain_measure = float(pool_batch.get('domain_measure', float(target_count)))
    inv_q = 1.0 / q[chosen]
    weights = domain_measure * inv_q / np.sum(inv_q)
    result['weights'] = weights.tolist()
    result['weight_status'] = 'available'
    result['weight_kind'] = f"{pool_batch.get('weight_kind', 'measure')}_importance_weighted"
    return result, chosen


def sample_with_singularity_refinement(
    geometry,
    request,
    *,
    regions=None,
    bias=5.0,
    decay=0.1,
    angle_threshold_deg=30.0,
    pool_multiplier=6,
):
    """Sample a domain or boundary with exponential importance bias toward BC junctions and sharp corners."""
    from scipy.spatial import cKDTree
    from .refinement import refinement_density
    from .sampling import sample_geometry

    target_count = int(request['count'])
    pool_count = max(target_count * int(pool_multiplier), target_count + 128)
    pool_req = deepcopy(request)
    pool_req['count'] = pool_count
    pool_batch = sample_geometry(pool_req)

    sing = detect_singular_junctions(geometry, regions=regions, angle_threshold_deg=angle_threshold_deg)
    sing_pts = np.asarray(sing['singular_coordinates'], dtype=np.float64).reshape(-1, 3)
    if not len(sing_pts):
        raise ValueError('No geometric junction or corner singularities found on this model.')

    pool_xyz = np.asarray(pool_batch['coordinates'], dtype=np.float64)
    tree = cKDTree(sing_pts)
    dists, _ = tree.query(pool_xyz, k=1)
    dists = np.asarray(dists, dtype=np.float64)

    low = np.min(pool_xyz, axis=0)
    high = np.max(pool_xyz, axis=0)
    domain_scale = max(float(np.linalg.norm(high - low)), 1e-6)
    q = refinement_density(dists, domain_scale, float(bias), float(decay))

    result, chosen = _importance_subsample(pool_batch, q, target_count, request.get('seed', 42))
    result['singularity_distances'] = dists[chosen].tolist()
    result['singularity_entities'] = sing['all_singular_entities']
    result['adaptive_mode'] = 'geometric_singularity'
    return result


def adaptive_residual_resample(
    request,
    probe_points,
    residuals,
    *,
    k=1.0,
    c=1.0,
    pool_multiplier=6,
    neighbors=8,
):
    """Resample collocation points using the Wu et al. (2023) RAD density p(x) ~ |r(x)|^k / E[|r|^k] + c."""
    from scipy.spatial import cKDTree
    from .sampling import sample_geometry

    probes = np.asarray(probe_points, dtype=np.float64)
    res = np.abs(np.asarray(residuals, dtype=np.float64))
    if probes.ndim != 2 or probes.shape[1] != 3 or not len(probes) or not np.isfinite(probes).all():
        raise ValueError('Probe points must be a finite non-empty M by 3 array.')
    if res.shape != (len(probes),) or not np.isfinite(res).all():
        raise ValueError('Residuals must be a finite 1D array matching probe_points length.')
    k_val = float(k)
    c_val = float(c)
    if not math.isfinite(k_val) or k_val < 0.0 or not math.isfinite(c_val) or c_val <= 0.0:
        raise ValueError('RAD hyperparameters require k >= 0 and c > 0.')

    target_count = int(request['count'])
    pool_count = max(target_count * int(pool_multiplier), target_count + 128)
    pool_req = deepcopy(request)
    pool_req['count'] = pool_count
    pool_batch = sample_geometry(pool_req)
    pool_xyz = np.asarray(pool_batch['coordinates'], dtype=np.float64)

    tree = cKDTree(probes)
    k_nn = min(max(1, int(neighbors)), len(probes))
    dists, idx = tree.query(pool_xyz, k=k_nn)
    if k_nn == 1:
        interp_res = res[idx]
    else:
        inv_w = 1.0 / np.maximum(dists, 1e-12) ** 2
        inv_w /= np.sum(inv_w, axis=1, keepdims=True)
        interp_res = np.sum(inv_w * res[idx], axis=1)

    powered = np.power(np.maximum(interp_res, 0.0), k_val)
    mean_pow = float(np.mean(powered))
    q = (powered / mean_pow + c_val) if mean_pow > 1e-30 else np.full(len(pool_xyz), c_val, dtype=np.float64)

    result, chosen = _importance_subsample(pool_batch, q, target_count, request.get('seed', 42))
    result['rad_scores'] = q[chosen].tolist()
    result['interpolated_residuals'] = interp_res[chosen].tolist()
    result['rad_parameters'] = {'k': k_val, 'c': c_val, 'pool_multiplier': int(pool_multiplier)}
    result['adaptive_mode'] = 'RAD'
    return result


def adaptive_residual_augment(existing_batch, candidate_batch, candidate_residuals, *, add_count=32):
    """RAR-G (Lu et al. 2021 DeepXDE): greedily append the top add_count highest-residual candidate points."""
    res = np.abs(np.asarray(candidate_residuals, dtype=np.float64))
    if res.shape != (candidate_batch['count'],) or not np.isfinite(res).all():
        raise ValueError('Candidate residuals must be a finite 1D array matching candidate_batch count.')
    m = int(add_count)
    if m <= 0 or m > candidate_batch['count']:
        raise ValueError('add_count must be between 1 and candidate_batch count.')

    top_idx = np.argsort(res)[-m:][::-1]
    merged = deepcopy(existing_batch)
    old_n = int(existing_batch['count'])
    new_n = old_n + m
    merged['count'] = new_n
    merged['coordinates'] = (
        list(existing_batch['coordinates'])
        + np.asarray(candidate_batch['coordinates'], dtype=np.float64)[top_idx].tolist()
    )
    merged['entity_tags'] = (
        list(existing_batch['entity_tags'])
        + np.asarray(candidate_batch['entity_tags'], dtype=int)[top_idx].tolist()
    )
    if existing_batch.get('normal_status') == 'available' and candidate_batch.get('normal_status') == 'available':
        merged['normals'] = (
            list(existing_batch['normals'])
            + np.asarray(candidate_batch['normals'], dtype=np.float64)[top_idx].tolist()
        )
    if existing_batch.get('distance_status') == 'available' and candidate_batch.get('distance_status') == 'available':
        merged['boundary_distances'] = (
            list(existing_batch['boundary_distances'])
            + np.asarray(candidate_batch['boundary_distances'], dtype=np.float64)[top_idx].tolist()
        )
    if 'weights' in existing_batch and 'weights' in candidate_batch:
        w_cat = np.concatenate([
            np.asarray(existing_batch['weights'], dtype=np.float64),
            np.asarray(candidate_batch['weights'], dtype=np.float64)[top_idx],
        ])
        domain_measure = float(existing_batch.get('domain_measure', np.sum(existing_batch['weights'])))
        w_cat = domain_measure * w_cat / np.maximum(np.sum(w_cat), 1e-30)
        merged['weights'] = w_cat.tolist()
    merged['adaptive_mode'] = 'RAR-G'
    merged['rar_added_count'] = m
    return merged
