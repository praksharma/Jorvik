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
    """Evaluate from original coordinates, mapping existing normals by A^-T."""
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
            if conormal and anisotropic:
                # Ambient A^-T is sufficient for codimension-one surface normals.
                # Edge co-normals additionally need their parent plane's frame;
                # original metadata does not retain that per-point information.
                record.pop('normals')
                record.pop('normal_kind', None)
                record['normal_status'] = 'unavailable'
                record['normal_note'] = ('Non-uniform scaling of planar boundary co-normals requires '
                                         'the original plane frame. Points were transformed, but no guessed normals are exported.')
            else:
                normals = original_normals @ np.linalg.inv(linear)
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
            record['parametrisation'].update(
                setup=deepcopy(setup), matrix=_matrix(linear, offset).tolist(),
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
