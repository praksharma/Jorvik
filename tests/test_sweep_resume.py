"""Verified continuation preserves source outputs and never reuses changed inputs."""
from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from jorvik.sweep_export import export_sweep, resume_sweep
from jorvik.parametrisation import export_configuration
from jorvik.parameter_design import generate_configurations
from jorvik.sweep_results import inspect_sweep, iter_sweep_datasets
from test_sweep_export import inputs


def contents(root):
    return {p.name: p.read_bytes() for p in root.iterdir()}


def partial(clouds, base, configs, root, fmt='npz'):
    stop = []
    return export_sweep(clouds, base, configs, root, file_format=fmt,
                        cancelled=lambda: bool(stop),
                        progress=lambda data: stop.append(True) if data.get('succeeded_count') else None)


class SweepResumeTest(unittest.TestCase):
    def test_formats_reuse_completed_and_generate_only_pending(self):
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            for fmt in ('nc', 'npz'):
                source = Path(directory)/fmt; partial(clouds, base, configs, source, fmt)
                if fmt == 'nc':
                    # A killed process may leave lagging counters and an
                    # unterminated final journal record after a completed shard.
                    manifest = json.loads((source/'manifest.json').read_text())
                    manifest.update(status='running', attempted_count=0, succeeded_count=0, pending_count=3)
                    (source/'manifest.json').write_text(json.dumps(manifest))
                    with (source/'results.jsonl').open('a') as journal: journal.write('{interrupted')
                before = contents(source); destination = Path(directory)/(fmt+'-resumed')
                with patch('jorvik.sweep_export.export_configuration', wraps=export_configuration) as write:
                    result = resume_sweep(clouds, base, configs, source, destination)
                self.assertEqual(write.call_count, 2)
                self.assertEqual([call.args[1]['design_configuration']['id'] for call in write.call_args_list],
                                 [record['id'] for record in configs[1:]])
                self.assertEqual(result['status'], 'complete'); self.assertEqual(result['reused_count'], 1)
                self.assertEqual(result['resumed_from'], str(source.resolve()))
                self.assertEqual(contents(source), before)
                first = 'configuration_000001.'+fmt
                self.assertEqual((destination/first).read_bytes(), before[first])
                self.assertEqual(len(list(iter_sweep_datasets(destination, require_integrity=True))), 3)
                with patch('jorvik.sweep_export.export_configuration') as write:
                    again = resume_sweep(clouds, base, configs, destination, Path(directory)/(fmt+'-again'))
                write.assert_not_called(); self.assertEqual(again['reused_count'], 3)
                continued = Path(directory)/(fmt+'-again')
                metadata = json.loads((continued/'manifest.json').read_text())
                metadata['reused_count'] = 0
                (continued/'manifest.json').write_text(json.dumps(metadata))
                with self.assertRaisesRegex(ValueError, 'reused count'): inspect_sweep(continued)

    def test_failed_configurations_are_retried_with_stable_ids(self):
        clouds, base, configs = inputs()
        with TemporaryDirectory() as directory:
            source=Path(directory)/'errors'
            def export(batches, setup, destination):
                if setup['design_configuration']['id'].endswith('000002'): raise OSError('Quota')
                return export_configuration(batches, setup, destination)
            with patch('jorvik.sweep_export.export_configuration', side_effect=export):
                export_sweep(clouds, base, configs, source, file_format='npz', fail_fast=False)
            before=contents(source); destination=Path(directory)/'retry'
            with patch('jorvik.sweep_export.export_configuration', wraps=export_configuration) as write:
                result=resume_sweep(clouds, base, configs, source, destination)
            self.assertEqual(write.call_count, 1)
            self.assertEqual(write.call_args.args[1]['design_configuration']['id'], configs[1]['id'])
            self.assertEqual(result['reused_count'], 2)
            self.assertEqual(inspect_sweep(destination)['failed'], [])
            self.assertEqual(contents(source), before)

    def test_changed_inputs_setup_plan_and_corrupt_outputs_reject_before_creation(self):
        clouds, base, configs=inputs()
        with TemporaryDirectory() as directory:
            source=Path(directory)/'run'; partial(clouds, base, configs, source)
            before=contents(source)
            changed=deepcopy(clouds); changed[0]['coordinates'][0][0]+=0.25
            recipes=deepcopy(clouds); recipes[0]['seed']+=1
            altered=deepcopy(base); altered['scale']=[2., 1., 1.]
            candidates=[(changed, base, configs), (recipes, base, configs),
                        (clouds, altered, generate_configurations(altered, altered['design'])),
                        (clouds, base, list(reversed(configs)))]
            for index, args in enumerate(candidates):
                dest=Path(directory)/str(index)
                with self.subTest(index=index), self.assertRaises(ValueError):
                    resume_sweep(*args, source, dest)
                self.assertFalse(dest.exists()); self.assertEqual(contents(source), before)
            shard=source/'configuration_000001.npz'
            with shard.open('ab') as stream: stream.write(b'changed')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                resume_sweep(clouds, base, configs, source, Path(directory)/'corrupt')
            self.assertFalse((Path(directory)/'corrupt').exists())
            shard.write_bytes(before[shard.name])
            manifest=json.loads((source/'manifest.json').read_text()); del manifest['input_fingerprint']
            (source/'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'fingerprint'):
                resume_sweep(clouds, base, configs, source, Path(directory)/'legacy')
            self.assertFalse((Path(directory)/'legacy').exists())
            with self.assertRaises(FileExistsError): resume_sweep(clouds, base, configs, source, source)
            with self.assertRaisesRegex(ValueError, 'outside'): resume_sweep(clouds, base, configs, source, source/'nested')

    def test_preflight_and_export_cancellation_and_failed_copy_preserve_source(self):
        clouds, base, configs=inputs()
        with TemporaryDirectory() as directory:
            source=Path(directory)/'run'; partial(clouds, base, configs, source)
            before=contents(source); dest=Path(directory)/'preflight'; stopped=[]
            def progress(data):
                if data.get('stage')=='verifying' and data['verified_count']: stopped.append(True)
            with self.assertRaises(InterruptedError):
                resume_sweep(clouds, base, configs, source, dest, cancelled=lambda: bool(stopped), progress=progress)
            self.assertFalse(dest.exists()); stopped=[]; dest=Path(directory)/'cancelled'
            result=resume_sweep(clouds, base, configs, source, dest, cancelled=lambda:bool(stopped),
                                progress=lambda data: stopped.append(True) if data.get('attempted_count') else None)
            self.assertEqual(result['status'], 'cancelled'); self.assertEqual(result['reused_count'], 1)
            self.assertEqual(len(inspect_sweep(dest, allow_partial=True)['completed']), 1)
            dest=Path(directory)/'copy-error'
            with patch('jorvik.sweep_export.shutil.copyfile', side_effect=OSError('Disk full')):
                result=resume_sweep(clouds, base, configs, source, dest)
            self.assertEqual(result['status'], 'failed'); self.assertEqual(result['reused_count'], 0)
            self.assertFalse((dest/'configuration_000001.npz').exists())
            self.assertFalse(list(dest.glob('.*.tmp')))
            self.assertEqual(contents(source), before)
