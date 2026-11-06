"""Qt-independent affine transformations of original sampled datasets and normals."""
from copy import deepcopy
import json
import numpy as np

SCHEMA_VERSION = 2


def _vector(value, name):
    try:
        values = np.asarray(value, dtype=float)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValueError(f'{name} needs three finite values.') from error
    if values.shape != (3,) or not np.isfinite(values).all():
        raise ValueError(f'{name} needs three finite values.')
    return values


def validate_setup(setup, batches=None):
    if not isinstance(setup, dict) or type(setup.get('schema_version')) is not int or setup['schema_version'] not in (1, 2):
        raise ValueError('Unsupported parametrisation setup version.')
    if setup.get('scope') not in ('domain', 'regions'):
        raise ValueError('Choose whole domain or selected regions.')
    values = _vector(setup.get('translation'), 'Translation')
    if not isinstance(setup.get('source_hash'), str) or not setup['source_hash']:
        raise ValueError('A source geometry hash is required.')
    labels = setup.get('regions')
    if (not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() for label in labels)
            or len(set(labels)) != len(labels)):
        raise ValueError('Invalid region labels.')
    if setup['scope'] == 'regions' and not labels:
        raise ValueError('Select at least one sampled region.')
    if setup['schema_version'] == 2:
        axis = _vector(setup.get('rotation_axis'), 'Rotation axis')
        if not np.any(axis): raise ValueError('Rotation axis must be nonzero.')
        angle = setup.get('rotation_angle')
        try:
            valid_angle = type(angle) in (int, float) and np.isfinite(float(angle))
        except (ValueError, OverflowError):
            valid_angle = False
        if not valid_angle:
            raise ValueError('Rotation angle must be finite.')
        if setup.get('angle_units') not in ('degrees', 'radians'):
            raise ValueError('Choose degrees or radians for rotation.')
        scales = _vector(setup.get('scale'), 'Scale')
        if np.any(scales < 1e-8) or np.any(scales > 1e8):
            raise ValueError('Scale factors must be positive and between 1e-8 and 1e8; reflections are unsupported.')
        _vector(setup.get('pivot'), 'Pivot')
        if setup.get('order') != ['scale', 'rotate', 'translate'] or setup.get('frame') != 'world':
            raise ValueError('Use scale, rotate, translate order in the world coordinate frame.')
        # Check arithmetic before changing previews, saving setups or writing files.
        _affine_components(setup)
    if 'design' in setup:
        if setup['schema_version'] != 2:
            raise ValueError('Parameter designs require a schema 2 transform setup.')
        from .parameter_design import validate_design
        validate_design(setup['design'], setup)
    if 'design_configuration' in setup:
        configuration = setup['design_configuration']
        from .parameter_design import METHODS
        if (setup['schema_version'] != 2 or not isinstance(configuration, dict)
                or not isinstance(configuration.get('id'), str) or not configuration['id']
                or configuration.get('method') not in METHODS
                or type(configuration.get('seed')) is not int or not 0 <= configuration['seed'] < 2**32):
            raise ValueError('Invalid parameter configuration provenance.')
        parameters, units = configuration.get('parameters'), configuration.get('units')
        if (not isinstance(parameters, dict) or not parameters or not isinstance(units, dict)
                or set(parameters) != set(units)
                or any(not isinstance(name, str) or not name.strip() for name in parameters)
                or any(unit not in ('source', 'degrees', 'radians', 'dimensionless') for unit in units.values())):
            raise ValueError('Invalid named parameter values or units.')
        for value in parameters.values():
            try:
                valid = type(value) in (int, float) and np.isfinite(float(value))
            except (ValueError, OverflowError):
                valid = False
            if not valid: raise ValueError('Parameter values must be finite numbers.')
        from .parameter_design import validate_constraints, constraint_reasons
        rules = configuration.get('constraints', [])
        validate_constraints(rules, units)
        if constraint_reasons(parameters, rules):
            raise ValueError('Selected configuration violates its parameter constraints.')
        bindings = configuration.get('bindings')
        if 'design' in setup:
            definition = setup['design']
            if rules != definition.get('constraints', []):
                raise ValueError('Selected configuration constraints differ from its design.')
            expected = {parameter['name']: parameter['target'] for parameter in definition['parameters']}
            if (configuration['method'] != definition['method'] or configuration['seed'] != definition['seed']
                    or set(parameters) != set(expected)
                    or units != {parameter['name']: parameter['unit'] for parameter in definition['parameters']}
                    or bindings is not None and bindings != expected):
                raise ValueError('Selected configuration differs from its parameter design.')
            bindings = expected
        if bindings is not None:
            from .parameter_design import TARGETS
            if not isinstance(bindings, dict) or set(bindings) != set(parameters):
                raise ValueError('Invalid named parameter bindings.')
            targets = list(bindings.values())
            if (any(not isinstance(target, str) or target not in TARGETS for target in targets) or len(set(targets)) != len(targets)
                    or 'uniform_scale' in targets and any(target.startswith('scale_') for target in targets)):
                raise ValueError('Invalid or overlapping named parameter bindings.')
            for name, target in bindings.items():
                value = parameters[name]
                if target.startswith('translation_'):
                    actual = setup['translation']['xyz'.index(target[-1])]
                elif target.startswith('scale_'):
                    actual = setup['scale']['xyz'.index(target[-1])]
                elif target == 'rotation_angle': actual = setup['rotation_angle']
                else:
                    if any(factor != value for factor in setup['scale']):
                        raise ValueError('Uniform scale parameter differs from the actual transform.')
                    actual = value
                expected_unit = ('source' if target.startswith('translation_') else
                                 setup['angle_units'] if target == 'rotation_angle' else 'dimensionless')
                if actual != value or units[name] != expected_unit:
                    raise ValueError('Named parameter provenance differs from the actual transform.')
    if batches is not None:
        if not batches:
            raise ValueError('Generate samples before processing configurations.')
        identity = (batches[0]['document'], batches[0]['source_hash'])
        if (setup['source_hash'] != identity[1]
                or any((b['document'], b['source_hash']) != identity for b in batches)):
            raise ValueError('This setup belongs to a different geometry. Create a new setup for this model.')
        if set(labels) - {b['label'] for b in batches}:
            raise ValueError('Some setup regions are missing or renamed. Select the regions again.')
    return values


def _affine_components(setup):
    translation = np.asarray(setup['translation'], dtype=float)
    if setup['schema_version'] == 1:
        return np.eye(3), translation
    axis = np.asarray(setup['rotation_axis'], dtype=float)
    axis = axis / np.max(np.abs(axis))
    axis /= np.linalg.norm(axis)
    x, y, z = axis
    angle = setup['rotation_angle']
    radians = np.deg2rad(angle % 360) if setup['angle_units'] == 'degrees' else angle % (2 * np.pi)
    sine, cosine = np.sin(radians), np.cos(radians)
    cross = np.array([[0., -z, y], [z, 0., -x], [-y, x, 0.]])
    rotation = cosine * np.eye(3) + (1 - cosine) * np.outer(axis, axis) + sine * cross
    linear = rotation @ np.diag(setup['scale'])
    pivot = np.asarray(setup['pivot'], dtype=float)
    with np.errstate(over='ignore', invalid='ignore'):
        offset = pivot - linear @ pivot + translation
    if not np.isfinite(linear).all() or not np.isfinite(offset).all():
        raise ValueError('Transform produced non-finite values.')
    return linear, offset


def affine_transform(setup):
    """Return A, b for x' = A x + b; scale/rotate about pivot, then translate."""
    validate_setup(setup)
    return _affine_components(setup)


def _matrix(linear, offset):
    matrix = np.eye(4)
    matrix[:3, :3] = linear
    matrix[:3, 3] = offset
    return matrix


def preview_payload(setup):
    if setup is None: return None
    linear, offset = affine_transform(setup)
    return dict(deepcopy(setup), matrix=_matrix(linear, offset).flatten(order='F').tolist())


def transform_batches(batches, setup):
    """Evaluate original points, transporting surface normals and planar co-normals."""
    offset = validate_setup(setup, batches)
    linear, offset = _affine_components(setup)
    affine = setup['schema_version'] == 2
    result = []
    for batch in batches:
        record = deepcopy(batch)
        affected = setup['scope'] == 'domain' or batch['label'] in setup['regions']
        points = np.asarray(batch['coordinates'], dtype=float)
        if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
            raise ValueError('Coordinates must be finite non-empty N by 3 arrays.')
        with np.errstate(over='ignore', invalid='ignore'):
            transformed = (points @ linear.T + offset) if affected else points.copy()
        if not np.isfinite(transformed).all():
            raise ValueError('Transform produced non-finite coordinates.')
        record['coordinates'] = transformed.tolist()
        if affected and affine and 'normals' in batch:
            from .normals import validate_normals
            original_normals = validate_normals(batch)
            conormal = batch.get('normal_kind') == 'outward_conormal' or batch.get('entity_dimension') == 1
            anisotropic = len(set(setup['scale'])) != 1
            if conormal and anisotropic and 'normal_planes' not in batch:
                # Ambient A^-T is sufficient for codimension-one surface normals.
                # Edge co-normals additionally need their parent plane's frame;
                # Older datasets do not retain those per-edge plane frames.
                record.pop('normals')
                record.pop('normal_kind', None)
                record['normal_status'] = 'unavailable'
                record['normal_note'] = ('Non-uniform scaling of planar boundary co-normals requires '
                                         'the original plane frame. Points were transformed, but no guessed normals are exported.')
            else:
                inverse = np.linalg.inv(linear)
                normals = original_normals @ inverse
                if conormal and 'normal_planes' in batch:
                    tags = np.asarray(batch['entity_tags'])
                    transformed_planes = {}
                    for tag, values in batch['normal_planes'].items():
                        plane = np.asarray(values, dtype=float)
                        mapped_plane = plane @ inverse
                        mapped_plane /= np.linalg.norm(mapped_plane)
                        transformed_planes[tag] = mapped_plane.tolist()
                        mask = tags == int(tag)
                        # Transport the in-plane tangent and plane normal, then
                        # recover the outward co-normal within the mapped plane.
                        tangent = np.cross(plane, original_normals[mask]) @ linear.T
                        normals[mask] = np.cross(tangent, mapped_plane)
                    record['normal_planes'] = transformed_planes
                lengths = np.linalg.norm(normals, axis=1, keepdims=True)
                if not np.isfinite(normals).all() or np.any(lengths == 0):
                    raise ValueError('Transform produced invalid boundary normals.')
                record['normals'] = (normals / lengths).tolist()
        record['parametrisation'] = {
            'schema_version': setup['schema_version'], 'operation': 'affine' if affine else 'translation',
            'scope': setup['scope'], 'affected': affected,
            'parameters': dict(zip(('translation_x', 'translation_y', 'translation_z'), setup['translation'])),
            'regions': list(setup['regions']),
            'domain_consistency': ('consistent affine transform' if affine else 'consistent translation') if setup['scope'] == 'domain' else 'unverified',
        }
        if affine:
            # The full design definition is stored once in the export manifest,
            # rather than duplicating potentially long explicit lists per region.
            record_setup = {key: deepcopy(value) for key, value in setup.items() if key != 'design'}
            record['parametrisation'].update(
                setup=record_setup, matrix=_matrix(linear, offset).tolist(),
                order=list(setup['order']), frame=setup['frame'],
                density_note='Scaling changes spatial point density; point counts are preserved and no integration/Jacobian weights are supplied.')
        result.append(record)
    return result


def save_setup(setup, path):
    validate_setup(setup)
    # Setup contains references and parameters, never point-cloud output.
    from .export import atomic_write
    atomic_write(path, lambda temporary: temporary.write_text(json.dumps(setup, indent=2, allow_nan=False) + '\n'))


def load_setup(path):
    from pathlib import Path
    setup = json.loads(Path(path).read_text())
    if not isinstance(setup, dict):
        raise ValueError('Invalid parametrisation setup.')
    validate_setup(setup)
    return setup


def export_configuration(batches, setup, path):
    """Export one configuration with named parameter values and region metadata."""
    from pathlib import Path
    import xarray as xr
    from .export import to_xarray, atomic_write
    path = Path(path)
    if path.suffix.lower() not in ('.nc', '.npz'):
        raise ValueError('Choose a NetCDF (.nc) or NumPy (.npz) file.')
    records = transform_batches(batches, setup)
    dataset = to_xarray(records)
    dataset.coords['_gs_translation'] = xr.DataArray(
        setup['translation'], dims='_gs_parameter',
        coords={'_gs_parameter': ['translation_x', 'translation_y', 'translation_z']})
    if setup['schema_version'] == 2:
        linear, offset = affine_transform(setup)
        dataset.coords['_gs_transform_matrix'] = xr.DataArray(
            _matrix(linear, offset), dims=('_gs_matrix_row', '_gs_matrix_column'))
        for name, field in [('scale', 'scale'), ('pivot', 'pivot'), ('rotation_axis', 'rotation_axis')]:
            dataset.coords['_gs_' + name] = xr.DataArray(setup[field], dims='_gs_xyz')
        dataset.coords['_gs_rotation_angle'] = xr.DataArray(
            setup['rotation_angle'], attrs={'angle_units': setup['angle_units']})
    if 'design_configuration' in setup:
        parameters = setup['design_configuration']['parameters']
        dataset.coords['_gs_design_parameter'] = list(parameters)
        dataset.coords['_gs_design_values'] = xr.DataArray(
            list(parameters.values()), dims='_gs_design_parameter')
        dataset.coords['_gs_design_units'] = xr.DataArray(
            [setup['design_configuration']['units'][name] for name in parameters], dims='_gs_design_parameter')
    manifest = json.loads(dataset.attrs['metadata_json'])
    manifest['parametrisation'] = setup
    manifest['configuration_count'] = 1
    manifest['parametrisation_schema_version'] = setup['schema_version']
    dataset.attrs['metadata_json'] = json.dumps(manifest, allow_nan=False)
    try:
        def write(temporary):
            if path.suffix.lower() == '.nc':
                dataset.to_netcdf(temporary, engine='scipy', format='NETCDF3_64BIT')
            else:
                arrays = {name: array.values for name, array in dataset.variables.items()}
                arrays['_gs_metadata'] = np.asarray(dataset.attrs['metadata_json'])
                with temporary.open('wb') as stream:
                    np.savez_compressed(stream, allow_pickle=False, **arrays)
        atomic_write(path, write)
    finally:
        dataset.close()
    return path
