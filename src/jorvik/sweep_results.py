"""Read sweep metadata and one configuration at a time, without Qt or pickle."""
import json
from pathlib import Path
import re
import numpy as np
from .parametrisation import validate_setup
from .export import to_xarray
from .sweep_integrity import validate_integrity, verify_integrity


def _file(root, name):
    if not isinstance(name, str) or Path(name).name != name or (root / name).resolve().parent != root:
        raise ValueError('Sweep files must remain inside their output folder.')
    return root / name


def inspect_sweep(path, *, allow_partial=False, require_integrity=False):
    """Validate scalar metadata; incomplete runs require explicit opt-in.

    Returns the manifest and completed plan records, failed IDs and pending IDs.
    Interrupted runs may have a truncated last journal line, which is ignored
    only with allow_partial=True. Other malformed records are rejected. Plan
    checksums are checked here; shard checksums are checked by the iterator.
    Legacy runs remain readable unless require_integrity=True.
    """
    root = Path(path).resolve()
    manifest = json.loads(_file(root, 'manifest.json').read_text())
    if (not isinstance(manifest, dict) or manifest.get('schema_version') != 1 or manifest.get('kind') != 'jorvik_parameter_sweep'
            or manifest.get('format') not in ('nc', 'npz')
            or manifest.get('plan') != 'configurations.json' or manifest.get('results') != 'results.jsonl'):
        raise ValueError('Unsupported sweep manifest.')
    integrity = manifest.get('integrity')
    if 'integrity' in manifest:
        if not isinstance(integrity, dict) or integrity.get('algorithm') != 'sha256':
            raise ValueError('Unsupported sweep integrity algorithm.')
        verify_integrity(_file(root, manifest['plan']), integrity.get('plan'))
    elif require_integrity:
        raise ValueError('This legacy sweep has no integrity checksums.')
    status = manifest.get('status')
    if status not in ('complete', 'complete_with_errors', 'failed', 'cancelled', 'running'):
        raise ValueError('Invalid sweep status.')
    if status != 'complete' and not allow_partial:
        raise ValueError('Run is incomplete or has errors; explicitly allow_partial=True to read successful files.')
    if manifest.get('all_outputs_complete') is not (status == 'complete'):
        raise ValueError('Inconsistent sweep completion flag.')
    validate_setup(manifest['base_setup'])
    if manifest['source_hash'] != manifest['base_setup']['source_hash']:
        raise ValueError('Sweep source identity differs from its setup.')
    plan = json.loads(_file(root, manifest['plan']).read_text())
    if not isinstance(plan, dict) or plan.get('schema_version') != 1 or not isinstance(plan.get('configurations'), list):
        raise ValueError('Unsupported sweep plan.')
    records = {}
    from .parameter_design import constraint_reasons, design_summary
    definition = manifest['base_setup'].get('design', {})
    rules = definition.get('constraints', [])
    candidate_limit = design_summary(definition)['count'] if rules else None
    for record in plan['configurations']:
        if not isinstance(record, dict): raise ValueError('Invalid sweep plan record.')
        identity = record['id']
        if not isinstance(identity, str) or not re.fullmatch(r'configuration_[0-9]{6}', identity) or identity in records:
            raise ValueError('Invalid or duplicate sweep configuration ID.')
        if record['filename'] != identity + '.' + manifest['format']:
            raise ValueError('Configuration filename differs from its ID.')
        _file(root, record['filename'])
        setup = record['setup']; validate_setup(setup)
        if (setup['source_hash'] != manifest['source_hash']
                or setup['design_configuration']['id'] != identity
                or setup['design_configuration']['parameters'] != record['parameters']):
            raise ValueError('Sweep configuration provenance differs from its plan.')
        if candidate_limit is not None and not 1 <= int(identity[14:]) <= candidate_limit:
            raise ValueError('Constrained configuration ID lies outside its candidate sequence.')
        if setup['design_configuration'].get('constraints', []) != rules or constraint_reasons(record['parameters'], rules):
            raise ValueError('Sweep configuration violates its declared design constraints.')
        records[identity] = record
    if not 1 <= len(records) <= 10000 or manifest.get('configuration_count') != len(records):
        raise ValueError('Sweep configuration count differs from its plan.')
    rejected = []
    if manifest['base_setup'].get('design', {}).get('constraints') and 'design_filter' not in manifest:
        raise ValueError('A constrained sweep is missing its rejection report.')
    if 'design_filter' in manifest:
        filtering = manifest['design_filter']
        if not isinstance(filtering, dict) or filtering.get('report') != 'rejections.json' or filtering.get('replacement') is not False:
            raise ValueError('Invalid design rejection report.')
        if integrity is None: raise ValueError('Design rejection reports require checksums.')
        report_path = _file(root, filtering['report'])
        verify_integrity(report_path, integrity.get('rejections'))
        report = json.loads(report_path.read_text())
        if not isinstance(report, dict) or report.get('schema_version') != 1 or not isinstance(report.get('rejected'), list):
            raise ValueError('Invalid design rejection report.')
        rejected = report['rejected']
        from .parameter_design import validate_design, constraint_reasons, design_summary
        definition = manifest['base_setup'].get('design')
        validate_design(definition, manifest['base_setup'])
        count = design_summary(definition)['count']
        if (len(rejected) > count or any(type(filtering.get(key)) is not int for key in ('candidate_count', 'accepted_count', 'rejected_count'))
                or len(records) > filtering.get('accepted_count', -1)
                or filtering.get('candidate_count') != count or filtering.get('rejected_count') != len(rejected)
                or filtering.get('accepted_count') != count - len(rejected)):
            raise ValueError('Design rejection counts differ from their report.')
        identities = set(records)
        for record in rejected:
            if (not isinstance(record, dict) or not isinstance(record.get('id'), str)
                    or not re.fullmatch(r'configuration_[0-9]{6}', record['id']) or record['id'] in identities
                    or not 1 <= int(record['id'][14:]) <= count
                    or not isinstance(record.get('parameters'), dict)):
                raise ValueError('Invalid or duplicate rejected configuration.')
            identities.add(record['id'])
            expected_names = {parameter['name'] for parameter in definition['parameters']}
            values = record['parameters']
            if set(values) != expected_names or any(type(value) not in (int, float) or not np.isfinite(value) for value in values.values()):
                raise ValueError('Invalid rejected configuration values.')
            reasons = constraint_reasons(values, definition.get('constraints', []))
            if not reasons or record.get('reasons') != reasons:
                raise ValueError('Rejected configuration reasons differ from the design constraints.')
    done, failed, seen = set(), set(), set()
    checksums = {}
    reused = 0
    journal_path = _file(root, manifest['results'])
    text = journal_path.read_text() if journal_path.exists() else ''
    lines = text.splitlines()
    for index, line in enumerate(lines):
        try: result = json.loads(line)
        except json.JSONDecodeError:
            if allow_partial and status == 'running' and index == len(lines)-1 and not text.endswith('\n'):
                break
            raise ValueError('Invalid sweep result journal.') from None
        if not isinstance(result, dict): raise ValueError('Invalid sweep result record.')
        identity = result.get('id')
        if identity not in records or identity in seen:
            raise ValueError('Unknown or duplicate configuration result.')
        seen.add(identity)
        if 'reused' in result and (type(result['reused']) is not bool or
                                   result['reused'] and result.get('status') != 'complete'):
            raise ValueError('Invalid reused configuration result.')
        if result.get('status') == 'complete':
            if result.get('filename') != records[identity]['filename']:
                raise ValueError('Result filename differs from its plan.')
            if not _file(root, result['filename']).is_file():
                raise ValueError('A completed configuration file is missing.')
            if integrity is not None:
                validate_integrity(result.get('integrity'))
                checksums[identity] = result['integrity']
            if result.get('reused'): reused += 1
            done.add(identity)
        elif result.get('status') == 'failed' and 'filename' not in result:
            failed.add(identity)
        else: raise ValueError('Invalid configuration result status.')
    counters = {'attempted_count': len(seen), 'succeeded_count': len(done),
                'failed_count': len(failed), 'pending_count': len(records)-len(seen)}
    for key, expected in counters.items():
        value = manifest.get(key)
        if type(value) is not int or value < 0 or value > len(records) or (status != 'running' and value != expected):
            raise ValueError('Sweep counters differ from its result journal.')
    if 'reused_count' in manifest:
        value = manifest['reused_count']
        if type(value) is not int or not 0 <= value <= len(records) or (status != 'running' and value != reused):
            raise ValueError('Sweep reused count differs from its result journal.')
    if status == 'complete' and (failed or len(done) != len(records)):
        raise ValueError('Complete sweep has missing or failed outputs.')
    return {'manifest': manifest, 'completed': [record for key, record in records.items() if key in done],
            'failed': [key for key in records if key in failed],
            'pending': [key for key in records if key not in seen], 'checksums': checksums, 'rejected': rejected}


def iter_sweep_datasets(path, *, allow_partial=False, require_integrity=False):
    """Verify shard bytes, then yield independent arrays in plan order.

    Checksums detect accidental changes, not malicious replacement of a run.
    Legacy runs have structural validation only unless require_integrity=True.
    """
    root = Path(path).resolve()
    run = inspect_sweep(root, allow_partial=allow_partial, require_integrity=require_integrity)
    for record in run['completed']:
        shard = _file(root, record['filename'])
        if record['id'] in run['checksums']:
            verify_integrity(shard, run['checksums'][record['id']])
        if run['manifest']['format'] == 'npz':
            with np.load(shard, allow_pickle=False) as data:
                metadata = json.loads(data['_gs_metadata'].item())
                arrays = {key: data[key].copy() for key in data.files if key != '_gs_metadata'}
        else:
            import xarray as xr
            with xr.open_dataset(shard, engine='scipy') as data:
                metadata = json.loads(data.attrs['metadata_json'])
                arrays = {key: value.values.copy() for key, value in data.variables.items()}
        if not isinstance(metadata, dict): raise ValueError('Invalid configuration metadata.')
        setup = metadata.get('parametrisation')
        if metadata.get('schema_version') != 2 or setup != record['setup']:
            raise ValueError('Configuration file provenance differs from its plan.')
        parameters = setup['design_configuration']['parameters']
        names = list(parameters)
        if (arrays['_gs_design_parameter'].tolist() != names
                or not np.array_equal(arrays['_gs_design_values'], list(parameters.values()))
                or arrays['_gs_design_units'].tolist() != [setup['design_configuration']['units'][key] for key in names]):
            raise ValueError('Configuration parameter arrays differ from its provenance.')
        batches = []
        for region in metadata['datasets']:
            batch = dict(region['metadata'])
            if batch.get('label') != region['label'] or batch.get('source_hash') != run['manifest']['source_hash']:
                raise ValueError('Dataset label or source identity differs from the sweep.')
            batch['coordinates'] = arrays[region['array']]
            batch['entity_tags'] = arrays[region['entity_tags']]
            if 'normals' in region: batch['normals'] = arrays[region['normals']]
            batches.append(batch)
        expected_counts = {item['label']: item['count'] for item in run['manifest']['datasets']}
        if {item['label']: item['count'] for item in batches} != expected_counts:
            raise ValueError('Dataset labels or counts differ from the sweep manifest.')
        checked = to_xarray(batches); checked.close(); del checked
        yield {'id': record['id'], 'parameters': dict(parameters),
               'units': dict(setup['design_configuration']['units']), 'datasets': batches}


def training_arrays(configuration):
    """Return label -> N×(3+P) arrays; columns are xyz then named parameter order.

    Region counts stay independent. Normals/tags stay in configuration['datasets'];
    this helper neither combines boundary/interior regions nor assigns physics.
    """
    values = np.asarray(list(configuration['parameters'].values()), dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all(): raise ValueError('Invalid parameter values.')
    result = {}
    for batch in configuration['datasets']:
        points = np.asarray(batch['coordinates'], dtype=float)
        if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
            raise ValueError('Invalid training coordinates.')
        if batch['label'] in result: raise ValueError('Duplicate training dataset label.')
        result[batch['label']] = np.column_stack((points, np.broadcast_to(values, (len(points), len(values)))))
    return result
