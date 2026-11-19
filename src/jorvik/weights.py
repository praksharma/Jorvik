"""Qt-independent measure/quadrature weights and exact affine Jacobian transport."""
import math
import numpy as np


def measure_weights(count, domain_measure, *, importance=None):
    """Return per-point positive measure weights summing to domain_measure."""
    if type(count) is not int or count <= 0:
        raise ValueError('Point count must be a positive integer.')
    measure = float(domain_measure)
    if not math.isfinite(measure) or measure <= 0:
        raise ValueError('Domain measure must be a finite positive number.')
    if importance is None:
        weights = np.full(count, measure / count, dtype=np.float64)
    else:
        density = np.asarray(importance, dtype=np.float64)
        if density.shape != (count,) or not np.isfinite(density).all() or (density <= 0).any():
            raise ValueError('Sampling importance density must be finite and positive.')
        inverse = 1.0 / density
        total = float(inverse.sum())
        if not math.isfinite(total) or total <= 0:
            raise ValueError('Invalid importance weight normalization.')
        weights = measure * (inverse / total)
    return weights, measure


def validate_weights(batch, *, weights=None, tangents=None):
    """Reject non-finite, non-positive or inconsistent measure weights and tangents."""
    if 'tangents' in batch and 'weights' not in batch and weights is None:
        raise ValueError('Curve tangents require measure weights.')
    if 'weights' not in batch and weights is None:
        if batch.get('weight_status') == 'available':
            raise ValueError('Available measure weights are missing their numeric array.')
        return None
    weights = np.asarray(batch['weights'] if weights is None else weights, dtype=np.float64)
    if (weights.shape != (batch['count'],) or not np.isfinite(weights).all()
            or (weights <= 0).any()):
        raise ValueError('Measure weights must be finite positive numbers, one per point.')
    if 'domain_measure' in batch:
        measure = batch['domain_measure']
        if (type(measure) not in (int, float) or not math.isfinite(float(measure))
                or float(measure) <= 0
                or not np.isclose(float(weights.sum()), float(measure), rtol=1e-5, atol=1e-8)):
            raise ValueError('Measure weights do not sum to the declared domain measure.')
    if 'tangents' in batch or tangents is not None:
        tangents = np.asarray(batch['tangents'] if tangents is None else tangents, dtype=np.float64)
        if tangents.shape != (batch['count'], 3) or not np.isfinite(tangents).all():
            raise ValueError('Curve tangents must be finite unit vectors, one per point.')
        lengths = np.einsum('ij,ij->i', tangents, tangents)
        np.sqrt(lengths, out=lengths)
        if not np.allclose(lengths, 1.0, rtol=1e-6, atol=1e-8):
            raise ValueError('Curve tangents must be finite unit vectors, one per point.')
    return weights


def validate_tangents(batch, *, tangents=None):
    if 'tangents' not in batch and tangents is None:
        return None
    tangents = np.asarray(batch['tangents'] if tangents is None else tangents, dtype=np.float64)
    if tangents.shape != (batch['count'], 3) or not np.isfinite(tangents).all():
        raise ValueError('Curve tangents must be finite unit vectors, one per point.')
    lengths = np.einsum('ij,ij->i', tangents, tangents)
    np.sqrt(lengths, out=lengths)
    if not np.allclose(lengths, 1.0, rtol=1e-6, atol=1e-8):
        raise ValueError('Curve tangents must be finite unit vectors, one per point.')
    return tangents


def transport_weights(batch, linear, *, original_normals=None):
    """Transport measure weights and curve tangents under x' = A x + b."""
    weights = validate_weights(batch)
    if weights is None:
        return {}
    linear = np.asarray(linear, dtype=np.float64)
    det = abs(float(np.linalg.det(linear)))
    if not math.isfinite(det) or det <= 0:
        raise ValueError('Singular linear transform cannot transport measure weights.')
    gram = linear.T @ linear
    scale2 = float(gram[0, 0])
    is_uniform = scale2 > 0 and np.allclose(gram, scale2 * np.eye(3), rtol=1e-6, atol=1e-8)
    uniform_scale = math.sqrt(scale2) if is_uniform else None
    dim = batch.get('entity_dimension')
    new_tangents = None
    if dim == 3:
        new_weights = weights * det
    elif dim == 2:
        if original_normals is not None:
            inv_n = np.asarray(original_normals, dtype=np.float64) @ np.linalg.inv(linear)
            stretch = det * np.linalg.norm(inv_n, axis=1)
            new_weights = weights * stretch
        elif 'surface_plane_normal' in batch:
            plane = np.asarray(batch['surface_plane_normal'], dtype=np.float64)
            if plane.shape != (3,) or not np.isfinite(plane).all() or not np.isclose(np.linalg.norm(plane), 1.0, rtol=1e-6, atol=1e-8):
                raise ValueError('Invalid planar surface normal for measure transport.')
            stretch = det * float(np.linalg.norm(plane @ np.linalg.inv(linear)))
            new_weights = weights * stretch
        elif is_uniform:
            new_weights = weights * (uniform_scale ** 2)
        else:
            return {
                'weights': None,
                'domain_measure': None,
                'weight_status': 'unavailable',
                'weight_note': ('Non-uniform scaling of 2D surface measure weights requires '
                                'verified surface normals. No guessed integration weights are exported.'),
            }
    elif dim == 1:
        tangents = validate_tangents(batch)
        if tangents is not None:
            mapped = tangents @ linear.T
            stretch = np.linalg.norm(mapped, axis=1)
            if not np.isfinite(stretch).all() or (stretch <= 0).any():
                raise ValueError('Transform produced degenerate curve tangents.')
            new_weights = weights * stretch
            new_tangents = (mapped / stretch[:, None]).tolist()
        elif original_normals is not None and 'normal_planes' in batch:
            tags = np.asarray(batch['entity_tags'])
            stretch = np.empty(batch['count'], dtype=np.float64)
            for tag, values in batch['normal_planes'].items():
                plane = np.asarray(values, dtype=np.float64)
                mask = tags == int(tag)
                tangent = np.cross(plane, original_normals[mask]) @ linear.T
                stretch[mask] = np.linalg.norm(tangent, axis=1)
            if not np.isfinite(stretch).all() or (stretch <= 0).any():
                raise ValueError('Transform produced invalid curve stretch factors.')
            new_weights = weights * stretch
        elif is_uniform:
            new_weights = weights * uniform_scale
        else:
            return {
                'weights': None,
                'tangents': None,
                'domain_measure': None,
                'weight_status': 'unavailable',
                'weight_note': ('Non-uniform scaling of 1D curve measure weights requires '
                                'curve tangents or plane frames. No guessed weights are exported.'),
            }
    else:
        raise ValueError('Unsupported entity dimension for measure weights.')
    if not np.isfinite(new_weights).all() or (new_weights <= 0).any():
        raise ValueError('Transform produced non-finite or non-positive measure weights.')
    total = float(new_weights.sum())
    if not math.isfinite(total) or total <= 0:
        raise ValueError('Transform produced an invalid total domain measure.')
    result = {
        'weights': new_weights.tolist(),
        'domain_measure': total,
        'weight_status': 'available',
    }
    if new_tangents is not None:
        result['tangents'] = new_tangents
    return result
