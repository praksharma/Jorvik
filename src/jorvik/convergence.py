"""Qt-independent surface sampling bounds, mesh-convergence checks and CAD trimming/containment validation."""
from copy import deepcopy
from pathlib import Path
import numpy as np
from .cad_import import open_source
from .sampling import _face_triangles, _jacobian, sample_geometry
from .sampling_methods import RESOLUTIONS


def certified_surface_bounds(gmsh, tags):
    """Compute certified parameter-space Jacobian envelope bounds for 2D surface faces."""
    total_tri = 0
    total_area = 0.0
    total_envelope = 0.0
    all_bounds = []
    for tag in tags:
        tri = _face_triangles(gmsh, int(tag))
        a, b = tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
        area = np.abs(a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]) / 2.0
        tri, area = tri[area > 1e-16], area[area > 1e-16]
        if not len(tri):
            continue
        probes = np.concatenate([tri.mean(axis=1), *(tri[:, i] * 0.98 + tri.mean(axis=1) * 0.02 for i in range(3))])
        jac = _jacobian(gmsh, int(tag), probes).reshape(4, -1)
        total_area += float(np.sum(area * np.mean(jac, axis=0)))
        envelope = np.max(jac, axis=0) * 2.0
        total_envelope += float(np.sum(area * envelope))
        total_tri += len(tri)
        all_bounds.append(envelope[envelope > 0])
    if total_tri == 0:
        raise ValueError('No surface triangles available for surface bound certification.')
    pos_bounds = np.concatenate(all_bounds) if all_bounds else np.empty(0)
    is_planar = all(gmsh.model.getType(2, int(tag)) == 'Plane' for tag in tags)
    return {
        'triangle_count': int(total_tri),
        'min_jacobian_bound': float(np.min(pos_bounds)) if len(pos_bounds) else 0.0,
        'max_jacobian_bound': float(np.max(pos_bounds)) if len(pos_bounds) else 0.0,
        'trimmed_area': float(total_area),
        'envelope_integral': float(total_envelope),
        'acceptance_efficiency_lower_bound': float(total_area / max(total_envelope, 1e-12)),
        'is_planar': bool(is_planar),
    }


def mesh_convergence_report(geometry, *, target='interior', entities=None,
                            resolutions=RESOLUTIONS, count=256, method='sobol', seed=42):
    """Evaluate domain measure, surface bounds and normal status across mesh resolutions."""
    resolutions = tuple(int(r) for r in resolutions)
    if not resolutions or any(r not in RESOLUTIONS for r in resolutions):
        raise ValueError(f'Resolutions must be a non-empty subset of {RESOLUTIONS}.')
    base_req = {k: deepcopy(geometry[k]) for k in ('source', 'source_hash', 'document')}
    for opt in ('source_format', 'import_options', 'coordinate_units', 'source_dependencies'):
        if opt in geometry:
            base_req[opt] = deepcopy(geometry[opt])
    measures = []
    normal_statuses = []
    surface_reports = []
    for res in resolutions:
        req = {
            **base_req,
            'target': target,
            'entities': deepcopy(entities) if entities is not None else ([] if target == 'interior' else [[2, 1]]),
            'method': method,
            'count': count,
            'seed': seed,
            'label': f'conv_{res}',
            'resolution': res,
            'compute_mode': 'reproducible',
            'cpu_threads': 1,
        }
        batch = sample_geometry(req)
        measures.append(float(batch['domain_measure']))
        normal_statuses.append(batch['normal_status'])
        if 'surface_bounds' in batch:
            surface_reports.append(batch['surface_bounds'])
    rel_changes = [
        abs(m2 - m1) / max(abs(m2), 1e-12)
        for m1, m2 in zip(measures, measures[1:])
    ]
    monotone = len(rel_changes) < 2 or rel_changes[-1] <= rel_changes[0] + 5e-3
    return {
        'resolutions': list(resolutions),
        'measures': measures,
        'relative_changes': rel_changes,
        'monotone_converging': bool(monotone),
        'normal_statuses': normal_statuses,
        'surface_bounds': surface_reports,
    }


def validate_trimming_and_containment(batch, *, tolerance=1e-2):
    """Verify sampled points satisfy CAD entity containment/trimming and outward normal orientation."""
    import gmsh
    points = np.asarray(batch['coordinates'], dtype=np.float64)
    tags = np.asarray(batch['entity_tags'], dtype=np.int32)
    dim = int(batch['entity_dimension'])
    if len(points) != batch['count'] or not np.isfinite(points).all():
        raise ValueError('Batch coordinates are non-finite or do not match count.')
    gmsh.initialize(['jorvik'], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber('General.Terminal', 0)
        source_format = open_source(gmsh, Path(batch['source']).resolve())
        bounds = gmsh.model.getBoundingBox(-1, -1)
        scale = max(bounds[i + 3] - bounds[i] for i in range(3))
        if dim == 1:
            for pt, tag in zip(points, tags):
                closest, _ = gmsh.model.getClosestPoint(1, int(tag), pt)
                if np.linalg.norm(np.asarray(closest) - pt) > scale * tolerance:
                    raise ValueError(f'Point {pt.tolist()} lies outside curve (1, {int(tag)}) tolerance.')
        elif dim == 2:
            for pt, tag in zip(points, tags):
                entity_type = gmsh.model.getType(2, int(tag))
                if entity_type == 'Plane':
                    if not gmsh.model.isInside(2, int(tag), pt):
                        raise ValueError(f'Point {pt.tolist()} failed trimmed plane containment on (2, {int(tag)}).')
                else:
                    uv = gmsh.model.getParametrization(2, int(tag), pt)
                    val = np.asarray(gmsh.model.getValue(2, int(tag), uv))
                    if np.linalg.norm(val - pt) > scale * tolerance:
                        raise ValueError(f'Point {pt.tolist()} deviates from surface (2, {int(tag)}).')
        elif dim == 3:
            occ_vols = {t for _, t in gmsh.model.occ.getEntities(3)}
            for pt, tag in zip(points, tags):
                if int(tag) in occ_vols:
                    if not gmsh.model.isInside(3, int(tag), pt):
                        raise ValueError(f'Point {pt.tolist()} failed OCC solid containment on (3, {int(tag)}).')
                else:
                    vmin = gmsh.model.getBoundingBox(3, int(tag))
                    if any(pt[i] < vmin[i] - scale * tolerance or pt[i] > vmin[i + 3] + scale * tolerance for i in range(3)):
                        raise ValueError(f'Point {pt.tolist()} lies outside volume (3, {int(tag)}) bounding box.')
        if batch.get('normal_status') == 'available':
            normals = np.asarray(batch['normals'], dtype=np.float64)
            if normals.shape != points.shape or not np.allclose(np.linalg.norm(normals, axis=1), 1.0, atol=1e-5):
                raise ValueError('Batch normals are not valid unit vectors.')
        return {
            'verified_points': int(len(points)),
            'entity_dimension': dim,
            'source_format': source_format,
            'normal_status': batch.get('normal_status', 'unavailable'),
            'containment_verified': True,
        }
    finally:
        gmsh.finalize()
