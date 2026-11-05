"""Validated streaming sweep reads and independent parameter-conditioned arrays."""
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import numpy as np
from jorvik.sweep_export import export_sweep
from jorvik.sweep_results import inspect_sweep, iter_sweep_datasets, training_arrays
from test_sweep_export import inputs


class SweepResultsTest(unittest.TestCase):
    def test_formats_labels_counts_and_training_columns(self):
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            for fmt in ('nc','npz'):
                root=Path(directory)/fmt
                export_sweep(clouds,base,configs,root,file_format=fmt)
                self.assertEqual(len(inspect_sweep(root)['completed']),3)
                rows=list(iter_sweep_datasets(root))
                self.assertEqual([r['id'] for r in rows],[r['id'] for r in configs])
                for config in rows:
                    arrays=training_arrays(config)
                    self.assertEqual(list(arrays),['wall / 面','interior'])
                    self.assertEqual(arrays['wall / 面'].shape,(3,5))
                    self.assertEqual(arrays['interior'].shape,(7,5))
                    for batch in config['datasets']:
                        np.testing.assert_array_equal(arrays[batch['label']][:,:3],batch['coordinates'])
                        np.testing.assert_array_equal(arrays[batch['label']][:,3:],np.tile(list(config['parameters'].values()),(batch['count'],1)))
                    self.assertIn('normals',config['datasets'][0])
                rows[0]['datasets'][0]['coordinates'][0,0]=999
                self.assertNotEqual(next(iter_sweep_datasets(root))['datasets'][0]['coordinates'][0,0],999)

    def test_partial_requires_opt_in_and_interrupted_last_line(self):
        clouds, base, configs=inputs(); progress=[]
        with TemporaryDirectory() as directory:
            root=Path(directory)/'partial'
            export_sweep(clouds,base,configs,root,cancelled=lambda:bool(progress),
                         progress=lambda data:progress.append(data) if data['succeeded_count'] else None)
            with self.assertRaises(ValueError):inspect_sweep(root)
            self.assertEqual(len(list(iter_sweep_datasets(root,allow_partial=True))),1)
            manifest=json.loads((root/'manifest.json').read_text());manifest['status']='running'
            (root/'manifest.json').write_text(json.dumps(manifest))
            with (root/'results.jsonl').open('a') as stream:stream.write('{unfinished')
            self.assertEqual(len(inspect_sweep(root,allow_partial=True)['completed']),1)
            with (root/'results.jsonl').open('a') as stream:stream.write('\n')
            with self.assertRaises(ValueError):inspect_sweep(root,allow_partial=True)

    def test_corrupt_plan_missing_output_and_parameter_arrays(self):
        clouds, base, configs=inputs()
        with TemporaryDirectory() as directory:
            root=Path(directory)/'run';export_sweep(clouds,base,configs,root,file_format='npz')
            planpath=root/'configurations.json';original=planpath.read_bytes()
            plan=json.loads(original);plan['configurations'][0]['filename']='../outside.npz'
            planpath.write_text(json.dumps(plan))
            with self.assertRaises(ValueError):inspect_sweep(root)
            planpath.write_bytes(original)
            first=root/'configuration_000001.npz'
            with np.load(first,allow_pickle=False) as data:arrays={k:data[k].copy() for k in data.files}
            arrays['_gs_design_values'][0]+=1
            with first.open('wb') as stream:np.savez(stream,**arrays)
            with self.assertRaises(ValueError):next(iter_sweep_datasets(root))
            first.unlink()
            with self.assertRaises(ValueError):inspect_sweep(root)

    def test_checksums_detect_valid_coordinate_changes_in_both_formats(self):
        import xarray as xr
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            for fmt in ('nc', 'npz'):
                with self.subTest(format=fmt):
                    root = Path(directory) / fmt
                    export_sweep(clouds, base, configs, root, file_format=fmt)
                    run = inspect_sweep(root, require_integrity=True)
                    shard = root / run['completed'][0]['filename']
                    expected = run['checksums'][configs[0]['id']]
                    self.assertEqual(expected['sha256'], hashlib.sha256(shard.read_bytes()).hexdigest())
                    self.assertEqual(expected['size_bytes'], shard.stat().st_size)
                    self.assertEqual(len(list(iter_sweep_datasets(root, require_integrity=True))), 3)
                    if fmt == 'npz':
                        with np.load(shard, allow_pickle=False) as data:
                            arrays = {key: data[key].copy() for key in data.files}
                        metadata = json.loads(arrays['_gs_metadata'].item())
                        arrays[metadata['datasets'][0]['array']][0, 0] += 0.25
                        with shard.open('wb') as stream:
                            np.savez_compressed(stream, **arrays)
                    else:
                        with xr.open_dataset(shard, engine='scipy') as data:
                            changed = data.load().copy(deep=True)
                        metadata = json.loads(changed.attrs['metadata_json'])
                        changed[metadata['datasets'][0]['array']].values[0, 0] += 0.25
                        changed.to_netcdf(shard, engine='scipy'); changed.close()
                    # Metadata inspection stays cheap; iteration verifies actual bytes.
                    self.assertEqual(len(inspect_sweep(root)['completed']), 3)
                    with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                        next(iter_sweep_datasets(root))

    def test_plan_and_missing_or_malformed_checksums_are_rejected(self):
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'run'
            export_sweep(clouds, base, configs, root, file_format='npz')
            plan = root / 'configurations.json'; original = plan.read_bytes()
            plan.write_bytes(original + b' ')
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                inspect_sweep(root)
            plan.write_bytes(original)
            journal = root / 'results.jsonl'; original = journal.read_text()
            for value in (None, {}, {'sha256': 'x'*64, 'size_bytes': 1},
                          {'sha256': '0'*64, 'size_bytes': True}):
                results = [json.loads(line) for line in original.splitlines()]
                results[0]['integrity'] = value
                journal.write_text(''.join(json.dumps(row)+'\n' for row in results))
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'checksum'):
                    inspect_sweep(root)
            journal.write_text(original)
            manifestpath = root / 'manifest.json'
            manifest = json.loads(manifestpath.read_text())
            manifest['integrity']['algorithm'] = 'unknown'
            manifestpath.write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'algorithm'):
                inspect_sweep(root)

    def test_legacy_runs_remain_readable_with_explicit_strict_option(self):
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'legacy'
            export_sweep(clouds, base, configs, root, file_format='npz')
            manifestpath = root / 'manifest.json'
            manifest = json.loads(manifestpath.read_text()); del manifest['integrity']
            manifestpath.write_text(json.dumps(manifest))
            journal = root / 'results.jsonl'
            results = [json.loads(line) for line in journal.read_text().splitlines()]
            for row in results: del row['integrity']
            journal.write_text(''.join(json.dumps(row)+'\n' for row in results))
            self.assertEqual(inspect_sweep(root)['checksums'], {})
            self.assertEqual(len(list(iter_sweep_datasets(root))), 3)
            with self.assertRaisesRegex(ValueError, 'legacy'):
                inspect_sweep(root, require_integrity=True)
            with self.assertRaisesRegex(ValueError, 'legacy'):
                next(iter_sweep_datasets(root, require_integrity=True))
            # Legacy structural checks still catch changed parameter arrays.
            shard = root / 'configuration_000001.npz'
            with np.load(shard, allow_pickle=False) as data:
                arrays = {key: data[key].copy() for key in data.files}
            arrays['_gs_design_values'][0] += 1
            with shard.open('wb') as stream: np.savez(stream, **arrays)
            with self.assertRaisesRegex(ValueError, 'parameter arrays'):
                next(iter_sweep_datasets(root))

    def test_inspection_command_verifies_and_reports_checksum_errors(self):
        import subprocess
        import sys
        clouds, base, configs = inputs()
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / 'tools/inspect_sweep.py')]
        with TemporaryDirectory() as directory:
            root = Path(directory) / 'run'
            export_sweep(clouds, base, configs, root, file_format='npz')
            args = [*command, str(root), '--verify', '--require-integrity']
            good = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            summary = json.loads(good.stdout)
            self.assertEqual(summary['verified_files'], 3)
            self.assertEqual(summary['integrity'], 'sha256')
            with (root / 'configuration_000001.npz').open('ab') as stream:
                stream.write(b'changed')
            bad = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(bad.returncode, 1)
            self.assertIn('checksum mismatch', bad.stderr)
            self.assertEqual(bad.stdout, '')
