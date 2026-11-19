"""Qt-independent boundary-distance fields d(x) and distance-biased spatial refinement."""
import math
import numpy as np


def validate_refinement(refinement):
    """Validate optional distance-field and boundary-refinement settings."""
    if refinement is None:
        return None
    if not isinstance(refinement, dict):
        raise ValueError('Boundary refinement settings must be an object.')
    allowed = {'mode', 'entities', 'bias', 'decay', 'include_distance', 'region'}
    if set(refinement) - allowed:
        raise ValueError('Unsupported boundary refinement fields.')
    if refinement.get('mode', 'boundary_distance') != 'boundary_distance':
        raise ValueError('Unsupported boundary refinement mode.')
    entities = refinement.get('entities')
    if (not isinstance(entities, list) or not entities
            or any(not isinstance(item, list) or len(item) != 2
                   or type(item[0]) is not int or item[0] not in (1, 2)
                   or type(item[1]) is not int or item[1] <= 0 for item in entities)):
        raise ValueError('Refinement needs a non-empty list of reference faces or edges.')
    if len({tuple(item) for item in entities}) != len(entities) or len({item[0] for item in entities}) != 1:
        raise ValueError('Refinement reference entities must be distinct faces or distinct edges.')
    bias = refinement.get('bias', 0.0)
    decay = refinement.get('decay', 0.15)
    if (type(bias) is bool or type(bias) not in (int, float)
            or not math.isfinite(float(bias)) or not 0.0 <= float(bias) <= 10.0):
        raise ValueError('Boundary refinement bias must be between 0 and 10.')
    if (type(decay) is bool or type(decay) not in (int, float)
            or not math.isfinite(float(decay)) or not 0.01 <= float(decay) <= 10.0):
        raise ValueError('Boundary refinement decay scale must be between 0.01 and 10.')
    include_distance = refinement.get('include_distance', True)
    if type(include_distance) is not bool:
        raise ValueError('include_distance must be a boolean.')
    if float(bias) == 0.0 and not include_distance:
        raise ValueError('Enable boundary distance export or set a positive boundary bias.')
    if 'region' in refinement and refinement['region'] is not None:
        region = refinement['region']
        if (not isinstance(region, dict) or not isinstance(region.get('label'), str)
                or not region['label'].strip() or region['label'] != region['label'].strip()
                or sorted(region.get('entities', [])) != sorted(entities)):
            raise ValueError('Invalid refinement boundary-region reference.')
    return {
        'mode': 'boundary_distance',
        'entities': [[int(d), int(t)] for d, t in entities],
        'bias': float(bias),
        'decay': float(decay),
        'include_distance': include_distance,
        **({'region': {'label': refinement['region']['label'],
                       'entities': [[int(d), int(t)] for d, t in refinement['region']['entities']]}}
           if refinement.get('region') is not None else {}),
    }


def _segment_distance(points, seg_a, seg_b):
    """Exact Euclidean distance from each point (N,3) to paired segments (N,3)."""
    ab = seg_b - seg_a
    ap = points - seg_a
    length2 = np.einsum('ij,ij->i', ab, ab)
    t = np.where(length2 > 1e-30, np.einsum('ij,ij->i', ap, ab) / np.where(length2 > 1e-30, length2, 1.0), 0.0)
    t = np.clip(t, 0.0, 1.0)
    closest = seg_a + t[:, None] * ab
    return np.linalg.norm(points - closest, axis=1)


def evaluate_boundary_distance(gmsh, entities, points):
    """Compute unsigned Euclidean distance d(x) from points to reference curves/faces."""
    from scipy.spatial import cKDTree
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise ValueError('Points for boundary distance evaluation must be a finite N×3 array.')
    dim = entities[0][0]
    tags = [int(t) for _, t in entities]
    existing = {t for _, t in gmsh.model.getEntities(dim)}
    if not set(tags) <= existing:
        raise ValueError('Refinement reference boundary entity is absent from this geometry.')
    if dim == 1:
        seg_starts, seg_ends, midpoints = [], [], []
        for tag in tags:
            low, high = gmsh.model.getParametrizationBounds(1, tag)
            params = np.linspace(low[0], high[0], 2049)
            curve = np.asarray(gmsh.model.getValue(1, tag, params), dtype=np.float64).reshape(-1, 3)
            if len(curve) < 2 or not np.isfinite(curve).all():
                raise ValueError(f'Reference edge {tag} has invalid coordinates.')
            seg_starts.append(curve[:-1])
            seg_ends.append(curve[1:])
            midpoints.append(0.5 * (curve[:-1] + curve[1:]))
        seg_a = np.concatenate(seg_starts, axis=0)
        seg_b = np.concatenate(seg_ends, axis=0)
        mids = np.concatenate(midpoints, axis=0)
        tree = cKDTree(mids)
        k = min(8, len(mids))
        _, neighbors = tree.query(points, k=k)
        if k == 1:
            neighbors = neighbors[:, None]
        best = np.full(len(points), np.inf, dtype=np.float64)
        for col in range(k):
            idx = neighbors[:, col]
            best = np.minimum(best, _segment_distance(points, seg_a[idx], seg_b[idx]))
        return best
    # dim == 2: dense surface sampling across reference face triangles
    samples = []
    bary = np.array([
        [1/3, 1/3, 1/3], [1, 0, 0], [0, 1, 0], [0, 0, 1],
        [0.5, 0.5, 0], [0.5, 0, 0.5], [0, 0.5, 0.5],
        [0.6, 0.2, 0.2], [0.2, 0.6, 0.2], [0.2, 0.2, 0.6],
    ], dtype=np.float64)
    for tag in tags:
        node_tags, coords, _ = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True)
        if not len(node_tags):
            raise ValueError(f'Reference face {tag} has no surface mesh nodes.')
        mapping = {int(t): xyz for t, xyz in zip(node_tags, np.asarray(coords, dtype=np.float64).reshape(-1, 3))}
        types, _, blocks = gmsh.model.mesh.getElements(2, tag)
        tris = []
        for kind, block in zip(types, blocks):
            _, _, _, nodes, _, primary = gmsh.model.mesh.getElementProperties(kind)
            if primary not in (3, 4):
                continue
            for row in np.asarray(block).reshape(-1, nodes):
                indices = [(0, 1, 2)] if primary == 3 else [(0, 1, 2), (0, 2, 3)]
                for ids in indices:
                    tris.append([mapping[int(row[i])] for i in ids])
        if tris:
            tri_arr = np.asarray(tris, dtype=np.float64)
            pts = np.einsum('kb,nbj->nkj', bary, tri_arr).reshape(-1, 3)
            samples.append(pts)
        else:
            samples.append(np.asarray(list(mapping.values()), dtype=np.float64))
    cloud = np.concatenate(samples, axis=0)
    tree = cKDTree(cloud)
    distances, _ = tree.query(points, k=1)
    return np.asarray(distances, dtype=np.float64)


def refinement_density(distances, domain_scale, bias, decay):
    """Evaluate unnormalized importance density q(d) = 1 + bias * exp(-d / (decay * L))."""
    distances = np.asarray(distances, dtype=np.float64)
    scale = max(float(domain_scale) * float(decay), 1e-12)
    return 1.0 + float(bias) * np.exp(-np.clip(distances / scale, 0.0, 700.0))


def validate_distances(batch, *, distances=None):
    """Reject malformed, negative or non-finite boundary distance arrays."""
    if 'boundary_distances' not in batch and distances is None:
        if batch.get('distance_status') == 'available':
            raise ValueError('Available boundary distances are missing their numeric array.')
        return None
    distances = np.asarray(batch['boundary_distances'] if distances is None else distances, dtype=np.float64)
    if (distances.shape != (batch['count'],) or not np.isfinite(distances).all()
            or (distances < 0).any()):
        raise ValueError('Boundary distances must be finite non-negative numbers, one per point.')
    return distances


def transport_distances(batch, linear, affected_domain):
    """Transport boundary distances under x' = A x + b."""
    distances = validate_distances(batch)
    if distances is None:
        return {}
    linear = np.asarray(linear, dtype=np.float64)
    gram = linear.T @ linear
    scale2 = float(gram[0, 0])
    is_uniform = scale2 > 0 and np.allclose(gram, scale2 * np.eye(3), rtol=1e-6, atol=1e-8)
    if affected_domain and is_uniform:
        factor = math.sqrt(scale2)
        scaled = distances * factor
        if not np.isfinite(scaled).all():
            raise ValueError('Transform produced non-finite boundary distances.')
        return {
            'boundary_distances': scaled.tolist(),
            'distance_status': 'available',
        }
    return {
        'boundary_distances': None,
        'distance_status': 'unavailable',
        'distance_note': ('Non-uniform scaling or independent region movement changes '
                          'Euclidean boundary distances. No guessed distances are exported.'),
    }
