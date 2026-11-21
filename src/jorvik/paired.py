"""Pointwise-paired periodic boundary and conformal multi-volume internal interface sampling for PINNs."""
from copy import deepcopy
import math
import numpy as np


def _rotation_matrix(axis, angle, units='deg'):
    axis = np.asarray(axis, dtype=np.float64)
    if axis.shape != (3,) or not np.isfinite(axis).all():
        raise ValueError('Rotation axis must be a finite 3-vector.')
    norm = float(np.linalg.norm(axis))
    if norm <= 1e-12:
        raise ValueError('Rotation axis must be non-zero.')
    u = axis / norm
    theta = math.radians(float(angle)) if units == 'deg' else float(angle)
    if not math.isfinite(theta):
        raise ValueError('Rotation angle must be finite.')
    c, s = math.cos(theta), math.sin(theta)
    ux, uy, uz = u
    cross = np.array([[0.0, -uz, uy], [uz, 0.0, -ux], [-uy, ux, 0.0]], dtype=np.float64)
    return c * np.eye(3) + s * cross + (1.0 - c) * np.outer(u, u)


def _entity_bounds_center(gmsh, entities):
    mins, maxs = [], []
    for dim, tag in entities:
        bb = gmsh.model.getBoundingBox(int(dim), int(tag))
        mins.append(bb[:3])
        maxs.append(bb[3:])
    low = np.min(np.asarray(mins, dtype=np.float64), axis=0)
    high = np.max(np.asarray(maxs, dtype=np.float64), axis=0)
    return 0.5 * (low + high)


def _project_onto_entities(gmsh, entities, target_points):
    """Project target_points (N,3) onto the nearest entity in entities, returning (projected_xyz, entity_tags, distances)."""
    from scipy.spatial import cKDTree
    pts = np.asarray(target_points, dtype=np.float64)
    dim = int(entities[0][0])
    tags = [int(t) for _, t in entities]
    best_dist = np.full(len(pts), np.inf, dtype=np.float64)
    best_xyz = np.zeros_like(pts)
    best_tags = np.zeros(len(pts), dtype=int)
    for tag in tags:
        if dim == 1:
            low, high = gmsh.model.getParametrizationBounds(1, tag)
            t_grid = np.linspace(float(low[0]), float(high[0]), 4097)
            curve = np.asarray(gmsh.model.getValue(1, tag, t_grid), dtype=np.float64).reshape(-1, 3)
            tree = cKDTree(curve)
            _, idx = tree.query(pts, k=1)
            t = t_grid[idx].copy()
            dt = max(float(high[0] - low[0]), 1e-12) / 4096.0
            for _ in range(3):
                c = np.asarray(gmsh.model.getValue(1, tag, t), dtype=np.float64).reshape(-1, 3)
                dc = np.asarray(gmsh.model.getDerivative(1, tag, t), dtype=np.float64).reshape(-1, 3)
                diff = c - pts
                denom = np.einsum('ij,ij->i', dc, dc)
                step = np.where(denom > 1e-18, np.einsum('ij,ij->i', diff, dc) / np.where(denom > 1e-18, denom, 1.0), 0.0)
                t = np.clip(t - np.clip(step, -2.0 * dt, 2.0 * dt), float(low[0]), float(high[0]))
            proj = np.asarray(gmsh.model.getValue(1, tag, t), dtype=np.float64).reshape(-1, 3)
        elif dim == 2:
            low, high = gmsh.model.getParametrizationBounds(2, tag)
            u_lin = np.linspace(float(low[0]), float(high[0]), 65)
            v_lin = np.linspace(float(low[1]), float(high[1]), 65)
            uu, vv = np.meshgrid(u_lin, v_lin, indexing='ij')
            uv_grid = np.column_stack([uu.ravel(), vv.ravel()])
            surf = np.asarray(gmsh.model.getValue(2, tag, uv_grid.ravel()), dtype=np.float64).reshape(-1, 3)
            tree = cKDTree(surf)
            _, idx = tree.query(pts, k=1)
            uv = uv_grid[idx].copy()
            du = max(float(high[0] - low[0]), 1e-12) / 64.0
            dv = max(float(high[1] - low[1]), 1e-12) / 64.0
            for _ in range(4):
                c = np.asarray(gmsh.model.getValue(2, tag, uv.ravel()), dtype=np.float64).reshape(-1, 3)
                jac = np.asarray(gmsh.model.getDerivative(2, tag, uv.ravel()), dtype=np.float64).reshape(-1, 2, 3)
                ru, rv = jac[:, 0, :], jac[:, 1, :]
                diff = pts - c
                gu = np.einsum('ij,ij->i', diff, ru)
                gv = np.einsum('ij,ij->i', diff, rv)
                g11 = np.einsum('ij,ij->i', ru, ru)
                g12 = np.einsum('ij,ij->i', ru, rv)
                g22 = np.einsum('ij,ij->i', rv, rv)
                det = g11 * g22 - g12 * g12
                safe_det = np.where(np.abs(det) > 1e-18, det, 1.0)
                su = np.where(np.abs(det) > 1e-18, (g22 * gu - g12 * gv) / safe_det, 0.0)
                sv = np.where(np.abs(det) > 1e-18, (-g12 * gu + g11 * gv) / safe_det, 0.0)
                uv[:, 0] = np.clip(uv[:, 0] + np.clip(su, -2.0 * du, 2.0 * du), float(low[0]), float(high[0]))
                uv[:, 1] = np.clip(uv[:, 1] + np.clip(sv, -2.0 * dv, 2.0 * dv), float(low[1]), float(high[1]))
            proj = np.asarray(gmsh.model.getValue(2, tag, uv.ravel()), dtype=np.float64).reshape(-1, 3)
        else:
            raise ValueError(f'Periodic projection requires 1D edge or 2D face entities, got dimension {dim}.')
        d = np.linalg.norm(proj - pts, axis=1)
        improved = d < best_dist
        best_dist[improved] = d[improved]
        best_xyz[improved] = proj[improved]
        best_tags[improved] = tag
    return best_xyz, best_tags, best_dist


def sample_periodic_pair(
    source_request,
    target_entities,
    *,
    target_label=None,
    translation='auto',
    rotation_axis=(0.0, 0.0, 1.0),
    rotation_angle=0.0,
    angle_units='deg',
    pivot=(0.0, 0.0, 0.0),
    tolerance=1e-2,
):
    """Sample a periodic boundary pair (Gamma_A, Gamma_B) with pointwise-matched indices and verified normals."""
    from .normals import boundary_normals
    from .sampling import _load_geometry, sample_geometry

    src_entities = [[int(d), int(t)] for d, t in source_request['entities']]
    tgt_entities = [[int(d), int(t)] for d, t in target_entities]
    if not src_entities or not tgt_entities:
        raise ValueError('Periodic pair sampling requires non-empty source and target boundary entities.')
    dim = src_entities[0][0]
    if any(d != dim for d, _ in src_entities) or any(d != dim for d, _ in tgt_entities):
        raise ValueError('Periodic source and target boundary entities must have the same topological dimension.')

    source_batch = sample_geometry(source_request)
    src_pts = np.asarray(source_batch['coordinates'], dtype=np.float64)
    rot = _rotation_matrix(rotation_axis, rotation_angle, units=angle_units)
    piv = np.asarray(pivot, dtype=np.float64)
    if piv.shape != (3,) or not np.isfinite(piv).all():
        raise ValueError('Periodic pivot must be a finite 3-vector.')

    gmsh = _load_geometry(
        source_request['source'],
        source_request['source_hash'],
        source_request.get('resolution', 32),
        source_request.get('compute_mode', 'reproducible'),
        source_request.get('cpu_threads', 1),
    )
    try:
        if translation == 'auto':
            c_src = _entity_bounds_center(gmsh, src_entities)
            c_tgt = _entity_bounds_center(gmsh, tgt_entities)
            rotated_src = (c_src - piv) @ rot.T + piv
            trans_vec = c_tgt - rotated_src
        else:
            trans_vec = np.asarray(translation, dtype=np.float64)
            if trans_vec.shape != (3,) or not np.isfinite(trans_vec).all():
                raise ValueError('Periodic translation must be "auto" or a finite 3-vector.')
        mapped_pts = (src_pts - piv) @ rot.T + piv + trans_vec
        proj_pts, proj_tags, proj_dists = _project_onto_entities(gmsh, tgt_entities, mapped_pts)
        max_err = float(np.max(proj_dists)) if len(proj_dists) else 0.0
        if max_err > float(tolerance):
            raise ValueError(
                f'Periodic target boundary does not match mapped source points within tolerance '
                f'({max_err:.4g} > {float(tolerance):.4g}).'
            )
        norm_info = boundary_normals(gmsh, dim, proj_pts, proj_tags, 'entities')
    finally:
        gmsh.finalize()

    tgt_label = target_label or f"{source_batch['label']}_periodic_partner"
    matrix = np.eye(4, dtype=np.float64)
    matrix[:3, :3] = rot
    matrix[:3, 3] = piv - rot @ piv + trans_vec
    inv_matrix = np.linalg.inv(matrix)
    pair_id = f"periodic:{source_batch['label']}<->{tgt_label}"

    target_batch = deepcopy(source_batch)
    target_batch['label'] = str(tgt_label)
    target_batch['entities'] = tgt_entities
    target_batch['coordinates'] = proj_pts.tolist()
    target_batch['entity_tags'] = proj_tags.tolist()
    for key in ('normals', 'normal_status', 'normal_kind', 'normal_note', 'normal_validation', 'normal_planes'):
        target_batch.pop(key, None)
    target_batch.update(norm_info)

    alignment = 'unverified'
    if source_batch.get('normal_status') == 'available' and target_batch.get('normal_status') == 'available':
        n_src = np.asarray(source_batch['normals'], dtype=np.float64) @ rot.T
        n_tgt = np.asarray(target_batch['normals'], dtype=np.float64)
        dots = np.einsum('ij,ij->i', n_src, n_tgt)
        if np.all(dots < -0.95):
            alignment = 'anti_parallel_verified'
        else:
            alignment = 'non_anti_parallel'

    n_pts = int(source_batch['count'])
    indices = list(range(n_pts))
    source_batch['pairing'] = {
        'pair_id': pair_id,
        'pair_kind': 'periodic',
        'role': 'source',
        'partner_label': str(tgt_label),
        'pair_indices': indices,
        'transform_matrix': matrix.tolist(),
        'translation': trans_vec.tolist(),
        'max_projection_residual': max_err,
        'normal_alignment': alignment,
    }
    target_batch['pairing'] = {
        'pair_id': pair_id,
        'pair_kind': 'periodic',
        'role': 'target',
        'partner_label': str(source_batch['label']),
        'pair_indices': indices,
        'transform_matrix': inv_matrix.tolist(),
        'translation': (-trans_vec @ rot).tolist(),
        'max_projection_residual': max_err,
        'normal_alignment': alignment,
    }
    return source_batch, target_batch


def sample_internal_interface(geometry, request, *, volumes=None):
    """Sample a 3D multi-volume shared interface face with both opposite outward unit normal fields."""
    from .domains import classify_assembly
    from .sampling import _load_geometry, sample_geometry

    req = deepcopy(request)
    if not req.get('entities'):
        assembly = geometry.get('assembly')
        if assembly is None:
            gmsh_tmp = _load_geometry(req['source'], req['source_hash'])
            try:
                assembly = classify_assembly(gmsh_tmp.model)
            finally:
                gmsh_tmp.finalize()
        interface_faces = assembly.get('shared_interface_faces') or assembly.get('interface_faces', [])
        if not interface_faces:
            raise ValueError('Geometry has no shared internal interface faces between 3D solids.')
        req['entities'] = [[2, int(tag)] for tag in interface_faces]
    req['target'] = 'entities'
    batch = sample_geometry(req)
    pts = np.asarray(batch['coordinates'], dtype=np.float64)
    pt_tags = np.asarray(batch['entity_tags'], dtype=int)

    gmsh = _load_geometry(
        req['source'],
        req['source_hash'],
        req.get('resolution', 32),
        req.get('compute_mode', 'reproducible'),
        req.get('cpu_threads', 1),
    )
    try:
        occ_volumes = {tag for _, tag in gmsh.model.occ.getEntities(3)}
        normals_primary = np.empty_like(pts, dtype=np.float64)
        face_parent_pairs = {}
        for tag in np.unique(pt_tags):
            upward, _ = gmsh.model.getAdjacencies(2, int(tag))
            parents = sorted(int(v) for v in upward)
            if len(parents) != 2:
                raise ValueError(
                    f'Internal interface face {int(tag)} must be shared by exactly 2 solid volumes (found {len(parents)}).'
                )
            if volumes is not None:
                v_pair = [int(volumes[0]), int(volumes[1])]
                if sorted(v_pair) != parents:
                    raise ValueError(f'Interface face {int(tag)} separates volumes {parents}, not {v_pair}.')
                v1, v2 = v_pair
            else:
                v1, v2 = parents[0], parents[1]
            face_parent_pairs[str(int(tag))] = [v1, v2]
            mask = pt_tags == tag
            xyz = pts[mask]
            uv = gmsh.model.getParametrization(2, int(tag), xyz.ravel())
            cand = np.asarray(gmsh.model.getNormal(int(tag), uv), dtype=np.float64).reshape(-1, 3)
            lengths = np.linalg.norm(cand, axis=1, keepdims=True)
            if not np.isfinite(cand).all() or np.any(lengths <= 0):
                raise ValueError(f'Interface face {int(tag)} returned invalid surface normals.')
            cand = cand / lengths
            bb = gmsh.model.getBoundingBox(3, v1)
            scale = max(bb[i + 3] - bb[i] for i in range(3))
            if v1 not in occ_volumes:
                gmsh.model.mesh.generate(3)
                gmsh.model.mesh.setOrder(1)

            def inside_v1(point):
                if v1 in occ_volumes:
                    return bool(gmsh.model.isInside(3, v1, point))
                try:
                    elems = gmsh.model.mesh.getElementsByCoordinates(*point, dim=3, strict=True)
                except Exception:
                    return False
                return any(gmsh.model.mesh.getElement(int(e))[3] == v1 for e in elems)

            factors = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2)
            for i, (point, direction) in enumerate(zip(xyz, cand)):
                for factor in factors:
                    offset = scale * factor * direction
                    plus = inside_v1(point + offset)
                    minus = inside_v1(point - offset)
                    if plus != minus:
                        # Outward from v1 into v2 means point + offset is NOT inside v1
                        if plus:
                            cand[i] *= -1.0
                        break
                else:
                    raise ValueError(f'Could not orient interface normal on face {int(tag)} between volumes {v1} and {v2}.')
            normals_primary[mask] = cand
    finally:
        gmsh.finalize()

    batch.pop('normal_note', None)
    batch['normals'] = normals_primary.tolist()
    batch['partner_normals'] = (-normals_primary).tolist()
    batch['normal_status'] = 'available'
    batch['normal_kind'] = 'interface_outward_pair'
    batch['interface_volumes'] = face_parent_pairs
    batch['pairing'] = {
        'pair_id': f"interface:{batch['label']}",
        'pair_kind': 'internal_interface',
        'role': 'shared_interface',
        'interface_volumes': face_parent_pairs,
        'pair_indices': list(range(int(batch['count']))),
    }
    return batch
