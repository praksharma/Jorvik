"""Bounded, reproducible parameter-space designs, independent of spatial sampling.

Configurations contain transform settings and parameter values only. No point
clouds or Cartesian coordinate grids are allocated by this module.
"""
from copy import deepcopy
from itertools import product
import math

import numpy as np

DESIGN_VERSION = 1
MAX_CONFIGURATIONS = 10000
MAX_PARAMETERS = 8
TARGETS = {
    'translation_x': 'Translation X', 'translation_y': 'Translation Y',
    'translation_z': 'Translation Z', 'rotation_angle': 'Rotation angle',
    'scale_x': 'Scale X', 'scale_y': 'Scale Y', 'scale_z': 'Scale Z',
    'uniform_scale': 'Uniform scale',
}
METHODS = ('cartesian', 'paired', 'random', 'lhs', 'sobol')


def _number(value, description):
    if type(value) not in (int, float):
        raise ValueError(f'{description} must be a finite number.')
    try:
        number = float(value)
    except (ValueError, OverflowError) as error:
        raise ValueError(f'{description} must be a finite number.') from error
    if not math.isfinite(number):
        raise ValueError(f'{description} must be a finite number.')
    return number


def _count(value, description):
    if type(value) is not int or not 1 <= value <= MAX_CONFIGURATIONS:
        raise ValueError(f'{description} must be between 1 and {MAX_CONFIGURATIONS}.')
    return value


def _validate_parameter(parameter, method, base_setup):
    if not isinstance(parameter, dict):
        raise ValueError('Invalid parameter definition.')
    name, target = parameter.get('name'), parameter.get('target')
    if not isinstance(name, str) or not name.strip():
        raise ValueError('Every parameter needs a non-empty name.')
    if not isinstance(target, str) or target not in TARGETS:
        raise ValueError(f'{name}: choose a supported transform target.')
    unit = parameter.get('unit')
    if target.startswith('translation_'):
        units = ('source',)
    elif target == 'rotation_angle':
        units = ((base_setup.get('angle_units'),) if base_setup is not None
                 else ('degrees', 'radians'))
    else:
        units = ('dimensionless',)
    if unit not in units:
        raise ValueError(f'{name}: units must match the transform target; no unit conversion is applied.')
    reference = _number(parameter.get('reference'), f'{name} reference')
    mode = parameter.get('mode')
    if mode == 'values':
        if method in ('random', 'lhs', 'sobol'):
            raise ValueError('Random, LHS and Sobol designs require parameter ranges, not explicit value lists.')
        values = parameter.get('values')
        if not isinstance(values, list) or not values or len(values) > MAX_CONFIGURATIONS:
            raise ValueError(f'{name}: supply 1 to {MAX_CONFIGURATIONS} explicit values.')
        checked = [_number(value, f'{name} value') for value in values]
        length = len(checked)
    elif mode == 'range':
        start = _number(parameter.get('start'), f'{name} range start')
        stop = _number(parameter.get('stop'), f'{name} range stop')
        if start > stop:
            raise ValueError(f'{name}: range start must not exceed range stop.')
        if not math.isfinite(stop - start):
            raise ValueError(f'{name}: the range width is too large.')
        checked = [start, stop]
        length = (_count(parameter.get('count'), f'{name} value count')
                  if method in ('cartesian', 'paired') else None)
    else:
        raise ValueError(f'{name}: choose range or explicit values.')
    if target.startswith('scale_') or target == 'uniform_scale':
        if any(value < 1e-8 or value > 1e8 for value in [reference, *checked]):
            raise ValueError(f'{name}: scale factors and reference must be between 1e-8 and 1e8.')
    return length


def _validated_size(design, base_setup=None):
    if (not isinstance(design, dict) or type(design.get('schema_version')) is not int
            or design['schema_version'] != DESIGN_VERSION):
        raise ValueError('Unsupported parameter design version.')
    method = design.get('method')
    if not isinstance(method, str) or method not in METHODS:
        raise ValueError('Choose Cartesian, paired, random, LHS or Sobol design.')
    seed = design.get('seed')
    if type(seed) is not int or not 0 <= seed < 2**32:
        raise ValueError('Parameter design seed must be an integer from 0 to 2^32-1.')
    parameters = design.get('parameters')
    if not isinstance(parameters, list) or not 1 <= len(parameters) <= MAX_PARAMETERS:
        raise ValueError(f'Define between 1 and {MAX_PARAMETERS} parameters.')
    lengths, names, targets = [], set(), set()
    for parameter in parameters:
        lengths.append(_validate_parameter(parameter, method, base_setup))
        name, target = parameter['name'], parameter['target']
        if name in names or target in targets:
            raise ValueError('Parameter names and transform targets must be unique.')
        names.add(name); targets.add(target)
    if 'uniform_scale' in targets and targets & {'scale_x', 'scale_y', 'scale_z'}:
        raise ValueError('Uniform scale cannot overlap individual scale targets.')
    if method == 'cartesian':
        count = 1
        for length in lengths:
            # Check before product generation, linspace or meshgrid allocation.
            if count > MAX_CONFIGURATIONS // length:
                raise ValueError(f'The design exceeds {MAX_CONFIGURATIONS} configurations; reduce the per-parameter counts.')
            count *= length
        return count
    if method == 'paired':
        if len(set(lengths)) != 1:
            raise ValueError('Paired designs require the same value count for every parameter.')
        return lengths[0]
    return _count(design.get('count'), 'Total design size')


def validate_design(design, base_setup=None):
    """Validate definitions and bounded size without allocating configurations."""
    _validated_size(design, base_setup)


def design_summary(design, batches=None):
    """Estimate uncompressed numeric output, excluding JSON and container overhead."""
    count = _validated_size(design)
    regions, points, bytes_per_configuration = [], 0, 0
    for batch in batches or []:
        size = batch.get('count')
        if type(size) is not int or size < 0:
            raise ValueError('Dataset counts must be non-negative integers.')
        regions.append({'label': batch['label'], 'points': size})
        points += size
        bytes_per_configuration += size * (24 + 4 + (24 if 'normals' in batch else 0))
    warnings = ['Size estimates include numeric coordinates, tags and available normals only; metadata and container overhead are excluded.']
    if design['method'] == 'sobol' and count & (count - 1):
        warnings.append('Sobol balance is strongest for power-of-two design sizes; this design truncates a larger balanced sequence.')
    return {'count': count, 'parameter_count': len(design['parameters']),
            'points_per_configuration': points, 'total_points': points * count,
            'estimated_bytes': bytes_per_configuration * count,
            'per_region': regions, 'warnings': warnings}


def _values(parameter):
    if parameter['mode'] == 'values':
        return [float(value) for value in parameter['values']]
    return np.linspace(parameter['start'], parameter['stop'], parameter['count']).tolist()


def _apply_value(setup, target, value):
    if target.startswith('translation_'):
        setup['translation']['xyz'.index(target[-1])] = value
    elif target == 'rotation_angle':
        setup['rotation_angle'] = value
    elif target.startswith('scale_'):
        setup['scale']['xyz'.index(target[-1])] = value
    else:
        setup['scale'] = [value, value, value]


def generate_configurations(base_setup, design):
    """Generate deterministic IDs and scalar setups; never process spatial clouds."""
    from .parametrisation import validate_setup
    validate_setup(base_setup)
    if base_setup['schema_version'] != 2:
        raise ValueError('Parameter designs require a schema 2 transform setup.')
    count = _validated_size(design, base_setup)
    method, parameters = design['method'], design['parameters']
    if method in ('cartesian', 'paired'):
        columns = [_values(parameter) for parameter in parameters]
        rows = product(*columns) if method == 'cartesian' else zip(*columns)
    else:
        rng = np.random.default_rng(design['seed'])
        dimensions = len(parameters)
        if method == 'random':
            candidates = rng.random((count, dimensions))
        else:
            from scipy.stats import qmc
            if method == 'lhs':
                candidates = qmc.LatinHypercube(dimensions, rng=rng).random(count)
            else:
                candidates = qmc.Sobol(dimensions, scramble=True, rng=rng).random_base2((count - 1).bit_length())[:count]
        low = np.asarray([parameter['start'] for parameter in parameters], dtype=float)
        high = np.asarray([parameter['stop'] for parameter in parameters], dtype=float)
        rows = low + candidates * (high - low)
    units = {parameter['name']: parameter['unit'] for parameter in parameters}
    bindings = {parameter['name']: parameter['target'] for parameter in parameters}
    # A persisted base can contain a large explicit-value design. Exclude it
    # before any copies so N configurations never copy N source values each.
    template = deepcopy({key: value for key, value in base_setup.items()
                         if key not in ('design', 'design_configuration')})
    result = []
    for index, row in enumerate(rows, 1):
        identity = f'configuration_{index:06d}'
        setup = deepcopy(template)
        values = {}
        for parameter, value in zip(parameters, row):
            value = float(value)
            values[parameter['name']] = value
            _apply_value(setup, parameter['target'], value)
        setup['design_configuration'] = {'id': identity, 'method': method, 'seed': design['seed'],
                                         'parameters': values.copy(), 'units': units.copy(),
                                         'bindings': bindings.copy()}
        try:
            validate_setup(setup)
        except (ValueError, TypeError, OverflowError) as error:
            raise ValueError(f'{identity}: invalid transform configuration: {error}') from error
        result.append({'id': identity, 'parameters': values, 'setup': setup})
    return result
