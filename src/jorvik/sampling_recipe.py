"""Versioned sampling recipes without Qt, CAD execution or point arrays."""
from copy import deepcopy
import json
from pathlib import Path
import re
from .cad_import import validate_import_metadata
from .sampling_methods import METHODS

FIELDS = ('target', 'entities', 'method', 'count', 'seed', 'label', 'resolution', 'compute_mode', 'cpu_threads')


def _entities(value, *, empty=False):
    if not isinstance(value, list) or not value and not empty:
        raise ValueError('Recipe needs a non-empty list of faces or edges.')
    if any(not isinstance(item, list) or len(item) != 2 or type(item[0]) is not int
           or item[0] not in (1, 2) or type(item[1]) is not int or item[1] <= 0 for item in value):
        raise ValueError('Invalid recipe entity references.')
    if len({tuple(item) for item in value}) != len(value) or len({item[0] for item in value}) > 1:
        raise ValueError('Recipe entities must be distinct faces or distinct edges.')


def validate_recipe(recipe):
    if (not isinstance(recipe, dict) or type(recipe.get('schema_version')) is not int
            or recipe['schema_version'] != 1 or recipe.get('kind') != 'jorvik_sampling_recipe'):
        raise ValueError('Unsupported sampling recipe.')
    if set(recipe) - {'schema_version', 'kind', 'geometry', 'sampling'}:
        raise ValueError('Sampling recipes contain settings only, without point arrays or extra payloads.')
    geometry, settings = recipe.get('geometry'), recipe.get('sampling')
    if not isinstance(geometry, dict) or not isinstance(settings, dict):
        raise ValueError('Recipe geometry and sampling settings are required.')
    if not isinstance(geometry.get('source_hash'), str) or not re.fullmatch(r'[0-9a-f]{64}', geometry['source_hash']):
        raise ValueError('Recipe needs the original geometry SHA-256 hash.')
    validate_import_metadata(geometry)
    if isinstance(geometry.get('import_options'), dict) and set(geometry['import_options']) - {'backend', 'backend_version', 'read_config_files', 'loader', 'highest_dim_only', 'format'}:
        raise ValueError('Unsupported recipe import options.')
    if geometry.get('source_format') is None or 'import_options' not in geometry:
        raise ValueError('Recipe needs geometry format and import settings.')
    if set(geometry) - {'source_hash', 'source_format', 'import_options'}:
        raise ValueError('Recipes contain settings and references only, without generated point arrays.')
    validate_settings(settings)


def validate_settings(settings):
    """Validate sampling inputs independently of optional legacy provenance."""
    if not isinstance(settings, dict): raise ValueError('Sampling settings must be an object.')
    target = settings.get('target')
    if target not in ('selection', 'group', 'region', 'interior'):
        raise ValueError('Invalid recipe sampling target.')
    _entities(settings.get('entities'), empty=target == 'interior')
    if target == 'interior' and settings['entities']:
        raise ValueError('Interior recipes must not bind boundary entities.')
    if not isinstance(settings.get('method'), str) or settings['method'] not in METHODS:
        raise ValueError('Unknown recipe sampling method.')
    for field, low, high in (('count', 1, 50000), ('seed', 0, 2**32-1), ('cpu_threads', 1, 64)):
        value = settings.get(field)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'Recipe {field} must be an integer between {low} and {high}.')
    if type(settings.get('resolution')) is not int or settings['resolution'] not in (16, 32, 64):
        raise ValueError('Recipe accuracy must be 16, 32 or 64.')
    if settings.get('compute_mode') not in ('reproducible', 'multicore'):
        raise ValueError('Invalid recipe compute mode.')
    if settings['compute_mode'] == 'reproducible' and settings['cpu_threads'] != 1:
        raise ValueError('Reproducible recipes require one CPU thread.')
    label = settings.get('label')
    if not isinstance(label, str) or not label.strip() or label != label.strip():
        raise ValueError('Recipe needs a non-empty dataset label.')
    if target == 'region':
        region = settings.get('region')
        if (not isinstance(region, dict) or not isinstance(region.get('label'), str)
                or not region['label'].strip() or region['label'] != region['label'].strip()
                or region.get('entities') != settings['entities']):
            raise ValueError('Recipe boundary-region label or membership is invalid.')
    elif 'region' in settings:
        raise ValueError('Only region recipes can contain a boundary-region reference.')
    allowed = set(FIELDS) | {'region'}
    if set(settings) - allowed:
        raise ValueError('Recipes contain settings and references only, without generated point arrays.')


def create_recipe(request):
    """Extract a portable recipe from a generation request; never include samples."""
    defaults = {'resolution': 32, 'compute_mode': 'reproducible', 'cpu_threads': 1}
    try: settings = {key: deepcopy(request[key] if key in request else defaults[key]) for key in FIELDS}
    except KeyError as error: raise ValueError(f'Missing sampling recipe setting: {error.args[0]}') from None
    if settings['target'] == 'interior': settings['entities'] = []
    if settings['target'] == 'region':
        region = request.get('boundary_region', {})
        settings['region'] = {key: deepcopy(region.get(key)) for key in ('label', 'entities')}
    recipe = {'schema_version': 1, 'kind': 'jorvik_sampling_recipe',
              'geometry': {key: deepcopy(request.get(key)) for key in ('source_hash', 'source_format', 'import_options')},
              'sampling': settings}
    validate_recipe(recipe)
    return recipe


def save_recipe(recipe, path):
    validate_recipe(recipe)
    from .export import atomic_write
    path = Path(path)
    text = json.dumps(recipe, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    atomic_write(path, lambda temporary: temporary.write_text(text, encoding='utf-8'))
    return path


def load_recipe(path):
    recipe = json.loads(Path(path).read_text(encoding='utf-8'))
    validate_recipe(recipe)
    return recipe


def bind_recipe(recipe, geometry, regions=()):
    """Validate current CAD references and bind a fresh request to its document.

    No source code is executed. Identical source hashes/import settings permit
    reuse after reopening/moving CAD. Regions match exact labels and memberships;
    imported groups match exact dimensions and member tags. No entities are remapped.
    """
    validate_recipe(recipe)
    for field, expected in recipe['geometry'].items():
        if geometry.get(field) != expected:
            raise ValueError('Recipe geometry or import settings differ. Open the original geometry/project.')
    settings = deepcopy(recipe['sampling'])
    available = {(entity['dimension'], entity['tag']) for entity in geometry['entities']}
    if not {tuple(item) for item in settings['entities']} <= available:
        raise ValueError('Recipe references missing faces or edges.')
    region = settings.pop('region', None)
    if settings['target'] == 'group':
        dimension = settings['entities'][0][0]
        tags = {item[1] for item in settings['entities']}
        if not any(group['dimension'] == dimension and set(group['entities']) == tags for group in geometry.get('groups', [])):
            raise ValueError('The imported group referenced by this recipe is missing or changed.')
    if region is not None:
        from .regions import validate_regions
        validate_regions(list(regions), geometry)
        match = next((item for item in regions if item['label'] == region['label']
                      and sorted(item['entities']) == sorted(region['entities'])), None)
        if match is None:
            raise ValueError('The recipe boundary region is missing or its membership changed. Reopen the saved project.')
        settings['boundary_region'] = deepcopy(match)
    settings.update({key: deepcopy(geometry[key]) for key in ('source', 'source_hash', 'document', 'source_format', 'import_options')})
    return settings


def request_from_area(area, geometry):
    """Restore the original sampling request from a project dataset/recipe.

    Keep recorded region snapshots rather than silently applying edited live
    regions. Legacy projects may lack importer provenance; do not invent it.
    No points are copied and no CAD is executed here.
    """
    validate_import_metadata(area)
    if area.get('source_hash') != geometry['source_hash'] or area.get('document') != geometry['document']:
        raise ValueError('Saved recipe belongs to a different geometry/document.')
    for field in ('source_format', 'import_options'):
        if field in area and field in geometry and area[field] != geometry[field]:
            raise ValueError('Saved recipe import settings differ from the project geometry.')
    defaults = {'resolution': 32, 'compute_mode': 'reproducible', 'cpu_threads': 1}
    try: settings = {key: deepcopy(area[key] if key in area else defaults[key]) for key in FIELDS}
    except KeyError as error: raise ValueError(f'Missing saved recipe setting: {error.args[0]}') from None
    settings['count'] = area.get('requested_count', settings['count'])
    if settings['target'] == 'interior': settings['entities'] = []
    if settings['compute_mode'] == 'reproducible': settings['cpu_threads'] = 1
    if settings['target'] == 'region':
        snapshot = area.get('boundary_region')
        from .regions import validate_regions
        validate_regions([snapshot], geometry)
        settings['region'] = {key: deepcopy(snapshot[key]) for key in ('label', 'entities')}
    validate_settings(settings)
    available = {(entity['dimension'], entity['tag']) for entity in geometry['entities']}
    if not {tuple(entity) for entity in settings['entities']} <= available:
        raise ValueError('Saved recipe references missing faces or edges.')
    if settings['target'] == 'group':
        dimension = settings['entities'][0][0]
        tags = {item[1] for item in settings['entities']}
        if not any(group['dimension'] == dimension and set(group['entities']) == tags for group in geometry['groups']):
            raise ValueError('Saved recipe references a missing or changed imported group.')
    settings.pop('region', None)
    request = {key: deepcopy(geometry[key]) for key in ('source', 'source_hash', 'document')}
    for field in ('source_format', 'import_options'):
        if field in geometry: request[field] = deepcopy(geometry[field])
    request.update(settings)
    if settings['target'] == 'region': request['boundary_region'] = deepcopy(area['boundary_region'])
    return request
