"""Reproducible sampling engines and a Gmsh adapter, independent of the GUI."""
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path

import numpy as np

from .sampling_methods import METHODS, RESOLUTIONS


def radical_inverse(indices, base):
    indices = np.asarray(indices, dtype=np.int64).copy()
    values = np.zeros(len(indices))
    factor = 1. / base
    while np.any(indices):
        values += (indices % base) * factor
        indices //= base
        factor /= base
    return values


def unit_points(method, count, dimension, seed):
    """Design points in a unit cube. Grid retains complete Cartesian layers."""
    if method not in METHODS or not 1 <= count <= 500000 or not 1 <= dimension <= 4:
        raise ValueError('Invalid sampling method, count or dimension.')
    if method in ('lhs','sobol','halton'):
        from scipy.stats import qmc
    rng = np.random.default_rng(seed)
    if method == 'random':
        return rng.random((count, dimension))
    if method == 'lhs':
        return qmc.LatinHypercube(dimension, rng=rng).random(count)
    if method == 'sobol':
        return qmc.Sobol(dimension, scramble=True, rng=rng).random_base2(math.ceil(math.log2(count)))[:count]
    if method == 'halton':
        return qmc.Halton(dimension, scramble=True, rng=rng).random(count)
    if method == 'hammersley':
        result = np.empty((count, dimension))
        result[:, 0] = (np.arange(count) + .5) / count
        for axis, base in enumerate([2, 3, 5][:dimension-1], 1):
            result[:, axis] = radical_inverse(np.arange(count), base)
        return (result + rng.random(dimension)) % 1
    side = max(1, int(count ** (1 / dimension)))
    while (side + 1) ** dimension <= count:
        side += 1
    if method == 'grid':
        cells = np.array(list(itertools.product(range(side), repeat=dimension)), dtype=float)
        return (cells + .5) / side
    # Equal-volume recursive partitioning produces exactly N distinct strata.
    leaves = [(np.zeros(dimension), np.ones(dimension), count)]
    output = []
    while leaves:
        low, high, number = leaves.pop()
        if number == 1:
            output.append(low + rng.random(dimension) * (high-low))
            continue
        axis = int(np.argmax(high-low))
        left = number // 2
        middle = low[axis] + (high[axis]-low[axis]) * left / number
        upper, lower = high.copy(), low.copy()
        upper[axis], lower[axis] = middle, middle
        leaves.extend([(lower, high.copy(), number-left), (low.copy(), upper, left)])
    return np.array(output)


def _choose(values, weights):
    cdf = np.cumsum(weights / weights.sum())
    cdf[-1] = 1.
    indices = np.searchsorted(cdf, values, side='right')
    previous = np.r_[0., cdf[:-1]]
    local = (values - previous[indices]) / (cdf[indices] - previous[indices])
    return indices, local


def _edge_samples(gmsh, tags, method, count, seed):
    tables, lengths = [], []
    for tag in tags:
        low, high = gmsh.model.getParametrizationBounds(1, tag)
        parameters = np.linspace(low[0], high[0], 8193)
        xyz = np.array(gmsh.model.getValue(1, tag, parameters)).reshape(-1, 3)
        arc = np.r_[0., np.cumsum(np.linalg.norm(np.diff(xyz, axis=0), axis=1))]
        if not arc[-1] > 0:
            raise ValueError(f'Edge {tag} has zero length.')
        tables.append((parameters, arc));lengths.append(arc[-1])
    values = unit_points(method, count, 1, seed)[:, 0]
    indices, local = _choose(values, np.array(lengths))
    output = np.empty((len(values), 3));entity_tags = np.empty(len(values), dtype=int)
    for i, tag in enumerate(tags):
        mask = indices == i
        parameters, arc = tables[i]
        t = np.interp(local[mask]*arc[-1], arc, parameters)
        output[mask] = np.array(gmsh.model.getValue(1, tag, t)).reshape(-1, 3)
        entity_tags[mask] = tag
    return output, entity_tags, ['Edge spacing uses numerical arc-length inversion; coordinates are evaluated on the original curves.']


def _face_triangles(gmsh, tag):
    tags, _, params = gmsh.model.mesh.getNodes(2, tag, includeBoundary=True, returnParametricCoord=True)
    mapping = {int(t): uv for t, uv in zip(tags, np.array(params).reshape(-1, 2))}
    result = []
    types, _, blocks = gmsh.model.mesh.getElements(2, tag)
    for kind, block in zip(types, blocks):
        _, _, _, nodes, _, primary = gmsh.model.mesh.getElementProperties(kind)
        if primary not in (3, 4):
            raise ValueError('Surface sampling requires triangular or quadrilateral surface elements.')
        for row in np.array(block).reshape(-1, nodes):
            indices = [(0, 1, 2)] if primary == 3 else [(0, 1, 2), (0, 2, 3)]
            result.extend([[mapping[int(row[i])] for i in ids] for ids in indices])
    return np.array(result)


def _jacobian(gmsh, tag, uv):
    derivatives = np.array(gmsh.model.getDerivative(2, tag, np.asarray(uv).ravel())).reshape(-1, 2, 3)
    return np.linalg.norm(np.cross(derivatives[:, 0], derivatives[:, 1]), axis=1)


def _surface_samples(gmsh, tags, method, count, seed):
    triangles, owners, bounds = [], [], []
    for tag in tags:
        tri = _face_triangles(gmsh, tag)
        a, b = tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]
        area = np.abs(a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0]) / 2
        tri, area = tri[area > 1e-16], area[area > 1e-16]
        if not len(tri):
            raise ValueError(f'Face {tag} has no valid parameter triangles.')
        # Estimate an envelope with a generous margin; fail if it is exceeded.
        probes = np.concatenate([tri.mean(axis=1), *(tri[:, i]*.98+tri.mean(axis=1)*.02 for i in range(3))])
        jac = _jacobian(gmsh, tag, probes).reshape(4, -1)
        envelope = np.max(jac, axis=0)*2
        triangles.extend(tri);owners.extend([tag]*len(tri));bounds.extend(zip(area, envelope))
    triangles, owners, bounds = np.array(triangles), np.array(owners), np.array(bounds)
    if not np.all(np.isfinite(bounds)) or bounds[:, 1].sum() <= 0:
        raise ValueError('The surface parameterization has invalid derivatives.')
    output, output_tags = [], []
    warnings = ['Surface trimming uses a separate parameter-space tessellation; curved trim boundaries are numerically approximated.']
    if method == 'grid':
        # Complete parameter lattices, clipped against the trimmed surface triangles.
        per_face = max(1, count // len(tags))
        for tag in tags:
            tri = triangles[owners == tag]
            low, high = tri.reshape(-1, 2).min(axis=0), tri.reshape(-1, 2).max(axis=0)
            candidates = low + unit_points('grid', per_face, 2, seed)*(high-low)
            matrix = np.stack([tri[:, 1]-tri[:, 0], tri[:, 2]-tri[:, 0]], axis=-1)
            inverse = np.linalg.inv(matrix)
            for uv in candidates:
                bary = np.einsum('nij,nj->ni', inverse, uv-tri[:, 0])
                if not np.any((bary >= -1e-10).all(axis=1) & (bary.sum(axis=1) <= 1+1e-10)):
                    continue
                xyz = gmsh.model.getValue(2, tag, uv)
                if gmsh.model.getType(2, tag) == 'Plane' and not gmsh.model.isInside(2, tag, xyz):
                    continue
                output.append(xyz);output_tags.append(tag)
        warnings.append('Grid is regular in surface parameters; curved surfaces do not have constant physical grid spacing. Clipping changes the point count.')
        return np.array(output).reshape(-1, 3), np.array(output_tags), warnings
    # Candidate designs are transformed through a measure-weighted distribution.
    # Rejection changes the strict LHS/stratification and Sobol balance properties.
    refinements = 0
    for _ in range(5):
        draws = max(256, count*4)
        exceeded = False
        for attempt in range(8):
            unit = unit_points(method, draws, 4, seed+attempt)
            indices, _ = _choose(unit[:, 0], bounds[:, 0]*bounds[:, 1])
            tri = triangles[indices]
            r = np.sqrt(unit[:, 1])
            uv = (1-r[:, None])*tri[:, 0] + (r*(1-unit[:, 2]))[:, None]*tri[:, 1] + (r*unit[:, 2])[:, None]*tri[:, 2]
            candidate_tags = owners[indices]
            for tag in tags:
                mask = candidate_tags == tag
                local_uv, local_indices = uv[mask], indices[mask]
                if not len(local_uv):
                    continue
                jac = _jacobian(gmsh, tag, local_uv)
                over = jac > bounds[local_indices, 1]*(1+1e-8)
                if not np.isfinite(jac).all() or (np.any(over) and refinements >= 4):
                    raise ValueError('Surface curvature exceeded the numerical sampling envelope. No dataset was produced.')
                if np.any(over):
                    np.maximum.at(bounds[:, 1], local_indices[over], jac[over]*2)
                    refinements += 1
                    output.clear();output_tags.clear()
                    exceeded = True
                    break
                accepted = unit[mask, 3] < jac / bounds[local_indices, 1]
                coords = np.array(gmsh.model.getValue(2, tag, local_uv[accepted].ravel())).reshape(-1, 3)
                if gmsh.model.getType(2, tag) == 'Plane':
                    coords = np.array([p for p in coords if gmsh.model.isInside(2, tag, p)]).reshape(-1, 3)
                output.extend(coords);output_tags.extend([tag]*len(coords))
            if exceeded:
                break
            if len(output) >= count:
                # Shuffle before truncation: avoid favouring the first face in a batch.
                order = np.random.default_rng(seed).permutation(len(output))[:count]
                return np.array(output)[order], np.array(output_tags)[order], warnings
            draws = min(500000, max(256, (count-len(output))*6))
        if not exceeded:
            break
    raise ValueError('Unable to generate the requested surface samples within the candidate limit.')


def _volume_samples(gmsh, tags, method, count, seed):
    node_tags, coords, _ = gmsh.model.mesh.getNodes()
    node_order = np.argsort(node_tags)
    sorted_tags = np.asarray(node_tags)[node_order]
    positions = np.asarray(coords).reshape(-1,3)[node_order]
    tetra, owners = [], []
    for tag in tags:
        types, _, blocks = gmsh.model.mesh.getElements(3, tag)
        for kind, block in zip(types, blocks):
            _, _, _, nodes, _, primary = gmsh.model.mesh.getElementProperties(kind)
            if primary != 4:
                raise ValueError('Interior sampling currently requires tetrahedral volume elements.')
            rows = np.asarray(block).reshape(-1,nodes)[:,:4]
            tetra.append(positions[np.searchsorted(sorted_tags,rows)])
            owners.append(np.full(len(rows),tag,dtype=int))
    tetra = np.concatenate(tetra) if tetra else np.empty((0,4,3))
    owners = np.concatenate(owners) if owners else np.empty(0,dtype=int)
    if not len(tetra):
        raise ValueError('No tetrahedra were generated for the volume.')
    matrix = np.stack([tetra[:, i]-tetra[:, 0] for i in (1, 2, 3)], axis=-1)
    volume = np.abs(np.linalg.det(matrix))/6
    valid = volume > 0
    tetra, owners, volume = tetra[valid], owners[valid], volume[valid]
    if method == 'grid':
        low, high = tetra.reshape(-1, 3).min(axis=0), tetra.reshape(-1, 3).max(axis=0)
        points, point_tags = [], []
        for point in low + unit_points('grid', count, 3, seed)*(high-low):
            try:
                element, _, _, _, _, _ = gmsh.model.mesh.getElementByCoordinates(*point, dim=3, strict=True)
                _, _, dimension, tag = gmsh.model.mesh.getElement(element)
                if dimension == 3 and tag in tags:
                    points.append(point);point_tags.append(tag)
            except Exception:
                pass
        return np.array(points).reshape(-1, 3), np.array(point_tags), ['Volume interior is approximated by an independent tetrahedral mesh. Grid clipping changes the point count.']
    unit = unit_points(method, count, 4, seed)
    indices, _ = _choose(unit[:, 0], volume)
    r, s, t = unit[:, 1]**(1/3), np.sqrt(unit[:, 2]), unit[:, 3]
    weights = np.column_stack([1-r, r*(1-s), r*s*(1-t), r*s*t])
    points = np.einsum('ni,nij->nj', weights, tetra[indices])
    return points, owners[indices], ['Volume interior is approximated by an independent tetrahedral mesh; curved boundaries have finite geometric tolerance.']


def sample_geometry(request):
    import gmsh
    path = Path(request['source']).resolve()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != request['source_hash']:
        raise ValueError('Geometry source changed. Reopen it before sampling.')
    method, count, seed = request['method'], request['count'], request['seed']
    if method not in METHODS or not isinstance(count, int) or not 1 <= count <= 50000 or not isinstance(seed, int) or not 0 <= seed < 2**32:
        raise ValueError('Invalid sampling settings.')
    mode = request.get('compute_mode','reproducible')
    threads = request.get('cpu_threads',1)
    if mode not in ('multicore','reproducible') or isinstance(threads,bool) or not isinstance(threads,int) or not 1<=threads<=64:
        raise ValueError('Invalid CPU sampling settings.')
    threads = threads if mode=='multicore' else 1
    gmsh.initialize(['jorvik'], readConfigFiles=False)
    try:
        gmsh.option.setNumber('General.Terminal', 0)
        from .cad_import import open_source, import_options
        source_format = open_source(gmsh, path)
        options = import_options(gmsh, source_format)
        if request.get("import_options", options) != options:
            raise ValueError("Geometry import settings changed. Reopen the geometry before sampling.")
        gmsh.option.setNumber('General.NumThreads', threads)
        for axis in (1,2,3):
            gmsh.option.setNumber(f'Mesh.MaxNumThreads{axis}D',threads)
        gmsh.option.setNumber('Mesh.Reproducible',1 if mode=='multicore' else 0)
        gmsh.option.setNumber('Mesh.RandomSeed', 1)
        from .domains import domain_kind
        interior_domain = domain_kind(gmsh.model, source_format)
        if request['target'] == 'interior':
            if interior_domain == 'open_or_nonplanar':
                if source_format != 'geo':
                    raise ValueError('Interior sampling requires solid volumes or a coplanar planar sheet. This CAD file imported as open or non-planar surfaces only; sample its faces or supply a closed solid model.')
                raise ValueError('Interior sampling requires a closed solid or a coplanar analytic-plane .geo domain. This model has open or non-planar surfaces; sample its faces or provide a valid solid.')
            dim = 3 if interior_domain == 'solid' else 2
            tags = [t for _, t in gmsh.model.getEntities(dim)]
        else:
            entities = sorted(set(tuple(e) for e in request['entities']))
            if not entities or len({e[0] for e in entities}) != 1:
                raise ValueError('Select faces or edges of one dimension before generating samples.')
            dim = entities[0][0]
            if dim not in (1, 2):
                raise ValueError('Only faces and edges can be sampled as selected regions.')
            tags = [t for _, t in entities]
        existing = {t for _, t in gmsh.model.getEntities(dim)}
        if not tags or not set(tags) <= existing:
            raise ValueError('Sampling target is absent from this geometry.')
        if dim >= 2:
            # Independent mesh controls: never use the viewport's display triangles.
            resolution = request.get('resolution', 32)
            if resolution not in RESOLUTIONS:
                raise ValueError('Unsupported geometry accuracy setting.')
            bounds = gmsh.model.getBoundingBox(-1, -1)
            extent = max(bounds[i+3]-bounds[i] for i in range(3))
            gmsh.option.setNumber('Mesh.MeshSizeMax', extent / request.get('resolution', 32))
            gmsh.option.setNumber('Mesh.ElementOrder', 1)
            gmsh.option.setNumber('Mesh.RecombineAll', 0)
            if mode=='multicore' and dim==3:
                gmsh.option.setNumber('Mesh.Algorithm3D',10)  # Gmsh's parallel HXT mesher.
            try:
                gmsh.model.mesh.generate(dim)
                gmsh.model.mesh.setOrder(1)
            except Exception as error:
                if dim == 3:
                    raise ValueError('Could not mesh a closed solid domain; check open boundaries or invalid topology: ' + str(error)) from error
                raise
        if dim == 1:
            occ_edges = set(gmsh.model.occ.getEntities(1))
            if occ_edges:
                bounds = gmsh.model.getBoundingBox(-1, -1)
                scale = max(bounds[i+3]-bounds[i] for i in range(3))
                for tag in tags:
                    if (1, tag) in occ_edges and gmsh.model.occ.getMass(1, tag) <= max(scale, 1e-12)*1e-12:
                        raise ValueError(f'Edge {tag} is a degenerate CAD edge with zero length; choose another edge.')
            points, point_tags, notes = _edge_samples(gmsh, tags, method, count, seed)
        elif dim == 2:
            points, point_tags, notes = _surface_samples(gmsh, tags, method, count, seed)
        else:
            points, point_tags, notes = _volume_samples(gmsh, tags, method, count, seed)
        if not len(points):
            raise ValueError('No points survived geometry clipping. Increase the grid point budget.')
        if not np.isfinite(points).all():
            raise ValueError('Sampling returned invalid coordinates.')
        if method in ('lhs', 'sobol', 'halton', 'hammersley', 'stratified') and dim >= 2:
            notes.append('The method defines candidate-space coverage. Geometric transformation and rejection do not preserve strict Cartesian LHS, stratification, or Sobol balance guarantees.')
        if method == 'sobol' and count & (count-1):
            notes.append('Sobol designs have their strongest balance properties at power-of-two counts.')
        if mode=='multicore':
            notes.append('Multicore meshing can change the numerical mesh and coordinates across runs or thread counts; use Reproducible mode for repeatable meshes.')
        from .normals import boundary_normals
        normal_data = boundary_normals(gmsh, dim, points, point_tags, request['target'])
        if normal_data['normal_status'] == 'unavailable':
            notes.append(normal_data['normal_note'])
        result = {'document': request['document'], 'label': request['label'], 'target': request['target'],
                'entities': [[dim, t] for t in tags], 'method': method, 'seed': seed,
                'requested_count': count, 'count': len(points), 'coordinates': points.tolist(),
                'entity_tags': point_tags.astype(int).tolist(), 'entity_dimension': dim,
                'source': str(path), 'source_hash': digest, 'source_format': source_format, 'import_options': options, 'notes': notes,
                'resolution': request.get('resolution', 32), 'compute_mode':mode,
                'cpu_threads':threads, 'mesh_algorithm_2d':int(gmsh.option.getNumber('Mesh.Algorithm')),
                'mesh_algorithm_3d':int(gmsh.option.getNumber('Mesh.Algorithm3D'))}
        result['interior_domain'] = interior_domain
        result.update(normal_data)
        if 'boundary_region' in request:
            from .regions import validate_regions
            snapshot = request['boundary_region']
            model = {'source_hash': digest, 'document': request['document'], 'source_format': source_format,
                     'entities': [{'dimension': d, 'tag': t} for d, t in gmsh.model.getEntities()]}
            validate_regions([snapshot], model)
            if sorted(snapshot['entities']) != sorted(result['entities']):
                raise ValueError('Boundary region differs from the sampling target.')
            result['boundary_region'] = snapshot
        return result
    finally:
        gmsh.finalize()


if __name__ == '__main__':
    try:
        print(json.dumps(sample_geometry(json.load(sys.stdin)), allow_nan=False))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
