"""Qt-independent .geo script parameter discovery and topological CAD regeneration."""
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re
import tempfile

_COMMENT_BLOCK = re.compile(r'/\*.*?\*/', re.DOTALL)
_COMMENT_LINE = re.compile(r'//[^\n]*')
_ASSIGNMENT = re.compile(
    r'^(?P<indent>\s*)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*'
    r'(?P<value>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\s*;(?P<tail>\s*(?://.*)?)$',
    re.MULTILINE,
)
_DEFINE_CONSTANT = re.compile(
    r'(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{\s*'
    r'(?P<value>[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)\b',
)


def discover_geo_parameters(path):
    """Extract top-level scalar numeric parameters from a .geo script without executing it."""
    path = Path(path)
    if path.suffix.lower() != '.geo' or not path.is_file():
        return []
    try:
        text = path.read_text(encoding='utf-8', errors='replace')
    except OSError:
        return []
    stripped = _COMMENT_LINE.sub('', _COMMENT_BLOCK.sub('', text))
    seen = {}
    for match in _ASSIGNMENT.finditer(stripped):
        name = match.group('name')
        try:
            value = float(match.group('value'))
        except (ValueError, OverflowError):
            continue
        if math.isfinite(value) and name not in seen:
            seen[name] = {'name': name, 'default': value}
    for match in _DEFINE_CONSTANT.finditer(stripped):
        name = match.group('name')
        try:
            value = float(match.group('value'))
        except (ValueError, OverflowError):
            continue
        if math.isfinite(value) and name not in seen:
            seen[name] = {'name': name, 'default': value}
    return list(seen.values())


def validate_geo_overrides(overrides, declared=None):
    """Validate a mapping of .geo parameter names to finite numeric values."""
    if overrides is None:
        return {}
    if not isinstance(overrides, dict):
        raise ValueError('CAD parameter overrides must be a dictionary.')
    allowed = ({item['name'] for item in declared}
               if isinstance(declared, list) else None)
    checked = {}
    for name, value in overrides.items():
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError(f'Invalid CAD parameter name: {name!r}')
        if allowed is not None and name not in allowed:
            raise ValueError(f'Unknown .geo parameter: {name}')
        if type(value) is bool or type(value) not in (int, float) or not math.isfinite(float(value)):
            raise ValueError(f'CAD parameter {name} must be a finite number.')
        checked[name] = float(value)
    return checked


def apply_geo_overrides_text(text, overrides):
    """Substitute overridden top-level numeric assignments in memory without touching disk."""
    overrides = validate_geo_overrides(overrides)
    if not overrides:
        return text

    def replace_assignment(match):
        name = match.group('name')
        if name not in overrides:
            return match.group(0)
        return f"{match.group('indent')}{name} = {repr(overrides[name])};{match.group('tail')}"

    updated = _ASSIGNMENT.sub(replace_assignment, text)

    def replace_define(match):
        name = match.group('name')
        if name not in overrides:
            return match.group(0)
        return f"{name} = {{ {repr(overrides[name])}"

    return _DEFINE_CONSTANT.sub(replace_define, updated)


def verify_topology_consistency(base_geometry, regenerated_geometry, batches=()):
    """Reject missing, split, merged or degenerate entities after dimensional regeneration."""
    if regenerated_geometry.get('interior_domain') != base_geometry.get('interior_domain'):
        raise ValueError('Regenerated CAD changed the interior domain classification.')
    if regenerated_geometry.get('solid_count') != base_geometry.get('solid_count'):
        raise ValueError('Regenerated CAD changed the solid volume count.')
    base_entities = {(e['dimension'], e['tag']): e for e in base_geometry['entities']}
    regen_entities = {(e['dimension'], e['tag']): e for e in regenerated_geometry['entities']}
    if set(base_entities) != set(regen_entities):
        raise ValueError('Regenerated CAD changed face or edge identities; topology changes are rejected.')
    for key, base_entity in base_entities.items():
        regen_entity = regen_entities[key]
        if regen_entity['type'] != base_entity['type']:
            raise ValueError(f'Entity {key} changed CAD type during regeneration.')
        if regen_entity.get('degenerate') and not base_entity.get('degenerate'):
            raise ValueError(f'Entity {key} collapsed to a degenerate edge during regeneration.')
        if sorted(regen_entity.get('boundary', [])) != sorted(base_entity.get('boundary', [])):
            raise ValueError(f'Entity {key} boundary adjacency changed during regeneration.')
    base_groups = {(g['dimension'], g['tag']): (g['name'], sorted(g['entities']))
                   for g in base_geometry.get('groups', [])}
    regen_groups = {(g['dimension'], g['tag']): (g['name'], sorted(g['entities']))
                    for g in regenerated_geometry.get('groups', [])}
    if base_groups != regen_groups:
        raise ValueError('Regenerated CAD changed physical group memberships.')
    if 'topology' in base_geometry and 'topology' in regenerated_geometry:
        base_top = {(e['dimension'], e['tag']): e for e in base_geometry['topology']}
        regen_top = {(e['dimension'], e['tag']): e for e in regenerated_geometry['topology']}
        if set(base_top) != set(regen_top):
            raise ValueError('Regenerated CAD changed full topological entity identities.')
        for key, item in base_top.items():
            other = regen_top[key]
            if (other['type'] != item['type']
                    or sorted(other['boundary']) != sorted(item['boundary'])
                    or sorted(other['upward']) != sorted(item['upward'])):
                raise ValueError(f'Topological adjacency changed for entity {key} during regeneration.')
    for batch in batches:
        for dim, tag in batch.get('entities', []):
            if dim in (1, 2) and (dim, tag) not in regen_entities:
                raise ValueError(f"Dataset {batch.get('label')!r} references an entity missing after regeneration.")


def regenerate_batches(batches, overrides, *, base_geometry=None):
    """Regenerate .geo geometry with overrides and rerun each dataset's sampling recipe."""
    from .geometry import load_geometry
    from .sampling import sample_geometry
    from .sampling_recipe import FIELDS
    batches = list(batches)
    if not batches:
        raise ValueError('Generate samples before regenerating CAD configurations.')
    overrides = validate_geo_overrides(overrides)
    if not overrides:
        return deepcopy(batches), base_geometry
    first = batches[0]
    source_path = Path(first['source']).resolve()
    if source_path.suffix.lower() != '.geo' or first.get('source_format', 'geo') != 'geo':
        raise ValueError('CAD dimensional regeneration currently requires a Gmsh .geo script.')
    if not source_path.is_file():
        raise ValueError('Original .geo source file does not exist.')
    raw_bytes = source_path.read_bytes()
    digest = hashlib.sha256(raw_bytes).hexdigest()
    if digest != first['source_hash']:
        raise ValueError('Geometry source changed on disk. Reopen it before regenerating.')
    declared = discover_geo_parameters(source_path)
    validate_geo_overrides(overrides, declared)
    text = raw_bytes.decode('utf-8', errors='replace')
    modified_text = apply_geo_overrides_text(text, overrides)
    with tempfile.TemporaryDirectory(prefix='jorvik-regen-') as tmpdir:
        temp_geo = Path(tmpdir) / source_path.name
        temp_geo.write_text(modified_text, encoding='utf-8')
        temp_hash = hashlib.sha256(temp_geo.read_bytes()).hexdigest()
        reference_geometry = base_geometry if base_geometry is not None else load_geometry(source_path)
        regenerated_geometry = load_geometry(temp_geo)
        verify_topology_consistency(reference_geometry, regenerated_geometry, batches)
        regenerated_geometry['source'] = str(source_path)
        regenerated_geometry['source_hash'] = digest
        regenerated_geometry['document'] = first['document']
        regenerated_geometry['geo_overrides'] = deepcopy(overrides)
        regenerated_batches = []
        for batch in batches:
            request = {
                'source': str(temp_geo),
                'source_hash': temp_hash,
                'document': first['document'],
                'source_format': 'geo',
                'import_options': regenerated_geometry['import_options'],
            }
            defaults = {'resolution': 32, 'compute_mode': 'reproducible', 'cpu_threads': 1}
            for field in FIELDS:
                request[field] = deepcopy(batch[field] if field in batch else defaults[field])
            request['count'] = batch.get('requested_count', request['count'])
            if 'boundary_region' in batch:
                snapshot = deepcopy(batch['boundary_region'])
                snapshot['source_hash'] = temp_hash
                request['boundary_region'] = snapshot
            if 'refinement' in batch:
                request['refinement'] = deepcopy(batch['refinement'])
            fresh = sample_geometry(request)
            fresh['source'] = str(source_path)
            fresh['source_hash'] = digest
            if 'boundary_region' in fresh and 'boundary_region' in batch:
                fresh['boundary_region']['source_hash'] = digest
            if 'colour' in batch:
                fresh['colour'] = batch['colour']
            fresh['cad_regeneration'] = {'parameters': deepcopy(overrides)}
            regenerated_batches.append(fresh)
        return regenerated_batches, regenerated_geometry
