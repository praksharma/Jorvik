"""Sequential, bounded configuration exports with explicit partial-run records.

Only scalar plans are retained across configurations. Point clouds are transformed
and released by the ordinary exporter one configuration at a time. Existing output
directories are never reused; partial runs are deliberately not resumable yet.
"""
from copy import deepcopy
import json
from pathlib import Path
import re
import time

from .export import atomic_write, to_xarray
from .parametrisation import export_configuration, validate_setup
from .parameter_design import MAX_CONFIGURATIONS

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
                   'rotation_axis', 'rotation_angle', 'angle_units', 'scale', 'pivot', 'order', 'frame')
    core = {key: value for key, value in base_setup.items() if key in core_fields}
    _json(base_setup)
    identities, plan = set(), []
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
                if target.startswith('translation_'):
                    expected['translation']['xyz'.index(target[-1])] = value
                elif target.startswith('scale_'):
                    expected['scale']['xyz'.index(target[-1])] = value
                elif target == 'rotation_angle': expected['rotation_angle'] = value
                else: expected['scale'] = [value, value, value]
            if any(scalar.get(key) != value for key, value in expected.items()):
                raise ValueError('Configuration transform differs from the base setup and declared parameters.')
        else:
            raise ValueError('Sweep export requires a declared parameter design in its base setup.')
        if any(key in scalar for key in ('coordinates', 'entity_tags', 'normals')):
            raise ValueError('Configuration plans must not contain point-cloud arrays.')
        plan.append({'id': identity, 'parameters': record['parameters'], 'setup': scalar,
                     'filename': identity + '.' + file_format})
    # Serialization failures also precede destination creation.
    _json(base_setup); _json(plan)
    return plan


def export_sweep(batches, base_setup, configurations, destination, *,
                 file_format='nc', fail_fast=True, cancelled=None, progress=None):
    """Export a generated scalar plan to a new directory and return its manifest.

    Cancellation is cooperative between files: the current atomic shard finishes
    before stopping. A failed shard is recorded and either stops the run or lets
    later configurations proceed. A callback receives small manifest snapshots
    initially, after each attempt, and after final status is written. This API
    never changes the viewport or the borrowed original datasets.
    """
    if cancelled is not None and not callable(cancelled): raise ValueError('Invalid cancellation callback.')
    if progress is not None and not callable(progress): raise ValueError('Invalid progress callback.')
    plan = _prepare(batches, base_setup, configurations, file_format, fail_fast)
    started = time.monotonic()
    destination = Path(destination)
    # Exclusive creation protects existing complete and interrupted runs alike.
    destination.mkdir()
    manifest_path = destination / 'manifest.json'
    summary = [{key: deepcopy(value) for key, value in batch.items()
                if key not in ('coordinates', 'entity_tags', 'normals')} for batch in batches]
    manifest = {'schema_version': SCHEMA_VERSION, 'kind': 'jorvik_parameter_sweep',
                'status': 'running', 'format': file_format,
                'configuration_count': len(plan), 'attempted_count': 0,
                'succeeded_count': 0, 'failed_count': 0, 'pending_count': len(plan),
                'all_outputs_complete': False, 'base_setup': deepcopy(base_setup),
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
                       if key not in ('base_setup', 'datasets')}
            progress(dict(payload, current_id=current_id, current_error=current_error))

    try:
        _write_json(destination / manifest['plan'], {'schema_version': SCHEMA_VERSION, 'configurations': plan})
        checkpoint()
        with (destination / manifest['results']).open('x', encoding='utf-8') as journal:
            for record in plan:
                if cancelled is not None and cancelled():
                    manifest['status'] = 'cancelled'
                    break
                current_id = record['id']
                current_error = None
                result = {'id': current_id, 'status': 'complete',
                          'point_counts': {batch['label']: batch['count'] for batch in batches}}
                try:
                    export_configuration(batches, record['setup'], destination / record['filename'])
                except Exception as error:
                    result.update(status='failed', error=str(error) or type(error).__name__)
                    current_error = result['error']
                    manifest['failed_count'] += 1
                else:
                    result['filename'] = record['filename']
                    manifest['succeeded_count'] += 1
                manifest['attempted_count'] += 1
                journal.write(_json(result) + '\n')
                journal.flush()
                checkpoint()
                if result['status'] == 'failed' and fail_fast:
                    manifest['status'] = 'failed'
                    break
            else:
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
