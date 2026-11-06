"""Read-only sweep inspection protocol for the GUI subprocess (no Qt)."""
import json
import sys
from .sweep_results import inspect_sweep, iter_sweep_datasets


def emit(value):
    print(json.dumps(value, ensure_ascii=False, allow_nan=False), flush=True)


def inspect(path, *, verify=False, report=emit):
    run = inspect_sweep(path, allow_partial=True)
    manifest = run['manifest']
    completed = {record['id']: record for record in run['completed']}
    failed = set(run['failed'])
    # The ordered scalar plan supplies pending/failed rows too; inspection has
    # already checked its location, schema, safe filenames and checksum.
    from pathlib import Path
    plan = json.loads((Path(path) / manifest['plan']).read_text())['configurations']
    rows = [{'id': record['id'], 'parameters': record['parameters'],
             'status': 'complete' if record['id'] in completed else 'failed' if record['id'] in failed else 'pending',
             'filename': record['filename'] if record['id'] in completed else ''}
            for record in plan]
    rows += [{'id': record['id'], 'parameters': record['parameters'], 'status': 'rejected',
              'filename': '', 'reasons': record['reasons']} for record in run['rejected']]
    rows.sort(key=lambda record: record['id'])
    summary = {key: manifest[key] for key in ('status', 'source_hash', 'configuration_count')}
    summary.update(constraint_rejected=len(run['rejected']), reused_count=manifest.get('reused_count', 0), resumed_from=manifest.get('resumed_from'),
                   completed=len(completed), failed=len(failed), pending=len(run['pending']),
                   integrity=manifest.get('integrity', {}).get('algorithm'),
                   datasets=[{'label': batch['label'], 'count': batch['count']} for batch in manifest['datasets']])
    report({'event': 'metadata', 'summary': summary, 'rows': rows})
    if verify:
        iterator = iter_sweep_datasets(path, allow_partial=True)
        for record in run['completed']:
            try:
                # Only the ID crosses the process boundary, never point arrays.
                identity = next(iterator)['id']
            except Exception as error:
                report({'event': 'verification_error', 'id': record['id'], 'error': str(error) or type(error).__name__})
                return False
            report({'event': 'verified', 'id': identity})
    report({'event': 'done', 'verified': len(completed) if verify else None})
    return True


def main():
    try:
        request = json.load(sys.stdin)
        if not inspect(request['path'], verify=request.get('verify', False)):
            return 1
    except Exception as error:
        emit({'event': 'error', 'error': str(error) or type(error).__name__})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
