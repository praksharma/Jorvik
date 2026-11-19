"""Qt-independent labelled coordinates, xarray schema and atomic file export."""
import json
import os
from pathlib import Path
import re
import tempfile

import numpy as np
import xarray as xr

from . import __version__
from .normals import validate_normals
from .weights import validate_weights, validate_tangents
from .refinement import validate_distances
from .cad_import import validate_import_metadata, DEFAULT_UNITS

_PREFIX = '_gs_'
_RESERVED = {'xyz', 'file', 'allow_pickle', 'like'}
ARRAY_FIELDS = ('coordinates', 'entity_tags', 'normals', 'weights', 'tangents', 'boundary_distances')


def validate_batch_header(batch, labels, source_identity):
    validate_import_metadata(batch)
    label = batch['label']
    if not isinstance(label, str) or not label.strip():
        raise ValueError('Each dataset must have a non-empty label.')
    if label in labels:
        raise ValueError(f'Duplicate dataset label: {label}')
    labels.add(label)
    identity = (batch['document'], batch['source_hash'])
    if source_identity is not None and identity != source_identity:
        raise ValueError('Datasets must belong to the same source geometry.')
    dimension = batch['entity_dimension']
    if dimension not in (1, 2, 3):
        raise ValueError(f'{label}: point entity tags do not match the target entities.')
    return label, identity, dimension


def validate_points(batch, points=None):
    label = batch['label']
    points = np.asarray(batch['coordinates'] if points is None else points, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise ValueError(f'{label}: coordinates must be a finite, non-empty N×3 array.')
    if len(points) != batch['count']:
        raise ValueError(f'{label}: point count does not match coordinates.')
    return points


def validate_tags(batch, tags=None):
    label = batch['label']
    dimension = batch['entity_dimension']
    tags = np.asarray(batch['entity_tags'] if tags is None else tags)
    if tags.shape != (batch['count'],) or not np.issubdtype(tags.dtype, np.integer):
        raise ValueError(f'{label}: each point needs an integer entity tag.')
    if (tags <= 0).any() or (tags > np.iinfo(np.int32).max).any():
        raise ValueError(f'{label}: entity tags are outside the supported range.')
    if dimension not in (1, 2, 3) or not set(np.unique(tags).tolist()) <= {t for d, t in batch['entities'] if d == dimension}:
        raise ValueError(f'{label}: point entity tags do not match the target entities.')
    return tags


def to_xarray(batches):
    """One N×3 data variable per label, with independent point dimensions.

    Ordinary identifier labels are variable names. Other labels use a stable
    internal name, retaining the original label in attrs and the label map.
    """
    batches = list(batches)
    if not batches:
        raise ValueError('Generate samples before exporting.')
    variables, coordinates = {}, {'xyz': ['x', 'y', 'z']}
    labels, records = set(), []
    source_identity = None
    for index, batch in enumerate(batches):
        label, source_identity, dimension = validate_batch_header(batch, labels, source_identity)
        points = validate_points(batch)
        if points is batch['coordinates']:
            points = points.copy()
        tags = validate_tags(batch)
        tags_i32 = tags.astype(np.int32, copy=tags is batch['entity_tags'])
        name = label if (re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', label)
                         and not label.startswith(_PREFIX) and label not in _RESERVED) else f'{_PREFIX}region_{index}'
        point_dim = f'{_PREFIX}points_{index}'
        tag_name = f'{_PREFIX}entity_tags_{index}'
        metadata = {key: value for key, value in batch.items() if key not in ARRAY_FIELDS}
        metadata_json = json.dumps(metadata, ensure_ascii=False, allow_nan=False)
        variables[name] = xr.DataArray(points, dims=(point_dim, 'xyz'), attrs={
            'label': label, 'method': batch['method'], 'seed': batch['seed'],
            'entity_dimension': dimension, 'entity_tags_coordinate': tag_name,
            'metadata_json': metadata_json})
        coordinates[tag_name] = xr.DataArray(tags_i32, dims=point_dim,
                                            attrs={'entity_dimension': dimension})
        record = {'label': label, 'array': name, 'point_dimension': point_dim,
                  'entity_tags': tag_name, 'metadata': metadata}
        normals = validate_normals(batch, tags=tags_i32)
        del tags
        if normals is not None:
            if normals is batch['normals']:
                normals = normals.copy()
            normal_name = f'{_PREFIX}normals_{index}'
            coordinates[normal_name] = xr.DataArray(normals, dims=(point_dim, 'xyz'))
            variables[name].attrs['normals_coordinate'] = normal_name
            record['normals'] = normal_name
        weights = validate_weights(batch)
        if weights is not None:
            if weights is batch['weights']:
                weights = weights.copy()
            weight_name = f'{_PREFIX}weights_{index}'
            coordinates[weight_name] = xr.DataArray(weights, dims=point_dim)
            variables[name].attrs['weights_coordinate'] = weight_name
            record['weights'] = weight_name
        tangents = validate_tangents(batch)
        if tangents is not None:
            if tangents is batch['tangents']:
                tangents = tangents.copy()
            tangent_name = f'{_PREFIX}tangents_{index}'
            coordinates[tangent_name] = xr.DataArray(tangents, dims=(point_dim, 'xyz'))
            variables[name].attrs['tangents_coordinate'] = tangent_name
            record['tangents'] = tangent_name
        distances = validate_distances(batch)
        if distances is not None:
            if distances is batch['boundary_distances']:
                distances = distances.copy()
            distance_name = f'{_PREFIX}boundary_distances_{index}'
            coordinates[distance_name] = xr.DataArray(distances, dims=point_dim)
            variables[name].attrs['boundary_distances_coordinate'] = distance_name
            record['boundary_distances'] = distance_name
        records.append(record)
    units_set = {b.get('coordinate_units', DEFAULT_UNITS) for b in batches}
    coord_units = next(iter(units_set)) if len(units_set) == 1 else DEFAULT_UNITS
    manifest = {'schema_version': 2, 'jorvik_version': __version__,
                'coordinate_units': coord_units, 'datasets': records}
    return xr.Dataset(variables, coords=coordinates, attrs={
        'schema_version': 2, 'jorvik_version': __version__,
        'coordinate_units': manifest['coordinate_units'],
        'metadata_json': json.dumps(manifest, ensure_ascii=False, allow_nan=False)})


def export_samples(batches, path):
    """Write .nc or .npz, replacing the destination only after a complete write."""
    path = Path(path)
    if path.suffix.lower() not in ('.nc', '.npz'):
        raise ValueError('Choose a NetCDF (.nc) or NumPy (.npz) file.')
    dataset = to_xarray(batches)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', suffix='.tmp',
                                         dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
        if path.suffix.lower() == '.nc':
            dataset.to_netcdf(temporary, engine='scipy', format='NETCDF3_64BIT')
        else:
            arrays = {name: variable.values for name, variable in dataset.data_vars.items()}
            arrays.update({name: coord.values for name, coord in dataset.coords.items()})
            arrays[f'{_PREFIX}metadata'] = np.asarray(dataset.attrs['metadata_json'])
            with temporary.open('wb') as stream:
                np.savez_compressed(stream, allow_pickle=False, **arrays)
        os.replace(temporary, path)
        return path
    finally:
        dataset.close()
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write(path, writer):
    """Keep an existing destination intact when writing fails."""
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
        writer(temporary)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
