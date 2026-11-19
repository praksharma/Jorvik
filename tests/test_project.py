"""Project archives preserve numeric arrays, references and old files on failure."""
from pathlib import Path
import tempfile
import json
import zipfile
import unittest
from unittest.mock import patch
from jorvik.project import save_project,load_project
from jorvik.geometry import load_geometry
from jorvik.sampling import sample_geometry

FIXTURE=Path(__file__).resolve().parents[1]/'examples/geometries/cfdbench/annulus_unstructured.geo'


def state():
    return {'selection':[[2,1]],'mode':'face','selected_area':'wall',
        'show_points':True,'through':False,'show_grid':False,'show_axes':True,
        'isolate':False,'isolate_geometry':False,'point_size':9,
        'geometry_visibility':{'geometry':True,'faces':False,'edges':True,'hiddenFaces':[1],'isolatedFaces':None,'isolatedEntities':None},
        'controls':{'method':'sobol','compute_mode':'reproducible','resolution':32,'count':128,'seed':42,'cpu_threads':1,'label':'boundary','target':'selection'},
        'layout':None,'camera':None,'parametrisation':None}


class ProjectTest(unittest.TestCase):
    def test_round_trip_and_atomic_failure(self):
        geometry=load_geometry(FIXTURE)
        request={k:geometry[k] for k in ('source','source_hash','document')}
        request.update(label='wall',target='selection',entities=[[2,1]],method='sobol',count=32,seed=42,resolution=32)
        batch=sample_geometry(request);batch['colour']='#ffc857'
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'annulus.jorvik'
            save_project(path,geometry,[(batch,'Generated',False)],state())
            data,source=load_project(path)
            self.assertEqual(data['geometry'],geometry)
            self.assertEqual(data['areas'][0]['area'],batch)
            self.assertFalse(data['areas'][0]['visible'])
            self.assertEqual(data['state'],state())
            self.assertEqual(source,FIXTURE.read_bytes())
            before=path.read_bytes()
            with patch('numpy.save',side_effect=OSError('Disk full')):
                with self.assertRaises(OSError):save_project(path,geometry,[(batch,'Generated',True)],state())
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(len(list(Path(directory).iterdir())),1)
            # Malformed state must fail validation before a UI sees the candidate.
            broken=state();broken['point_size']=-1
            save_project(path,geometry,[(batch,'Generated',True)],broken)
            with self.assertRaisesRegex(ValueError,'point size'):load_project(path)
            with zipfile.ZipFile(path,'w') as archive:archive.writestr('manifest.json',json.dumps({'schema_version':99}))
            with self.assertRaisesRegex(ValueError,'version'):load_project(path)
            coarse=state();coarse['controls']['resolution']=16
            save_project(path,geometry,[(batch,'Generated',True)],coarse)
            self.assertEqual(load_project(path)[0]['state']['controls']['resolution'],16)
