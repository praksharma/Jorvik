"""Inspect sweep status, optionally validating every completed shard one at a time."""
import argparse
import json
import sys
from jorvik.sweep_results import inspect_sweep, iter_sweep_datasets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory')
    parser.add_argument('--allow-partial', action='store_true', help='Inspect successful files in an incomplete/error run')
    parser.add_argument('--verify', action='store_true', help='Also load and validate each completed configuration')
    parser.add_argument('--require-integrity', action='store_true', help='Reject legacy runs without SHA-256 checksums')
    args = parser.parse_args()
    try:
        result = inspect_sweep(args.directory, allow_partial=args.allow_partial, require_integrity=args.require_integrity)
        verified = None
        if args.verify:
            verified = sum(1 for _ in iter_sweep_datasets(args.directory, allow_partial=args.allow_partial, require_integrity=args.require_integrity))
        print(json.dumps({'status': result['manifest']['status'],
                          'completed': len(result['completed']), 'failed': result['failed'],
                          'pending': result['pending'], 'verified_files': verified,
                          'integrity': result['manifest'].get('integrity', {}).get('algorithm')}, indent=2))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, 'Could not inspect sweep: ' + str(error) + '\n')


if __name__ == '__main__': main()
