"""Sampling correctness, coverage, source integrity and reproducibility."""
import tempfile
import unittest
from pathlib import Path
import numpy as np
from jorvik.geometry import load_geometry
from jorvik.sampling import METHODS, sample_geometry, unit_points

FIXTURES=Path(__file__).resolve().parents[1]/'examples/geometries/cfdbench'


class SamplingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.annulus=load_geometry(FIXTURES/'annulus_unstructured.geo')
        cls.sphere=load_geometry(FIXTURES/'sphere_unstructured.geo')

    def sample(self, geometry, method='random', target='selection', entities=None, count=128):
        request={key:geometry[key] for key in ('source','source_hash','document')}
        request.update(method=method,target=target,entities=entities or [[1,1]],count=count,seed=42,label='test',resolution=16)
        return sample_geometry(request)

    def test_unit_designs(self):
        for method in METHODS:
            with self.subTest(method=method):
                a=unit_points(method,64,3,42)
                np.testing.assert_array_equal(a,unit_points(method,64,3,42))
                self.assertTrue(((a>=0)&(a<1)).all())
                self.assertEqual(len(a),64)
        lhs=unit_points('lhs',64,3,42)
        for axis in range(3):
            self.assertEqual(len(np.unique(np.floor(lhs[:,axis]*64))),64)
        stratified=unit_points('stratified',64,2,42)
        self.assertEqual(len(np.unique(np.floor(stratified*8),axis=0)),64)
        self.assertEqual(len(unit_points('grid',100,3,42)),64)

    def test_all_methods_on_curves_and_2d_interior(self):
        for method in METHODS:
            with self.subTest(method=method):
                edges=self.sample(self.annulus,method,entities=[[1,t] for t in range(1,5)])
                xyz=np.array(edges['coordinates'])
                np.testing.assert_allclose(np.linalg.norm(xyz,axis=1),.5,atol=1e-10)
                self.assertEqual(edges['count'],128)
                np.testing.assert_array_equal(xyz,self.sample(self.annulus,method,entities=[[1,t] for t in range(1,5)])['coordinates'])
                area=self.sample(self.annulus,method,target='interior')
                radius=np.linalg.norm(area['coordinates'],axis=1)
                self.assertTrue(((radius>=.5-1e-10)&(radius<=1+1e-10)).all())
                if method!='grid':
                    self.assertEqual(area['count'],128)
                else:
                    self.assertTrue(0<area['count']<=128)
                self.assertEqual(len(set(area['entity_tags'])),1)

    def test_curved_surface_and_measure_coverage(self):
        result=self.sample(self.sphere,entities=[[2,t] for t in range(1,9)],count=4096)
        xyz=np.array(result['coordinates'])
        np.testing.assert_allclose(np.linalg.norm(xyz,axis=1),1,atol=1e-8)
        self.assertLess(np.max(np.abs(xyz.mean(axis=0))),.05)
        self.assertTrue(all(350<result['entity_tags'].count(tag)<650 for tag in range(1,9)))
        patch=self.sample(self.sphere,entities=[[2,1]],count=1024)
        xyz=np.array(patch['coordinates'])
        self.assertTrue((xyz[:,0]<=1e-8).all() and (xyz[:,1]>=-1e-8).all() and (xyz[:,2]<=1e-8).all())
        np.testing.assert_allclose(xyz.mean(axis=0),[-.5,.5,-.5],atol=.06)
        annulus=self.sample(self.annulus,target='interior',count=4096)
        r2=np.square(np.array(annulus['coordinates'])).sum(axis=1)
        self.assertAlmostEqual(float(r2.mean()),.625,delta=.025)

    def test_all_volume_methods(self):
        for method in METHODS:
            with self.subTest(method=method):
                result=self.sample(self.sphere,method,target='interior')
                radius=np.linalg.norm(result['coordinates'],axis=1)
                self.assertTrue((radius<1+1e-9).all())
                self.assertGreater(result['count'],0)
                if method!='grid':self.assertEqual(result['count'],128)
                self.assertTrue(any('tetrahedral' in note for note in result['notes']))

    def test_multicore_mesh_and_metadata(self):
        for method in ('random','grid','sobol'):
            request={key:self.sphere[key] for key in ('source','source_hash','document')}
            request.update(method=method,target='interior',entities=[],count=128,seed=42,label='fast',resolution=16,compute_mode='multicore',cpu_threads=2)
            result=sample_geometry(request)
            self.assertEqual(result['cpu_threads'],2)
            self.assertEqual(result['compute_mode'],'multicore')
            self.assertEqual(result['mesh_algorithm_3d'],10)
            self.assertTrue((np.linalg.norm(result['coordinates'],axis=1)<1+1e-9).all())
            self.assertGreater(result['count'],0)
            if method!='grid':self.assertEqual(result['count'],128)
        request['cpu_threads']=0
        with self.assertRaisesRegex(ValueError,'CPU'):sample_geometry(request)

    def test_changed_source_and_invalid_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'changed.geo'
            path.write_text('changed geometry')
            geometry={**self.annulus,'source':str(path)}
            with self.assertRaisesRegex(ValueError,'changed'):self.sample(geometry)
        with self.assertRaisesRegex(ValueError,'settings'):self.sample(self.annulus,count=0)

    def test_adaptive_surface_envelope_refinement_and_singularity_rejection(self):
        from unittest.mock import patch
        import gmsh
        from jorvik.sampling import _surface_samples, _jacobian
        gmsh.initialize(['jorvik'], readConfigFiles=False)
        try:
            gmsh.option.setNumber('General.Terminal', 0)
            gmsh.open(str(FIXTURES / 'sphere_unstructured.geo'))
            gmsh.option.setNumber('Mesh.ElementOrder', 1)
            gmsh.model.mesh.generate(2)
            gmsh.model.mesh.setOrder(1)
            calls = {'n': 0}
            def underestimated_then_valid(g, tag, uv):
                val = _jacobian(g, tag, uv)
                calls['n'] += 1
                # First call is initial 4-point triangle probe: underestimate by 4x
                return val * 0.25 if calls['n'] == 1 else val
            with patch('jorvik.sampling._jacobian', side_effect=underestimated_then_valid):
                points, tags, _ = _surface_samples(gmsh, [1], 'sobol', 64, 42)
            self.assertEqual(len(points), 64)
            self.assertEqual(set(tags.tolist()), {1})
            # Persistent blow-up must still fail after bounded envelope refinements
            def blowing_up(g, tag, uv):
                calls['n'] += 1
                return _jacobian(g, tag, uv) * (10.0 ** calls['n'])
            calls['n'] = 0
            with patch('jorvik.sampling._jacobian', side_effect=blowing_up):
                with self.assertRaisesRegex(ValueError, 'Surface curvature exceeded'):
                    _surface_samples(gmsh, [1], 'sobol', 64, 42)
        finally:
            gmsh.finalize()
