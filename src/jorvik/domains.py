"""Conservative distinction between solid interiors and planar script domains."""
import numpy as np


def planar_script_domain(model):
    """Recognize coplanar analytic planes; do not assume an open shell is 2D."""
    faces = model.getEntities(2)
    if not faces or any(model.getType(2, tag) != 'Plane' for _, tag in faces):
        return False
    try:
        origin, normal = None, None
        bounds = model.getBoundingBox(-1, -1)
        scale = max(bounds[i+3] - bounds[i] for i in range(3))
        if not np.isfinite(scale) or scale <= 0: return False
        for _, tag in faces:
            low, high = model.getParametrizationBounds(2, tag)
            uv = (np.asarray(low) + np.asarray(high)) / 2
            point = np.asarray(model.getValue(2, tag, uv))
            direction = np.asarray(model.getNormal(tag, uv))
            length = np.linalg.norm(direction)
            if point.shape != (3,) or direction.shape != (3,) or not np.isfinite(point).all() or not np.isfinite(length) or length <= 0:
                return False
            direction = direction / length
            if origin is None:
                origin, normal = point, direction
            elif abs(np.dot(normal, direction)) < 1 - 1e-10 or abs(np.dot(point-origin, normal)) > scale*1e-8:
                return False
        return True
    except Exception:
        return False


def classify_assembly(model):
    """Distinguish single solids, connected multi-volume solids, disconnected bodies, open shells and non-manifold faces."""
    solids = [int(tag) for _, tag in model.getEntities(3)]
    faces = [int(tag) for _, tag in model.getEntities(2)]
    shared_interfaces = []
    non_manifold_faces = []
    open_faces = []
    adjacency = {s: set() for s in solids}
    for ftag in faces:
        upward, _ = model.getAdjacencies(2, ftag)
        parents = sorted(set(int(t) for t in upward))
        if len(parents) == 0:
            open_faces.append(ftag)
        elif len(parents) == 2:
            shared_interfaces.append(ftag)
            s1, s2 = parents
            if s1 in adjacency and s2 in adjacency:
                adjacency[s1].add(s2)
                adjacency[s2].add(s1)
        elif len(parents) > 2:
            non_manifold_faces.append(ftag)
    visited = set()
    components = 0
    for s in solids:
        if s not in visited:
            components += 1
            stack = [s]
            while stack:
                curr = stack.pop()
                if curr not in visited:
                    visited.add(curr)
                    stack.extend(adjacency[curr] - visited)
    return {
        'solid_count': len(solids),
        'disconnected_body_count': components,
        'shared_interface_faces': sorted(shared_interfaces),
        'non_manifold_faces': sorted(non_manifold_faces),
        'unattached_surface_faces': sorted(open_faces) if solids else [],
        'is_manifold': not non_manifold_faces,
        'has_mixed_open_shells': bool(solids and open_faces),
    }


def domain_kind(model, source_format=None):
    assembly = classify_assembly(model)
    if not assembly['is_manifold'] or assembly['has_mixed_open_shells']:
        return 'open_or_nonplanar'
    if assembly['solid_count'] > 0:
        return 'solid'
    if planar_script_domain(model):
        return 'planar'
    return 'open_or_nonplanar'
