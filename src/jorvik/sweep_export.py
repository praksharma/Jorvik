"""Sequential, bounded configuration exports with explicit partial-run records.

Only scalar plans are retained across configurations. Point clouds are transformed
and released by the ordinary exporter one configuration at a time. Existing output
directories are never reused; continuation copies verified outputs into a new run.
"""
from copy import deepcopy
import json
import hashlib
import shutil

import numpy as np
from pathlib import Path
import re
import time

from .export import ARRAY_FIELDS, atomic_write, to_xarray
from .parametrisation import export_configuration, validate_setup
from .parameter_design import MAX_CONFIGURATIONS
from .sweep_integrity import file_integrity

SCHEMA_VERSION = 1


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _write_json(path, value):
    encoded = _json(value) + '\n'
    atomic_write(path, lambda temporary: temporary.write_text(encoded, encoding='utf-8'))


def _prepare(batches, base_setup, configurations, file_format, fail_fast):
    if file_format not in ('nc', 'npz'):
        raise ValueError('Choose nc or npz configuration shards.')
    if type(fail_fast) is not bool:
        raise ValueError('Fail-fast must be a boolean.')
    if not isinstance(configurations, (list, tuple)) or not 1 <= len(configurations) <= MAX_CONFIGURATIONS:
        raise ValueError(f'Supply 1 to {MAX_CONFIGURATIONS} generated configurations.')
    validate_setup(base_setup, batches)
    # Validate the original numeric data once before claiming an output directory.
    # This temporary dataset is not retained through the sweep.
    dataset = to_xarray(batches)
    dataset.close()
    del dataset
    # Validate long value definitions once, not once per configuration. Copy only
    # the tiny transform frame for each row; shared input arrays are never copied.
    definition = base_setup.get('design')
    bindings = ({parameter['name']: parameter['target'] for parameter in definition['parameters']}
                if definition is not None else None)
    units = ({parameter['name']: parameter['unit'] for parameter in definition['parameters']}
             if definition is not None else None)
    allowed = ({parameter['name']: (set(map(float, parameter['values'])) if parameter['mode'] == 'values'
                                    else (float(parameter['start']), float(parameter['stop'])))
                for parameter in definition['parameters']} if definition is not None else None)
    core_fields = ('schema_version', 'scope', 'regions', 'source_hash', 'translation',
                   'rotation_axis', 'rotation_angle', 'angle_units', 'scale', 'pivot', 'order', 'frame',
                   'geo_parameters', 'geo_overrides', 'region_transforms')
    core = {key: value for key, value in base_setup.items() if key in core_fields}
    _json(base_setup)
    identities, plan = set(), []
    from .parameter_design import _apply_value
    for record in configurations:
        if not isinstance(record, dict): raise ValueError('Invalid configuration record.')
        identity = record.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'configuration_[0-9]{6}', identity):
            raise ValueError('Configuration IDs must use configuration_ followed by six digits.')
        if identity in identities: raise ValueError('Duplicate configuration ID.')
        identities.add(identity)
        setup = record.get('setup')
        if not isinstance(setup, dict): raise ValueError('Invalid configuration setup.')
        scalar = {key: value for key, value in setup.items() if key != 'design'}
        validate_setup(scalar, batches)
        provenance = scalar.get('design_configuration')
        if (not isinstance(provenance, dict) or provenance.get('id') != identity
                or provenance.get('parameters') != record.get('parameters')):
            raise ValueError('Configuration identity or named values differ from its setup.')
        if scalar['source_hash'] != base_setup['source_hash']:
            raise ValueError('Configuration belongs to a different source geometry.')
        if definition is not None:
            if provenance.get('constraints', []) != definition.get('constraints', []):
                raise ValueError('Configuration constraints differ from the base design.')
            if (provenance.get('method') != definition['method'] or provenance.get('seed') != definition['seed']
                    or set(provenance['parameters']) != set(bindings) or provenance.get('units') != units
                    or provenance.get('bindings') != bindings):
                raise ValueError('Configuration provenance differs from the base design.')
            expected = deepcopy(core)
            for name, target in bindings.items():
                value = provenance['parameters'][name]
                domain = allowed[name]
                if (isinstance(domain, set) and value not in domain
                        or isinstance(domain, tuple) and not domain[0] <= value <= domain[1]):
                    raise ValueError('Configuration parameter is outside its declared design values.')
                _apply_value(expected, target, value)
            if any(scalar.get(key) != value for key, value in expected.items()):
                raise ValueError('Configuration transform differs from the base setup and declared parameters.')
        else:
            raise ValueError('Sweep export requires a declared parameter design in its base setup.')
        if any(key in scalar for key in ARRAY_FIELDS):
            raise ValueError('Configuration plans must not contain point-cloud arrays.')
        plan.append({'id': identity, 'parameters': record['parameters'], 'setup': scalar,
                     'filename': identity + '.' + file_format})
    if definition.get('constraints'):
        from .parameter_design import generate_design
        accepted = {record['id']: record['parameters'] for record in generate_design(base_setup, definition)['accepted']}
        if any(accepted.get(record['id']) != record['parameters'] for record in plan):
            raise ValueError('Constrained configuration IDs/values differ from the generated candidate sequence.')
    # Serialization failures also precede destination creation.
    _json(base_setup); _json(plan)
    return plan


def input_fingerprint(batches):
    """Hash ordered original numeric buffers and metadata, one buffer at a time."""
    digest = hashlib.sha256()
    for batch in batches:
        metadata = {key: value for key, value in batch.items()
                    if key not in ARRAY_FIELDS}
        encoded = json.dumps(metadata, sort_keys=True, ensure_ascii=False, allow_nan=False).encode('utf-8')
        digest.update(len(encoded).to_bytes(8, 'little')); digest.update(encoded)
        fields = [('coordinates', '<f8'), ('entity_tags', '<i4'), ('normals', '<f8')]
        for extra in ('weights', 'tangents', 'boundary_distances'):
            if extra in batch:
                fields.append((extra, '<f8'))
        for field, dtype in fields:
            digest.update(field.encode('ascii'))
            if field not in batch:
                digest.update(b'absent'); continue
            data = np.asarray(batch[field], dtype=dtype).reshape(-1)
            digest.update(data.size.to_bytes(8, 'little'))
            for start in range(0, data.size, 131072):
                digest.update(data[start:start+131072].tobytes())
            del data
    return digest.hexdigest()


def resume_sweep(batches, base_setup, configurations, source, destination, *,
                 fail_fast=True, cancelled=None, progress=None):
    """Continue a run into a new folder, retaining the source directory unchanged.

    Require matching original data/metadata, base setup and scalar plan, plus
    checksums and structural validation of every completed source file. Successful
    files are copied atomically; failed/pending entries are regenerated. Older
    runs without an input fingerprint cannot be continued safely.
    Cancellation during preflight raises InterruptedError without creating output.
    """
    from .sweep_results import inspect_sweep, iter_sweep_datasets
    _callbacks(cancelled, progress)
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if destination.exists(): raise FileExistsError('Choose a new output folder for the resumed run.')
    if source in destination.parents:
        raise ValueError('The new output folder must be outside the source run.')
    run = inspect_sweep(source, allow_partial=True, require_integrity=True)
    manifest = run['manifest']
    plan = _prepare(batches, base_setup, configurations, manifest['format'], fail_fast)
    fingerprint = input_fingerprint(batches)
    if manifest.get('input_fingerprint_version') != 1 or manifest.get('input_fingerprint') != fingerprint:
        raise ValueError('Original samples or metadata differ, or this older run has no input fingerprint. Start a new sweep.')
    if manifest['base_setup'] != base_setup:
        raise ValueError('The base setup or parameter design differs from the source run.')
    old_plan = json.loads((source / manifest['plan']).read_text())['configurations']
    if old_plan != plan: raise ValueError('The generated configuration plan differs from the source run.')
    if progress is not None:
        progress({'stage': 'verifying', 'verified_count': 0, 'verification_total': len(run['completed']),
                  'configuration_count': len(plan), 'format': manifest['format']})
    iterator = iter_sweep_datasets(source, allow_partial=True, require_integrity=True)
    try:
        for verified, record in enumerate(run['completed'], 1):
            if cancelled is not None and cancelled():
                raise InterruptedError('Resume cancelled before creating the output folder.')
            if next(iterator)['id'] != record['id']:
                raise ValueError('Completed source configurations differ from the saved plan.')
            if progress is not None:
                progress({'stage': 'verifying', 'verified_count': verified, 'verification_total': len(run['completed']),
                          'configuration_count': len(plan), 'format': manifest['format']})
    finally:
        iterator.close()
    if cancelled is not None and cancelled():
        raise InterruptedError('Resume cancelled before creating the output folder.')
    reuse = {record['id']: (source / record['filename'], run['checksums'][record['id']])
             for record in run['completed']}
    return _run_sweep(batches, base_setup, plan, destination, manifest['format'], fail_fast,
                      cancelled, progress, fingerprint, reuse, str(source))


def _callbacks(cancelled, progress):
    if cancelled is not None and not callable(cancelled): raise ValueError('Invalid cancellation callback.')
    if progress is not None and not callable(progress): raise ValueError('Invalid progress callback.')


def export_sweep(batches, base_setup, configurations, destination, *,
                 file_format='nc', fail_fast=True, cancelled=None, progress=None,
                 consolidated=False, max_workers=1, float_dtype='float64'):
    """Export a generated scalar plan to a new directory and return its manifest.

    Cancellation is cooperative between files: the current atomic shard finishes
    before stopping. A failed shard is recorded and either stops the run or lets
    later configurations proceed. A callback receives small manifest snapshots
    initially, after each attempt, and after final status is written. This API
    never changes the viewport or the borrowed original datasets.
    """
    _callbacks(cancelled, progress)
    if type(consolidated) is not bool:
        raise ValueError('consolidated must be a boolean.')
    if type(max_workers) is not int or not 1 <= max_workers <= 16:
        raise ValueError('max_workers must be an integer between 1 and 16.')
    if float_dtype not in ('float64', 'float32'):
        raise ValueError("float_dtype must be 'float64' or 'float32'.")
    plan = _prepare(batches, base_setup, configurations, file_format, fail_fast)
    manifest = _run_sweep(batches, base_setup, plan, destination, file_format, fail_fast,
                          cancelled, progress, input_fingerprint(batches), {}, None,
                          max_workers=max_workers, float_dtype=float_dtype)
    if consolidated and manifest['status'] == 'complete':
        from .sweep_container import export_consolidated_sweep
        bundle_name = f'sweep_bundle.{file_format}'
        export_consolidated_sweep(destination, Path(destination) / bundle_name,
                                  require_integrity=True, float_dtype=float_dtype, cancelled=cancelled)
        manifest['consolidated_bundle'] = bundle_name
        manifest['integrity']['consolidated_bundle'] = file_integrity(Path(destination) / bundle_name)
        _write_json(Path(destination) / 'manifest.json', manifest)
    return manifest


def _run_sweep(batches, base_setup, plan, destination, file_format, fail_fast,
               cancelled, progress, fingerprint, reuse, resumed_from, *,
               max_workers=1, float_dtype='float64'):
    started = time.monotonic()
    destination = Path(destination)
    # Exclusive creation protects existing complete and interrupted runs alike.
    destination.mkdir()
    manifest_path = destination / 'manifest.json'
    summary = [{key: deepcopy(value) for key, value in batch.items()
                if key not in ARRAY_FIELDS} for batch in batches]
    manifest = {'schema_version': SCHEMA_VERSION, 'kind': 'jorvik_parameter_sweep',
                'status': 'running', 'format': file_format,
                'configuration_count': len(plan), 'attempted_count': 0,
                'succeeded_count': 0, 'failed_count': 0, 'pending_count': len(plan),
                'all_outputs_complete': False, 'base_setup': deepcopy(base_setup),
                'input_fingerprint': fingerprint, 'input_fingerprint_version': 1, 'reused_count': 0, 'resumed_from': resumed_from,
                'source_hash': base_setup['source_hash'], 'datasets': summary,
                'fail_fast': fail_fast, 'elapsed_seconds': 0.,
                'plan': 'configurations.json', 'results': 'results.jsonl'}
    current_id = None
    current_error = None

    def checkpoint(notify=True):
        manifest['elapsed_seconds'] = time.monotonic() - started
        manifest['pending_count'] = len(plan) - manifest['attempted_count']
        _write_json(manifest_path, manifest)
        if notify and progress is not None:
            # The base design can be large: keep progress events small and never
            # send coordinate arrays or the complete plan through a GUI queue.
            payload = {key: value for key, value in manifest.items()
                       if key not in ('base_setup', 'datasets', 'design_filter')}
            progress(dict(payload, current_id=current_id, current_error=current_error))

    try:
        if base_setup['design'].get('constraints'):
            from .parameter_design import generate_design
            report = generate_design(base_setup, base_setup['design'])
            _write_json(destination / 'rejections.json', {'schema_version': 1, 'rejected': report['rejected']})
            manifest['design_filter'] = {'candidate_count': report['candidate_count'],
                                         'accepted_count': len(report['accepted']),
                                         'rejected_count': len(report['rejected']),
                                         'report': 'rejections.json', 'replacement': False,
                                         'coverage_note': 'Filtering changes random/LHS/Sobol coverage; no replacement draws.'}
            del report
        _write_json(destination / manifest['plan'], {'schema_version': SCHEMA_VERSION, 'configurations': plan})
        manifest['integrity'] = {'algorithm': 'sha256',
                                 'plan': file_integrity(destination / manifest['plan'])}
        if 'design_filter' in manifest:
            manifest['integrity']['rejections'] = file_integrity(destination / 'rejections.json')
        checkpoint()
        def _process_one(record):
            cid = record['id']
            res = {'id': cid, 'status': 'complete',
                   'point_counts': {batch['label']: batch['count'] for batch in batches}}
            try:
                if cid in reuse:
                    original, expected = reuse[cid]
                    def copy(temporary):
                        shutil.copyfile(original, temporary)
                        from .sweep_integrity import verify_integrity
                        verify_integrity(temporary, expected)
                    atomic_write(destination / record['filename'], copy)
                else:
                    if float_dtype == 'float64':
                        export_configuration(batches, record['setup'], destination / record['filename'])
                    else:
                        export_configuration(batches, record['setup'], destination / record['filename'],
                                             float_dtype=float_dtype)
                res['integrity'] = file_integrity(destination / record['filename'])
            except Exception as error:
                res.update(status='failed', error=str(error) or type(error).__name__)
            else:
                res['filename'] = record['filename']
                if cid in reuse:
                    res['reused'] = True
            return cid, res

        with (destination / manifest['results']).open('x', encoding='utf-8') as journal:
            stopped_early = False
            if max_workers == 1:
                for record in plan:
                    if cancelled is not None and cancelled():
                        manifest['status'] = 'cancelled'
                        stopped_early = True
                        break
                    current_id, result = _process_one(record)
                    current_error = result.get('error')
                    if result['status'] == 'complete':
                        manifest['succeeded_count'] += 1
                        if result.get('reused'):
                            manifest['reused_count'] += 1
                    else:
                        manifest['failed_count'] += 1
                    manifest['attempted_count'] += 1
                    journal.write(_json(result) + '\n')
                    journal.flush()
                    checkpoint()
                    if result['status'] == 'failed' and fail_fast:
                        manifest['status'] = 'failed'
                        stopped_early = True
                        break
            else:
                from concurrent.futures import ThreadPoolExecutor
                for start_idx in range(0, len(plan), max_workers):
                    if cancelled is not None and cancelled():
                        manifest['status'] = 'cancelled'
                        stopped_early = True
                        break
                    chunk = plan[start_idx:start_idx + max_workers]
                    with ThreadPoolExecutor(max_workers=min(max_workers, len(chunk))) as pool:
                        chunk_results = list(pool.map(_process_one, chunk))
                    for current_id, result in chunk_results:
                        current_error = result.get('error')
                        if result['status'] == 'complete':
                            manifest['succeeded_count'] += 1
                            if result.get('reused'):
                                manifest['reused_count'] += 1
                        else:
                            manifest['failed_count'] += 1
                        manifest['attempted_count'] += 1
                        journal.write(_json(result) + '\n')
                        journal.flush()
                        checkpoint()
                        if result['status'] == 'failed' and fail_fast:
                            manifest['status'] = 'failed'
                            stopped_early = True
                            break
                    if stopped_early:
                        break
            if not stopped_early:
                manifest['status'] = 'complete_with_errors' if manifest['failed_count'] else 'complete'
        manifest['all_outputs_complete'] = manifest['status'] == 'complete'
        checkpoint()
        return manifest
    except BaseException:
        # A failed journal/manifest write, callback or interruption must not leave
        # an on-disk claim of success. Disk failure can also prevent this marker;
        # an absent/running manifest is never a completed run.
        manifest['status'] = 'failed'
        manifest['all_outputs_complete'] = False
        try: checkpoint(notify=False)
        except Exception: pass
        raise
