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
        if 'geo_parameters' in setup:
            geo_params = setup['geo_parameters']
            if (not isinstance(geo_params, list)
                    or any(not isinstance(item, dict) or not isinstance(item.get('name'), str)
                           or type(item.get('default')) is bool or type(item.get('default')) not in (int, float)
                           or not np.isfinite(float(item['default'])) for item in geo_params)):
                raise ValueError('Invalid .geo parameter declarations in setup.')
        if 'geo_overrides' in setup:
            from .cad_regeneration import validate_geo_overrides
            validate_geo_overrides(setup['geo_overrides'], setup.get('geo_parameters'))
        if 'region_transforms' in setup:
            steps = setup['region_transforms']
            if not isinstance(steps, list) or len(steps) > 32:
                raise ValueError('region_transforms must be a list of at most 32 transform steps.')
            for step in steps:
                if not isinstance(step, dict):
                    raise ValueError('Invalid region transform step.')
                step_regions = step.get('regions')
                if (not isinstance(step_regions, list) or not step_regions
                        or any(not isinstance(r, str) or not r.strip() for r in step_regions)
                        or len(set(step_regions)) != len(step_regions)):
                    raise ValueError('Each region transform step needs a non-empty list of unique region labels.')
                _vector(step.get('translation'), 'Step translation')
                s_axis = _vector(step.get('rotation_axis', [0.0, 0.0, 1.0]), 'Step rotation axis')
                if not np.any(s_axis):
                    raise ValueError('Step rotation axis must be nonzero.')
                s_angle = step.get('rotation_angle', 0.0)
                if type(s_angle) is bool or type(s_angle) not in (int, float) or not np.isfinite(float(s_angle)):
                    raise ValueError('Step rotation angle must be finite.')
                if step.get('angle_units', 'degrees') not in ('degrees', 'radians'):
                    raise ValueError('Choose degrees or radians for step rotation.')
                s_scales = _vector(step.get('scale', [1.0, 1.0, 1.0]), 'Step scale')
                if np.any(s_scales < 1e-8) or np.any(s_scales > 1e8):
                    raise ValueError('Step scale factors must be positive and between 1e-8 and 1e8.')
                _vector(step.get('pivot', [0.0, 0.0, 0.0]), 'Step pivot')
                if step.get('order', ['scale', 'rotate', 'translate']) != ['scale', 'rotate', 'translate'] or step.get('frame', 'world') != 'world':
                    raise ValueError('Use scale, rotate, translate order in the world coordinate frame.')
                _affine_components({
                    'schema_version': 2,
                    'translation': step['translation'],
                    'rotation_axis': step.get('rotation_axis', [0.0, 0.0, 1.0]),
                    'rotation_angle': float(s_angle),
                    'angle_units': step.get('angle_units', 'degrees'),
                    'scale': step.get('scale', [1.0, 1.0, 1.0]),
                    'pivot': step.get('pivot', [0.0, 0.0, 0.0]),
                })
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
            from .parameter_design import TARGETS, is_geo_target, is_region_target, parse_region_target
            if not isinstance(bindings, dict) or set(bindings) != set(parameters):
                raise ValueError('Invalid named parameter bindings.')
            targets = list(bindings.values())
            if (any(not isinstance(target, str) or (target not in TARGETS and not is_geo_target(target) and not is_region_target(target)) for target in targets)
                    or len(set(targets)) != len(targets)
                    or 'uniform_scale' in targets and any(target.startswith('scale_') for target in targets)):
                raise ValueError('Invalid or overlapping named parameter bindings.')
            for name, target in bindings.items():
                value = parameters[name]
                if target.startswith('geo:'):
                    actual = setup.get('geo_overrides', {}).get(target[4:])
                elif is_region_target(target):
                    reg_label, base_target = parse_region_target(target)
                    step = next((s for s in setup.get('region_transforms', []) if s.get('step_id') == f'design:{reg_label}'), None)
                    if step is None:
                        raise ValueError(f'Missing region transform step for {reg_label!r}.')
                    if base_target.startswith('translation_'):
                        actual = step['translation']['xyz'.index(base_target[-1])]
                    elif base_target.startswith('scale_'):
                        actual = step['scale']['xyz'.index(base_target[-1])]
                    elif base_target == 'rotation_angle':
                        actual = step['rotation_angle']
                    else:
                        if any(factor != value for factor in step['scale']):
                            raise ValueError('Uniform scale parameter differs from the actual region transform.')
                        actual = value
                elif target.startswith('translation_'):
                    actual = setup['translation']['xyz'.index(target[-1])]
                elif target.startswith('scale_'):
                    actual = setup['scale']['xyz'.index(target[-1])]
                elif target == 'rotation_angle': actual = setup['rotation_angle']
                else:
                    if any(factor != value for factor in setup['scale']):
                        raise ValueError('Uniform scale parameter differs from the actual transform.')
                    actual = value
                effective_target = parse_region_target(target)[1] if is_region_target(target) else target
                expected_unit = ('source' if (effective_target.startswith('translation_') or target.startswith('geo:')) else
                                 setup['angle_units'] if effective_target == 'rotation_angle' else 'dimensionless')
                if actual != value or units[name] != expected_unit:
                    raise ValueError('Named parameter provenance differs from the actual transform.')
    if batches is not None:
        if not batches:
            raise ValueError('Generate samples before processing configurations.')
        identity = (batches[0]['document'], batches[0]['source_hash'])
        if (setup['source_hash'] != identity[1]
                or any((b['document'], b['source_hash']) != identity for b in batches)):
            raise ValueError('This setup belongs to a different geometry. Create a new setup for this model.')
        known_labels = {b['label'] for b in batches}
        if set(labels) - known_labels:
            raise ValueError('Some setup regions are missing or renamed. Select the regions again.')
        for step in setup.get('region_transforms', []):
            if set(step['regions']) - known_labels:
                raise ValueError('A region transform step references a missing or renamed region.')
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


def _region_affine_chain(label, setup, base_linear, base_offset):
    """Compose the base transform and any sequential region_transforms steps targeting label."""
    affected = setup['scope'] == 'domain' or label in setup['regions']
    net_linear = base_linear.copy() if affected else np.eye(3)
    net_offset = base_offset.copy() if affected else np.zeros(3)
    steps_applied = 1 if affected else 0
    if setup['schema_version'] == 2:
        for step in setup.get('region_transforms', []):
            if label in step['regions']:
                step_setup = {
                    'schema_version': 2,
                    'translation': step['translation'],
                    'rotation_axis': step.get('rotation_axis', [0.0, 0.0, 1.0]),
                    'rotation_angle': float(step.get('rotation_angle', 0.0)),
                    'angle_units': step.get('angle_units', 'degrees'),
                    'scale': step.get('scale', [1.0, 1.0, 1.0]),
                    'pivot': step.get('pivot', [0.0, 0.0, 0.0]),
                }
                s_lin, s_off = _affine_components(step_setup)
                net_linear = s_lin @ net_linear
                net_offset = s_lin @ net_offset + s_off
                affected = True
                steps_applied += 1
    return affected, net_linear, net_offset, steps_applied


def verify_domain_consistency(batches, setup, transformed_batches=None):
    """Check domain/boundary consistency, shared-entity coherence and outside-domain displacements."""
    try:
        if transformed_batches is None:
            transformed_batches = transform_batches(batches, setup)
    except Exception as error:
        return {
            'status': 'invalid_geometry',
            'shared_boundary_status': 'invalid',
            'outside_regions': [],
            'warnings': [str(error)],
        }
    matrices = [np.asarray(b['parametrisation'].get('matrix', np.eye(4)), dtype=float) for b in transformed_batches]
    all_equal = all(np.allclose(m, matrices[0], atol=1e-12) for m in matrices[1:]) and all(
        b['parametrisation']['affected'] for b in transformed_batches
    )
    if all_equal:
        return {
            'status': 'consistent',
            'shared_boundary_status': 'consistent',
            'outside_regions': [],
            'warnings': [],
        }
    warnings = ['Independent region transformation leaves boundary/interior geometric consistency unverified unless the CAD domain is regenerated.']
    shared_status = 'consistent'
    for i in range(len(transformed_batches)):
        ent_i = {tuple(e) for e in transformed_batches[i].get('entities', [])}
        for j in range(i + 1, len(transformed_batches)):
            ent_j = {tuple(e) for e in transformed_batches[j].get('entities', [])}
            if (ent_i & ent_j) and not np.allclose(matrices[i], matrices[j], atol=1e-12):
                shared_status = 'inconsistent_shared_entity'
                warnings.append(
                    f"Regions {transformed_batches[i]['label']!r} and {transformed_batches[j]['label']!r} share CAD entities but received different transformations."
                )
    interiors = [b for b in transformed_batches if b.get('target') == 'interior' or b.get('entity_dimension') == 3]
    outside_regions = []
    if interiors:
        int_mat = np.asarray(interiors[0]['parametrisation'].get('matrix', np.eye(4)), dtype=float)
        env_batches = [
            b for b, m in zip(transformed_batches, matrices)
            if np.allclose(m, int_mat, atol=1e-12)
        ]
        env_pts = np.vstack([np.asarray(b['coordinates'], dtype=float) for b in env_batches])
        low, high = env_pts.min(axis=0), env_pts.max(axis=0)
        diag = max(float(np.linalg.norm(high - low)), 1e-9)
        tol = diag * 0.02
        for b, m in zip(transformed_batches, matrices):
            if np.allclose(m, int_mat, atol=1e-12):
                continue
            pts = np.asarray(b['coordinates'], dtype=float)
            if np.any(pts < low - tol) or np.any(pts > high + tol):
                outside_regions.append(b['label'])
    if outside_regions:
        warnings.append(f"Transformed points in {outside_regions} lie outside the interior domain envelope.")
        status = 'outside_domain'
    else:
        status = 'unverified'
    return {
        'status': status,
        'shared_boundary_status': shared_status,
        'outside_regions': outside_regions,
        'warnings': warnings,
    }


def transform_batches(batches, setup):
    """Evaluate original points, transporting surface normals and planar co-normals."""
    offset = validate_setup(setup, batches)
    if setup.get('geo_overrides'):
        from .cad_regeneration import regenerate_batches
        batches, _ = regenerate_batches(batches, setup['geo_overrides'])
    base_linear, base_offset = _affine_components(setup)
    affine = setup['schema_version'] == 2
    has_region_steps = bool(setup.get('region_transforms'))
    result = []
    for batch in batches:
        record = deepcopy(batch)
        affected, linear, offset, steps_applied = _region_affine_chain(
            batch['label'], setup, base_linear, base_offset
        )
        points = np.asarray(batch['coordinates'], dtype=float)
        if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
            raise ValueError('Coordinates must be finite non-empty N by 3 arrays.')
        with np.errstate(over='ignore', invalid='ignore'):
            transformed = (points @ linear.T + offset) if affected else points.copy()
        if not np.isfinite(transformed).all():
            raise ValueError('Transform produced non-finite coordinates.')
        record['coordinates'] = transformed.tolist()
        original_normals = None
        if affected and affine and 'normals' in batch:
            from .normals import validate_normals
            original_normals = validate_normals(batch)
            conormal = batch.get('normal_kind') == 'outward_conormal' or batch.get('entity_dimension') == 1
            svals = np.linalg.svd(linear, compute_uv=False)
            anisotropic = not np.allclose(svals, svals[0], rtol=1e-7, atol=1e-9)
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
        if affected and affine and 'weights' in batch:
            from .weights import transport_weights
            weight_updates = transport_weights(batch, linear, original_normals=original_normals)
            for key, val in weight_updates.items():
                if val is None:
                    record.pop(key, None)
                else:
                    record[key] = val
        if affected and affine and 'boundary_distances' in batch:
            from .refinement import transport_distances
            dist_updates = transport_distances(batch, linear, setup['scope'] == 'domain' and not has_region_steps)
            for key, val in dist_updates.items():
                if val is None:
                    record.pop(key, None)
                else:
                    record[key] = val
        whole_consistent = (setup['scope'] == 'domain' and not has_region_steps)
        record['parametrisation'] = {
            'schema_version': setup['schema_version'], 'operation': 'affine' if affine else 'translation',
            'scope': setup['scope'], 'affected': affected,
            'parameters': dict(zip(('translation_x', 'translation_y', 'translation_z'), setup['translation'])),
            'regions': list(setup['regions']),
            'domain_consistency': ('consistent affine transform' if affine else 'consistent translation') if whole_consistent else 'unverified',
        }
        if affine:
            # The full design definition is stored once in the export manifest,
            # rather than duplicating potentially long explicit lists per region.
            record_setup = {key: deepcopy(value) for key, value in setup.items() if key != 'design'}
            record['parametrisation'].update(
                setup=record_setup, matrix=_matrix(linear, offset).tolist(),
                order=list(setup['order']), frame=setup['frame'],
                density_note='Scaling changes spatial point density; point counts are preserved and no integration/Jacobian weights are supplied.')
            if has_region_steps:
                record['parametrisation']['applied_transform_steps'] = steps_applied
        result.append(record)
    if has_region_steps:
        consistency = verify_domain_consistency(batches, setup, result)
        for rec in result:
            rec['parametrisation']['consistency_check'] = consistency
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


def export_configuration(batches, setup, path, *, float_dtype='float64', cancelled=None):
    """Export one configuration with named parameter values and region metadata."""
    from pathlib import Path
    import xarray as xr
    from .export import to_xarray, atomic_write
    path = Path(path)
    if path.suffix.lower() not in ('.nc', '.npz'):
        raise ValueError('Choose a NetCDF (.nc) or NumPy (.npz) file.')
    records = transform_batches(batches, setup)
    dataset = to_xarray(records, float_dtype=float_dtype, cancelled=cancelled)
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
