"""Validated streaming sweep reads and independent parameter-conditioned arrays."""
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
