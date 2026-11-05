"""Geometry-evaluated boundary normals with explicit orientation checks.

Containment is used only to orient derivatives, never to fabricate a normal for
open shells, shared internal interfaces or interior samples.
"""
import numpy as np


def _unavailable(reason, status='unavailable'):
    return {'normal_status': status, 'normal_note': reason}


def boundary_normals(gmsh, dimension, points, point_tags, target):
    if target == 'interior':
        return _unavailable('Interior samples have no outward boundary normal.', 'not_applicable')
    if dimension == 1 and gmsh.model.getEntities(3):
        return _unavailable('A 3D edge has no unique outward surface normal.', 'not_applicable')
    if dimension not in (1, 2):
        return _unavailable('Normals require a codimension-one boundary.', 'not_applicable')
    points, point_tags = np.asarray(points), np.asarray(point_tags)
    parents = {}
    for tag in np.unique(point_tags):
        upward, _ = gmsh.model.getAdjacencies(dimension, int(tag))
        if len(upward) != 1:
            return _unavailable('Open or shared boundary: exactly one parent domain is required for an outward normal.')
        parent = int(upward[0])
        if dimension == 1 and gmsh.model.getType(2, parent) != 'Plane':
            return _unavailable('Edge co-normals are currently supported only for planar domains.')
        parents[int(tag)] = parent
    parent_dim = dimension + 1
    occ_parents = {tag for dim, tag in gmsh.model.occ.getEntities(parent_dim)}
    mesh_parents = set(parents.values()) - occ_parents if dimension == 2 else set()
    try:
        if mesh_parents:
            # Built-in volumes do not implement isInside(3). The separate volume
            # mesh validates orientation numerically, including holes/cavities.
            gmsh.model.mesh.generate(3)
            gmsh.model.mesh.setOrder(1)
        normals = np.empty_like(points, dtype=float)
        planes = {}
        for tag, parent in parents.items():
            mask = point_tags == tag
            xyz = points[mask]
            parameters = gmsh.model.getParametrization(dimension, tag, xyz.ravel())
            if dimension == 2:
                candidate = np.asarray(gmsh.model.getNormal(tag, parameters)).reshape(-1, 3)
            else:
                tangent = np.asarray(gmsh.model.getDerivative(1, tag, parameters)).reshape(-1, 3)
                uv = gmsh.model.getParametrization(2, parent, xyz.ravel())
                surface = np.asarray(gmsh.model.getNormal(parent, uv)).reshape(-1, 3)
                surface_lengths = np.linalg.norm(surface, axis=1, keepdims=True)
                if not np.isfinite(surface).all() or np.any(surface_lengths <= 0):
                    return _unavailable('The geometry returned an invalid parent plane normal.')
                surface = surface / surface_lengths
                if not np.allclose(np.abs(surface @ surface[0]), 1., rtol=1e-6, atol=1e-8):
                    return _unavailable('The parent domain does not have a consistent plane normal.')
                planes[str(tag)] = surface[0].tolist()
                candidate = np.cross(tangent, surface)
            length = np.linalg.norm(candidate, axis=1)
            if not np.isfinite(candidate).all() or np.any(length <= 0):
                return _unavailable('The geometry returned singular or non-finite boundary derivatives.')
            candidate = candidate / length[:, None]
            bounds = gmsh.model.getBoundingBox(parent_dim, parent)
            scale = max(bounds[i+3] - bounds[i] for i in range(3))
            if not np.isfinite(scale) or scale <= 0:
                return _unavailable('The parent domain has invalid bounds.')
            use_mesh = parent in mesh_parents

            def inside(point):
                if not use_mesh:
                    return bool(gmsh.model.isInside(parent_dim, parent, point))
                try:
                    elements = gmsh.model.mesh.getElementsByCoordinates(*point, dim=3, strict=True)
                except Exception as error:
                    if 'No element found at' in str(error):
                        return False
                    raise
                return any(gmsh.model.mesh.getElement(int(e))[3] == parent for e in elements)

            # Resolve the smallest usable probe first. For built-in volumes,
            # larger probes accommodate the separate mesh's curved approximation.
            factors = (1e-6, 1e-5, 1e-4, 1e-3, 1e-2)
            for i, (point, direction) in enumerate(zip(xyz, candidate)):
                for factor in factors:
                    offset = scale * factor * direction
                    plus, minus = inside(point + offset), inside(point - offset)
                    if plus != minus:
                        if plus: candidate[i] *= -1
                        break
                else:
                    return _unavailable('Containment probes could not verify every outward normal; no partial or guessed normals are returned.')
            normals[mask] = candidate
        result = {'normals': normals.tolist(), 'normal_status': 'available',
                'normal_kind': 'outward_surface' if dimension == 2 else 'outward_conormal',
                'normal_validation': 'tetrahedral containment probes (numerical)' if mesh_parents else 'CAD containment probes'}
        if dimension == 1:
            result['normal_planes'] = planes
        return result
    except Exception as error:
        return _unavailable('Normal orientation could not be verified: ' + str(error))


def validate_normals(batch):
    """Reject malformed arrays before exporting or restoring generated datasets."""
    if 'normal_planes' in batch and 'normals' not in batch:
        raise ValueError('Parent plane frames have missing boundary normals.')
    if 'normals' not in batch:
        if batch.get('normal_status') == 'available':
            raise ValueError('Available boundary normals are missing their numeric array.')
        return None
    normals = np.asarray(batch['normals'], dtype=float)
    if (normals.shape != (batch['count'], 3) or not np.isfinite(normals).all()
            or not np.allclose(np.linalg.norm(normals, axis=1), 1, rtol=1e-6, atol=1e-8)):
        raise ValueError('Boundary normals must be finite unit vectors, one per point.')
    if 'normal_planes' in batch:
        planes = batch['normal_planes']
        tags = np.asarray(batch['entity_tags'])
        if (batch.get('entity_dimension') != 1 or batch.get('normal_kind') != 'outward_conormal'
                or not isinstance(planes, dict) or tags.shape != (batch['count'],)
                or set(planes) != {str(tag) for tag in np.unique(tags)}):
            raise ValueError('Parent plane frames must match the sampled edge tags.')
        for tag, values in planes.items():
            plane = np.asarray(values, dtype=float)
            if (plane.shape != (3,) or not np.isfinite(plane).all()
                    or not np.isclose(np.linalg.norm(plane), 1., rtol=1e-6, atol=1e-8)):
                raise ValueError('Parent plane normals must be finite unit vectors.')
            if not np.allclose(normals[tags == int(tag)] @ plane, 0., atol=1e-6):
                raise ValueError('Boundary co-normals must lie in their parent plane.')
    return normals
