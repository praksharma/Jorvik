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


def domain_kind(model, source_format=None):
    if model.getEntities(3): return 'solid'
    if planar_script_domain(model): return 'planar'
    return 'open_or_nonplanar'
