"""Gmsh import adapter. Run in a separate process to isolate kernel state."""
import hashlib
import json
import math
import sys
from pathlib import Path
from uuid import uuid4
from .cad_import import open_source, import_options, detect_coordinate_units
from .cad_regeneration import discover_geo_parameters
from .domains import domain_kind


def extract_topology(model, groups):
    """Capture original CAD entities without retaining backend objects or meshes."""
    records = []
    for dim, tag in model.getEntities():
        upward, downward = model.getAdjacencies(dim, tag)
        records.append({
            'dimension': int(dim), 'tag': int(tag),
            'type': model.getType(dim, tag), 'name': model.getEntityName(dim, tag),
            'bounds': list(map(float, model.getBoundingBox(dim, tag))),
            'boundary': [[dim - 1, int(t)] for t in sorted(set(downward))],
            'upward': [[dim + 1, int(t)] for t in sorted(set(upward))],
            'groups': [g['tag'] for g in groups if g['dimension'] == dim and tag in g['entities']],
        })
    return records


def load_geometry(filename):
    import gmsh
    path = Path(filename).resolve()
    if not path.is_file():
        raise ValueError("Geometry file does not exist.")
    gmsh.initialize(["jorvik"], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        source_format = open_source(gmsh, path)
        occ_edges = set(gmsh.model.occ.getEntities(1))
        faces = gmsh.model.getEntities(2)
        edges = gmsh.model.getEntities(1)
        if not faces:
            raise ValueError("This geometry has no faces to display.")
        interior_domain = domain_kind(gmsh.model, source_format)
        gmsh.option.setNumber("Mesh.ElementOrder", 1)
        gmsh.model.mesh.generate(2)
        gmsh.model.mesh.setOrder(1)
        tags, coords, _ = gmsh.model.mesh.getNodes()
        positions = {int(tag): [float(v) for v in coords[3*i:3*i+3]] for i, tag in enumerate(tags)}
        if any(not math.isfinite(v) for p in positions.values() for v in p):
            raise ValueError("Geometry contains non-finite coordinates.")
        groups = [{"dimension": d, "tag": t, "name": gmsh.model.getPhysicalName(d, t),
                   "entities": list(map(int, gmsh.model.getEntitiesForPhysicalGroup(d, t)))}
                  for d, t in gmsh.model.getPhysicalGroups()]
        topology = extract_topology(gmsh.model, groups)
        entities = []
        for dim, tag in [*faces, *edges]:
            vertices = []
            types, _, blocks = gmsh.model.mesh.getElements(dim, tag)
            for kind, block in zip(types, blocks):
                name, _, order, count, _, primary = gmsh.model.mesh.getElementProperties(kind)
                if (dim == 1 and primary != 2) or (dim == 2 and primary not in (3, 4)):
                    raise ValueError(f"Unsupported display element: {name}")
                for offset in range(0, len(block), count):
                    indices = [0, 1] if dim == 1 else ([0, 1, 2] if primary == 3 else [0, 1, 2, 0, 2, 3])
                    for i in indices:
                        vertices.extend(positions[int(block[offset+i])])
            degenerate = False
            if not vertices:
                bounds = gmsh.model.getBoundingBox(-1, -1)
                scale = max(bounds[i+3] - bounds[i] for i in range(3))
                degenerate = (dim == 1 and (dim, tag) in occ_edges
                              and gmsh.model.occ.getMass(dim, tag) <= max(scale, 1e-12) * 1e-12)
                if not degenerate:
                    raise ValueError(f"Entity ({dim}, {tag}) has no display elements.")
            entities.append({"dimension": dim, "tag": tag, "type": gmsh.model.getType(dim, tag),
                             "name": gmsh.model.getEntityName(dim, tag), "degenerate": degenerate,
                             "boundary": [list(e) for e in gmsh.model.getBoundary([(dim, tag)], oriented=False)],
                             "groups": [g["tag"] for g in groups if g["dimension"] == dim and tag in g["entities"]],
                             "positions": vertices})
        geometry = {"document": uuid4().hex, "source": str(path), "name": path.name,
                "dimension": 2 if interior_domain == "planar" else 3,
                "interior_domain": interior_domain,
                "source_format": source_format, "import_options": import_options(gmsh, source_format),
                "solid_count": len(gmsh.model.getEntities(3)),
                "coordinate_units": detect_coordinate_units(path, source_format),
                "source_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
                "entities": entities, "groups": groups, "topology": topology}
        if source_format == "geo":
            geo_params = discover_geo_parameters(path)
            if geo_params:
                geometry["geo_parameters"] = geo_params
        from .topology import validate_topology
        validate_topology(geometry)
        return geometry
    finally:
        gmsh.finalize()


if __name__ == "__main__":
    try:
        print(json.dumps(load_geometry(sys.argv[1]), allow_nan=False))
    except Exception as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
