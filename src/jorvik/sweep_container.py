"""Qt-independent xarray.DataTree builder and single-file consolidated sweep bundles."""
import json
from pathlib import Path
import re
import numpy as np
import xarray as xr
from .export import atomic_write
from .sweep_results import inspect_sweep, iter_sweep_datasets

_RESERVED_GROUPS = {'parameters', 'configurations', 'regions', 'xyz', 'configuration', 'parameter', 'point'}


def _safe_region_keys(labels):
    """Map arbitrary region labels (including spaces, slashes and Unicode) to collision-free group names."""
    mapping = {}
    used = set(_RESERVED_GROUPS)
    for index, label in enumerate(labels):
        if (re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', label)
                and not label.startswith('_gs_') and label not in used):
            key = label
        else:
            key = f'region_{index}'
            while key in used:
                index += 1
                key = f'region_{index}'
        used.add(key)
        mapping[label] = key
    return mapping


def to_datatree(path, *, allow_partial=False, require_integrity=False):
    """Load a sweep directory or consolidated bundle (.nc / .npz) into an xarray.DataTree."""
    path = Path(path).resolve()
    if path.is_file():
        return load_consolidated_sweep(path)
    run = inspect_sweep(path, allow_partial=allow_partial, require_integrity=require_integrity)
    configurations = list(iter_sweep_datasets(path, allow_partial=allow_partial, require_integrity=require_integrity))
    if not configurations:
        raise ValueError('No completed sweep configurations are available to build a DataTree.')
    return _datatree_from_configurations(run['manifest'], configurations)


def _datatree_from_configurations(manifest, configurations):
    config_ids = [item['id'] for item in configurations]
    param_names = list(configurations[0]['parameters'])
    param_units = [configurations[0]['units'][name] for name in param_names]
    param_values = np.asarray([[item['parameters'][name] for name in param_names]
                               for item in configurations], dtype=np.float64)
    first_datasets = configurations[0]['datasets']
    labels = [batch['label'] for batch in first_datasets]
    key_map = _safe_region_keys(labels)
    reverse_map = {key: label for label, key in key_map.items()}
    # Check whether every region keeps a constant point count across configurations.
    ragged = False
    for index, label in enumerate(labels):
        counts = {len(item['datasets'][index]['coordinates']) for item in configurations}
        if len(counts) != 1:
            ragged = True
            break
    root_ds = xr.Dataset(
        coords={'configuration': config_ids, 'xyz': ['x', 'y', 'z']},
        attrs={
            'schema_version': 1,
            'kind': 'jorvik_sweep_datatree',
            'ragged': int(ragged),
            'uniform_counts': int(not ragged),
            'source_hash': manifest['source_hash'],
            'region_map_json': json.dumps(reverse_map, ensure_ascii=False, allow_nan=False),
            'manifest_json': json.dumps(manifest, ensure_ascii=False, allow_nan=False),
        },
    )
    param_vars = {
        'values': xr.DataArray(param_values, dims=('configuration', 'parameter')),
    }
    for p_idx, p_name in enumerate(param_names):
        if p_name not in ('values', 'configuration', 'parameter', 'units'):
            param_vars[p_name] = xr.DataArray(
                param_values[:, p_idx], dims=('configuration',), attrs={'unit': param_units[p_idx]}
            )
    param_ds = xr.Dataset(
        data_vars=param_vars,
        coords={
            'configuration': config_ids,
            'parameter': param_names,
            'units': xr.DataArray(param_units, dims=('parameter',)),
        },
    )
    tree_dict = {'/': root_ds, '/parameters': param_ds}
    if not ragged:
        for index, label in enumerate(labels):
            safe_key = key_map[label]
            region_batches = [item['datasets'][index] for item in configurations]
            coords = np.stack([np.asarray(b['coordinates'], dtype=np.float64) for b in region_batches], axis=0)
            tags = np.stack([np.asarray(b['entity_tags'], dtype=np.int32) for b in region_batches], axis=0)
            meta = {k: v for k, v in region_batches[0].items()
                    if k not in ('coordinates', 'entity_tags', 'normals', 'weights', 'tangents', 'boundary_distances')}
            data_vars = {
                'coordinates': xr.DataArray(coords, dims=('configuration', 'point', 'xyz')),
                'entity_tags': xr.DataArray(tags, dims=('configuration', 'point')),
            }
            if all('normals' in b for b in region_batches):
                normals = np.stack([np.asarray(b['normals'], dtype=np.float64) for b in region_batches], axis=0)
                data_vars['normals'] = xr.DataArray(normals, dims=('configuration', 'point', 'xyz'))
            if all('weights' in b for b in region_batches):
                weights = np.stack([np.asarray(b['weights'], dtype=np.float64) for b in region_batches], axis=0)
                data_vars['weights'] = xr.DataArray(weights, dims=('configuration', 'point'))
            if all('boundary_distances' in b for b in region_batches):
                distances = np.stack([np.asarray(b['boundary_distances'], dtype=np.float64) for b in region_batches], axis=0)
                data_vars['boundary_distances'] = xr.DataArray(distances, dims=('configuration', 'point'))
            tree_dict[f'/regions/{safe_key}'] = xr.Dataset(
                data_vars=data_vars,
                coords={'configuration': config_ids, 'xyz': ['x', 'y', 'z']},
                attrs={
                    'label': label,
                    'safe_key': safe_key,
                    'entity_dimension': region_batches[0]['entity_dimension'],
                    'metadata_json': json.dumps(meta, ensure_ascii=False, allow_nan=False),
                },
            )
    else:
        for item in configurations:
            cid = item['id']
            for index, batch in enumerate(item['datasets']):
                label = batch['label']
                safe_key = key_map[label]
                meta = {k: v for k, v in batch.items()
                        if k not in ('coordinates', 'entity_tags', 'normals', 'weights', 'tangents', 'boundary_distances')}
                data_vars = {
                    'coordinates': xr.DataArray(np.asarray(batch['coordinates'], dtype=np.float64), dims=('point', 'xyz')),
                    'entity_tags': xr.DataArray(np.asarray(batch['entity_tags'], dtype=np.int32), dims=('point',)),
                }
                if 'normals' in batch:
                    data_vars['normals'] = xr.DataArray(np.asarray(batch['normals'], dtype=np.float64), dims=('point', 'xyz'))
                if 'weights' in batch:
                    data_vars['weights'] = xr.DataArray(np.asarray(batch['weights'], dtype=np.float64), dims=('point',))
                if 'boundary_distances' in batch:
                    data_vars['boundary_distances'] = xr.DataArray(np.asarray(batch['boundary_distances'], dtype=np.float64), dims=('point',))
                tree_dict[f'/configurations/{cid}/regions/{safe_key}'] = xr.Dataset(
                    data_vars=data_vars,
                    coords={'xyz': ['x', 'y', 'z']},
                    attrs={
                        'configuration_id': cid,
                        'label': label,
                        'safe_key': safe_key,
                        'entity_dimension': batch['entity_dimension'],
                        'metadata_json': json.dumps(meta, ensure_ascii=False, allow_nan=False),
                    },
                )
    return xr.DataTree.from_dict(tree_dict)


def export_consolidated_sweep(sweep_dir, destination, *, allow_partial=False, require_integrity=False):
    """Export all verified configurations from a sweep folder into one atomic .nc or .npz bundle."""
    destination = Path(destination)
    suffix = destination.suffix.lower()
    if suffix not in ('.nc', '.npz'):
        raise ValueError('Choose a NetCDF (.nc) or NumPy (.npz) consolidated sweep file.')
    tree = to_datatree(sweep_dir, allow_partial=allow_partial, require_integrity=require_integrity)
    flat_vars, flat_coords, node_records = {}, {'xyz': ['x', 'y', 'z']}, []
    flat_coords['_gs_configuration'] = tree.coords['configuration'].values
    flat_coords['_gs_parameter'] = tree['parameters'].coords['parameter'].values
    flat_coords['_gs_parameter_units'] = xr.DataArray(
        tree['parameters'].coords['units'].values, dims=('_gs_parameter',))
    flat_vars['_gs_parameter_values'] = xr.DataArray(
        tree['parameters']['values'].values, dims=('_gs_configuration', '_gs_parameter'))
    ragged = bool(tree.attrs.get('ragged', 0))
    if not ragged:
        for index, (safe_key, node) in enumerate(tree['regions'].children.items()):
            ds = node.to_dataset()
            pdim = f'_gs_points_{index}'
            prefix = f'_gs_r{index}_'
            flat_vars[prefix + 'coordinates'] = xr.DataArray(
                ds['coordinates'].values, dims=('_gs_configuration', pdim, 'xyz'))
            flat_coords[prefix + 'entity_tags'] = xr.DataArray(
                ds['entity_tags'].values, dims=('_gs_configuration', pdim))
            entry = {
                'path': f'/regions/{safe_key}',
                'label': ds.attrs['label'],
                'safe_key': safe_key,
                'entity_dimension': int(ds.attrs['entity_dimension']),
                'metadata_json': ds.attrs['metadata_json'],
                'coordinates': prefix + 'coordinates',
                'entity_tags': prefix + 'entity_tags',
            }
            for field, dims in (('normals', ('_gs_configuration', pdim, 'xyz')),
                                ('weights', ('_gs_configuration', pdim)),
                                ('boundary_distances', ('_gs_configuration', pdim))):
                if field in ds:
                    flat_coords[prefix + field] = xr.DataArray(ds[field].values, dims=dims)
                    entry[field] = prefix + field
            node_records.append(entry)
    else:
        slot = 0
        for cid, cnode in tree['configurations'].children.items():
            for safe_key, rnode in cnode['regions'].children.items():
                ds = rnode.to_dataset()
                pdim = f'_gs_points_{slot}'
                prefix = f'_gs_c{slot}_'
                slot += 1
                flat_vars[prefix + 'coordinates'] = xr.DataArray(
                    ds['coordinates'].values, dims=(pdim, 'xyz'))
                flat_coords[prefix + 'entity_tags'] = xr.DataArray(
                    ds['entity_tags'].values, dims=(pdim,))
                entry = {
                    'path': f'/configurations/{cid}/regions/{safe_key}',
                    'configuration_id': cid,
                    'label': ds.attrs['label'],
                    'safe_key': safe_key,
                    'entity_dimension': int(ds.attrs['entity_dimension']),
                    'metadata_json': ds.attrs['metadata_json'],
                    'coordinates': prefix + 'coordinates',
                    'entity_tags': prefix + 'entity_tags',
                }
                for field, dims in (('normals', (pdim, 'xyz')),
                                    ('weights', (pdim,)),
                                    ('boundary_distances', (pdim,))):
                    if field in ds:
                        flat_coords[prefix + field] = xr.DataArray(ds[field].values, dims=dims)
                        entry[field] = prefix + field
                node_records.append(entry)
    bundle_meta = {
        'schema_version': 1,
        'kind': 'jorvik_consolidated_sweep',
        'ragged': int(ragged),
        'source_hash': tree.attrs['source_hash'],
        'region_map_json': tree.attrs['region_map_json'],
        'manifest_json': tree.attrs['manifest_json'],
        'nodes': node_records,
    }
    bundle_json = json.dumps(bundle_meta, ensure_ascii=False, allow_nan=False)
    flat_ds = xr.Dataset(flat_vars, coords=flat_coords, attrs={
        'schema_version': 1,
        'kind': 'jorvik_consolidated_sweep',
        'bundle_json': bundle_json,
    })
    try:
        def write(temporary):
            if suffix == '.nc':
                flat_ds.to_netcdf(temporary, engine='scipy', format='NETCDF3_64BIT')
            else:
                arrays = {name: var.values for name, var in flat_ds.variables.items()}
                arrays['_gs_bundle'] = np.asarray(bundle_json)
                with temporary.open('wb') as stream:
                    np.savez_compressed(stream, allow_pickle=False, **arrays)
        atomic_write(destination, write)
    finally:
        flat_ds.close()
    return destination


def load_consolidated_sweep(path):
    """Reconstruct an xarray.DataTree from a consolidated .nc or .npz sweep bundle."""
    path = Path(path).resolve()
    suffix = path.suffix.lower()
    if suffix == '.nc':
        with xr.open_dataset(path, engine='scipy') as ds:
            bundle = json.loads(ds.attrs['bundle_json'])
            arrays = {name: var.values.copy() for name, var in ds.variables.items()}
    elif suffix == '.npz':
        with np.load(path, allow_pickle=False) as data:
            bundle = json.loads(data['_gs_bundle'].item())
            arrays = {name: data[name].copy() for name in data.files if name != '_gs_bundle'}
    else:
        raise ValueError('Unsupported consolidated sweep file format.')
    if not isinstance(bundle, dict) or bundle.get('kind') != 'jorvik_consolidated_sweep' or bundle.get('schema_version') != 1:
        raise ValueError('Invalid consolidated sweep bundle metadata.')
    config_ids = [str(v) for v in arrays['_gs_configuration'].tolist()]
    param_names = [str(v) for v in arrays['_gs_parameter'].tolist()]
    param_units = [str(v) for v in arrays['_gs_parameter_units'].tolist()]
    param_values = np.asarray(arrays['_gs_parameter_values'], dtype=np.float64)
    ragged = bool(bundle.get('ragged', 0))
    root_ds = xr.Dataset(
        coords={'configuration': config_ids, 'xyz': ['x', 'y', 'z']},
        attrs={
            'schema_version': 1,
            'kind': 'jorvik_sweep_datatree',
            'ragged': int(ragged),
            'uniform_counts': int(not ragged),
            'source_hash': bundle['source_hash'],
            'region_map_json': bundle['region_map_json'],
            'manifest_json': bundle['manifest_json'],
        },
    )
    param_vars = {'values': xr.DataArray(param_values, dims=('configuration', 'parameter'))}
    for p_idx, p_name in enumerate(param_names):
        if p_name not in ('values', 'configuration', 'parameter', 'units'):
            param_vars[p_name] = xr.DataArray(
                param_values[:, p_idx], dims=('configuration',), attrs={'unit': param_units[p_idx]}
            )
    param_ds = xr.Dataset(
        data_vars=param_vars,
        coords={
            'configuration': config_ids,
            'parameter': param_names,
            'units': xr.DataArray(param_units, dims=('parameter',)),
        },
    )
    tree_dict = {'/': root_ds, '/parameters': param_ds}
    for entry in bundle['nodes']:
        if not ragged:
            data_vars = {
                'coordinates': xr.DataArray(arrays[entry['coordinates']], dims=('configuration', 'point', 'xyz')),
                'entity_tags': xr.DataArray(arrays[entry['entity_tags']], dims=('configuration', 'point')),
            }
            for field, dims in (('normals', ('configuration', 'point', 'xyz')),
                                ('weights', ('configuration', 'point')),
                                ('boundary_distances', ('configuration', 'point'))):
                if field in entry:
                    data_vars[field] = xr.DataArray(arrays[entry[field]], dims=dims)
            tree_dict[entry['path']] = xr.Dataset(
                data_vars=data_vars,
                coords={'configuration': config_ids, 'xyz': ['x', 'y', 'z']},
                attrs={
                    'label': entry['label'],
                    'safe_key': entry['safe_key'],
                    'entity_dimension': entry['entity_dimension'],
                    'metadata_json': entry['metadata_json'],
                },
            )
        else:
            data_vars = {
                'coordinates': xr.DataArray(arrays[entry['coordinates']], dims=('point', 'xyz')),
                'entity_tags': xr.DataArray(arrays[entry['entity_tags']], dims=('point',)),
            }
            for field, dims in (('normals', ('point', 'xyz')),
                                ('weights', ('point',)),
                                ('boundary_distances', ('point',))):
                if field in entry:
                    data_vars[field] = xr.DataArray(arrays[entry[field]], dims=dims)
            tree_dict[entry['path']] = xr.Dataset(
                data_vars=data_vars,
                coords={'xyz': ['x', 'y', 'z']},
                attrs={
                    'configuration_id': entry['configuration_id'],
                    'label': entry['label'],
                    'safe_key': entry['safe_key'],
                    'entity_dimension': entry['entity_dimension'],
                    'metadata_json': entry['metadata_json'],
                },
            )
    return xr.DataTree.from_dict(tree_dict)
