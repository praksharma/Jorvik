"""Shared source loading for display and sampling; independent of Qt."""
from copy import deepcopy
import hashlib
from pathlib import Path
import os
import re
import sys

FORMATS = {'.geo': 'geo', '.step': 'step', '.stp': 'step',
           '.brep': 'brep', '.iges': 'iges', '.igs': 'iges'}
UNSUPPORTED_TRANSLATORS = {
    '.stl': 'STL is a triangle-only mesh without B-Rep topology; export STEP, BREP or IGES instead.',
    '.x_t': 'Parasolid (.x_t) is not imported directly; convert to STEP or BREP.',
    '.x_b': 'Parasolid (.x_b) is not imported directly; convert to STEP or BREP.',
    '.sat': 'ACIS (.sat) is not imported directly; convert to STEP or BREP.',
    '.sab': 'ACIS (.sab) is not imported directly; convert to STEP or BREP.',
    '.3dm': 'Rhino (.3dm) is not imported directly; export STEP or IGES from Rhino.',
}
FILE_FILTER = 'Supported geometry (*.geo *.step *.stp *.brep *.iges *.igs);;Gmsh (*.geo);;STEP (*.step *.stp);;Open CASCADE BREP (*.brep);;IGES (*.iges *.igs)'
DEFAULT_UNITS = 'Source geometry units (unspecified)'

_COMMENT_BLOCK = re.compile(r'/\*.*?\*/', re.DOTALL)
_COMMENT_LINE = re.compile(r'//[^\n]*')
_GEO_INCLUDE = re.compile(r'\b(?:Include|Merge)\s+"([^"]+)"\s*;')

_STEP_SI_PREFIX = {
    '.MILLI.': 'mm',
    '.CENTI.': 'cm',
    '$': 'm',
}
_IGES_UNIT_NAME = {
    'MM': 'mm', 'MILLIMETER': 'mm', 'MILLIMETERS': 'mm',
    'CM': 'cm', 'CENTIMETER': 'cm', 'CENTIMETERS': 'cm',
    'M': 'm', 'METER': 'm', 'METERS': 'm', 'METRE': 'm', 'METRES': 'm',
    'IN': 'in', 'INCH': 'in', 'INCHES': 'in',
    'FT': 'ft', 'FEET': 'ft', 'FOOT': 'ft',
}


def detect_coordinate_units(filename, source_format=None):
    """Detect unambiguous declared CAD length units without rescaling coordinates."""
    path = Path(filename)
    kind = source_format or FORMATS.get(path.suffix.lower())
    if kind not in ('step', 'iges') or not path.is_file():
        return DEFAULT_UNITS
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return DEFAULT_UNITS
    found = set()
    if kind == 'step':
        upper = text.upper()
        for match in re.finditer(r'SI_UNIT\s*\(\s*(\.[A-Z]+\.|\$)\s*,\s*\.METRE\.\s*\)', upper):
            unit = _STEP_SI_PREFIX.get(match.group(1))
            if unit:
                found.add(unit)
        if re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'INCH'", upper):
            found.add('in')
        elif re.search(r"CONVERSION_BASED_UNIT\s*\(\s*'FOOT'", upper):
            found.add('ft')
        if len(found) == 1:
            return next(iter(found))
        return DEFAULT_UNITS
    # IGES Global section: inspect lines with 'G' in column 73 (or standard Hollerith unit tokens)
    global_lines = [line[:72] for line in text.splitlines() if len(line) >= 73 and line[72] == 'G']
    header = ''.join(global_lines) if global_lines else text[:4096]
    for match in re.finditer(r',\s*\d+H([A-Za-z]+)\s*,', header):
        token = match.group(1).upper()
        if token in _IGES_UNIT_NAME:
            found.add(_IGES_UNIT_NAME[token])
    if len(found) == 1:
        return next(iter(found))
    return DEFAULT_UNITS


def detect_cad_header_metadata(filename, source_format=None):
    """Extract STEP application protocol (AP203/AP214/AP242), originating system and units."""
    path = Path(filename)
    kind = source_format or FORMATS.get(path.suffix.lower())
    units = detect_coordinate_units(path, kind)
    info = {'protocol': None, 'originating_system': None, 'units': units}
    if kind not in ('step', 'iges') or not path.is_file():
        return info
    try:
        text = path.read_text(encoding='utf-8', errors='replace')[:16384]
    except OSError:
        return info
    if kind == 'step':
        schema_match = re.search(r"FILE_SCHEMA\s*\(\s*\(\s*'([^']+)'", text, re.IGNORECASE)
        if schema_match:
            raw_schema = schema_match.group(1).strip()
            upper_schema = raw_schema.upper()
            if 'AP242' in upper_schema or 'MANAGED_MODEL_BASED_3D_ENGINEERING' in upper_schema:
                info['protocol'] = 'AP242'
            elif 'AP214' in upper_schema or 'AUTOMOTIVE_DESIGN' in upper_schema:
                info['protocol'] = 'AP214'
            elif 'AP203' in upper_schema or 'CONFIG_CONTROL_DESIGN' in upper_schema:
                info['protocol'] = 'AP203'
            else:
                info['protocol'] = raw_schema
        fn_match = re.search(
            r"FILE_NAME\s*\(\s*'[^']*'\s*,\s*'[^']*'\s*,\s*\([^)]*\)\s*,\s*\([^)]*\)\s*,\s*'([^']*)'\s*,\s*'([^']*)'",
            text, re.IGNORECASE | re.DOTALL,
        )
        if fn_match:
            preprocessor, originating = fn_match.group(1).strip(), fn_match.group(2).strip()
            info['originating_system'] = originating or preprocessor or None
    elif kind == 'iges':
        info['protocol'] = 'IGES'
        global_lines = [line[:72] for line in text.splitlines() if len(line) >= 73 and line[72] == 'G']
        header = ''.join(global_lines)
        tokens = [m.group(1).strip() for m in re.finditer(r'\d+H([^,;]+)', header)]
        if len(tokens) >= 3 and tokens[2]:
            info['originating_system'] = tokens[2]
    return info


def extract_entity_metadata(model):
    """Extract CAD entity names, explicit RGBA colours and body names separately from Gmsh physical groups."""
    colours = {}
    named_count = 0
    body_names = {}
    for dim, tag in model.getEntities():
        name = model.getEntityName(dim, tag)
        if name:
            named_count += 1
            if dim == 3:
                body_names[str(tag)] = name
        try:
            r, g, b, a = model.getColor(dim, tag)
            # Gmsh returns (0, 0, 255, 0) with alpha=0 when no explicit CAD colour is assigned.
            if int(a) > 0:
                colours[(int(dim), int(tag))] = f'#{int(r):02x}{int(g):02x}{int(b):02x}'
        except Exception:
            pass
    return colours, {
        'named_entity_count': named_count,
        'coloured_entity_count': len(colours),
        'body_names': body_names,
    }


def discover_geo_dependencies(filename):
    """Recursively discover relative Include/Merge dependencies of a .geo script with SHA-256 checksums."""
    root_path = Path(filename).resolve()
    if root_path.suffix.lower() != '.geo' or not root_path.is_file():
        return []
    root_dir = root_path.parent
    visited = []
    seen = set()
    active_stack = set()

    def visit(current):
        active_stack.add(current)
        try:
            text = current.read_text(encoding='utf-8', errors='replace')
        except OSError as error:
            raise ValueError(f'Could not read .geo file {current.name}: {error}') from error
        stripped = _COMMENT_LINE.sub('', _COMMENT_BLOCK.sub('', text))
        for match in _GEO_INCLUDE.finditer(stripped):
            raw_ref = match.group(1).strip()
            dep_path = (current.parent / raw_ref).resolve()
            try:
                rel_posix = dep_path.relative_to(root_dir).as_posix()
            except ValueError as error:
                raise ValueError(f'.geo dependency {raw_ref!r} escapes the source geometry folder.') from error
            if not dep_path.is_file():
                raise ValueError(f'Included .geo dependency does not exist: {rel_posix}')
            if dep_path in active_stack or dep_path == root_path:
                raise ValueError(f'Circular .geo Include/Merge dependency detected: {rel_posix}')
            if rel_posix not in seen:
                seen.add(rel_posix)
                digest = hashlib.sha256(dep_path.read_bytes()).hexdigest()
                visited.append({'path': rel_posix, 'sha256': digest})
                if dep_path.suffix.lower() == '.geo':
                    visit(dep_path)
        active_stack.remove(current)

    visit(root_path)
    return visited


def heal_and_remap(filename, *, tolerance=1e-6, regions=None):
    """Explicitly evaluate OCC shape healing/sewing and remap region entities without silent tag reuse."""
    import gmsh
    import math
    if type(tolerance) is bool or type(tolerance) not in (int, float) or not math.isfinite(float(tolerance)) or float(tolerance) <= 0:
        raise ValueError('Healing tolerance must be a finite positive number.')
    tolerance = float(tolerance)
    path = Path(filename).resolve()
    kind = FORMATS.get(path.suffix.lower())
    if kind not in ('step', 'brep', 'iges'):
        raise ValueError('Optional OCC shape healing applies to STEP, BREP and IGES files.')
    gmsh.initialize(['jorvik'], readConfigFiles=False, interruptible=False)
    try:
        gmsh.option.setNumber('General.Terminal', 0)
        open_source(gmsh, path)
        def snapshot():
            items = {}
            for dim, tag in gmsh.model.getEntities():
                bounds = tuple(float(v) for v in gmsh.model.getBoundingBox(dim, tag))
                mass = float(gmsh.model.occ.getMass(dim, tag)) if dim >= 1 else 0.0
                com = tuple(float(v) for v in gmsh.model.occ.getCenterOfMass(dim, tag)) if dim >= 1 else bounds[:3]
                items[(int(dim), int(tag))] = {
                    'type': gmsh.model.getType(dim, tag),
                    'bounds': bounds,
                    'mass': mass,
                    'com': com,
                }
            return items
        before = snapshot()
        before_counts = {dim: sum(k[0] == dim for k in before) for dim in range(4)}
        gmsh.model.occ.healShapes(tolerance=tolerance)
        gmsh.model.occ.synchronize()
        after = snapshot()
        after_counts = {dim: sum(k[0] == dim for k in after) for dim in range(4)}
        entity_map = {}
        unmapped = []
        match_tol = max(tolerance * 100.0, 1e-4)
        for (dim, old_tag), old_info in sorted(before.items()):
            candidates = []
            for (new_dim, new_tag), new_info in after.items():
                if new_dim != dim:
                    continue
                com_dist = math.dist(old_info['com'], new_info['com'])
                bounds_diff = max(abs(a - b) for a, b in zip(old_info['bounds'], new_info['bounds']))
                mass_scale = max(abs(old_info['mass']), 1.0)
                mass_diff = abs(old_info['mass'] - new_info['mass']) / mass_scale
                if com_dist <= match_tol and bounds_diff <= match_tol and mass_diff <= 1e-2:
                    candidates.append(new_tag)
            if len(candidates) == 1:
                entity_map[f'{dim}:{old_tag}'] = candidates[0]
            else:
                unmapped.append([dim, old_tag])
        remapped_regions = None
        if regions is not None:
            remapped_regions = []
            for region in regions:
                new_entities = []
                for dim, old_tag in region['entities']:
                    mapped = entity_map.get(f'{dim}:{old_tag}')
                    if mapped is None:
                        raise ValueError(
                            f"Region {region.get('label')!r} entity ({dim}, {old_tag}) was split, merged or ambiguously modified by healing."
                        )
                    new_entities.append([dim, mapped])
                if len({tuple(e) for e in new_entities}) != len(new_entities):
                    raise ValueError(f"Region {region.get('label')!r} entities merged together during healing.")
                updated = deepcopy(region)
                updated['entities'] = sorted(new_entities)
                remapped_regions.append(updated)
        return {
            'tolerance': tolerance,
            'before_counts': before_counts,
            'after_counts': after_counts,
            'topology_changed': before_counts != after_counts or bool(unmapped),
            'entity_map': entity_map,
            'unmapped_entities': unmapped,
            'remapped_regions': remapped_regions,
        }
    finally:
        gmsh.finalize()


def open_source(gmsh, filename):
    """Open scripts in their own kernel, neutral CAD explicitly through OCC."""
    path = Path(filename).resolve()
    suffix = path.suffix.lower()
    kind = FORMATS.get(suffix)
    if kind is None:
        detail = UNSUPPORTED_TRANSLATORS.get(suffix)
        msg = 'Choose a Gmsh .geo, STEP, Open CASCADE .brep or IGES geometry file.'
        raise ValueError(f'{msg} {detail}' if detail else msg)
    if not path.is_file():
        raise ValueError('Geometry file does not exist.')
    if kind == 'geo':
        gmsh.open(str(path))
    else:
        gmsh.model.add(path.stem)
        # OCC can print directly to native stdout despite General.Terminal=0.
        # Workers reserve stdout for JSON: retain native diagnostics on stderr.
        sys.stdout.flush()
        saved_stdout = os.dup(1)
        try:
            os.dup2(2, 1)
            gmsh.model.occ.importShapes(str(path), highestDimOnly=False, format=kind)
        finally:
            os.dup2(saved_stdout, 1)
            os.close(saved_stdout)
        gmsh.model.occ.synchronize()
    return kind


def import_options(gmsh, source_format):
    """Record Jorvik's loader arguments, not inferred CAD exporter settings."""
    options = {'backend': 'gmsh', 'backend_version': gmsh.__version__,
               'read_config_files': False,
               'loader': 'open' if source_format == 'geo' else 'occ.importShapes'}
    if source_format != 'geo':
        options.update(highest_dim_only=False, format=source_format)
    return options


def validate_import_metadata(record):
    """Older records may omit provenance; never invent their import settings."""
    kind = record.get('source_format')
    if kind is not None and (not isinstance(kind, str) or kind not in FORMATS.values()):
        raise ValueError('Invalid source format metadata.')
    if 'import_options' not in record:
        return
    options = record['import_options']
    if (not isinstance(options, dict) or kind is None
            or options.get('backend') != 'gmsh'
            or not isinstance(options.get('backend_version'), str)
            or not options['backend_version'].strip()
            or options.get('read_config_files') is not False
            or options.get('loader') != ('open' if kind == 'geo' else 'occ.importShapes')):
        raise ValueError('Invalid geometry import options.')
    if kind != 'geo' and (options.get('highest_dim_only') is not False or options.get('format') != kind):
        raise ValueError('Invalid CAD import options.')
