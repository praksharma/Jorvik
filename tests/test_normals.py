"""Outward directions on holes/solids and numeric persistence without guessed normals."""
from copy import deepcopy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np
import xarray as xr
from jorvik.geometry import load_geometry
from jorvik.sampling import sample_geometry
from jorvik.export import export_samples, to_xarray
from jorvik.project import save_project, load_project
from jorvik.parametrisation import transform_batches
from test_project import state

ROOT = Path(__file__).resolve().parents[1] / 'examples/geometries'


def sample(geometry, entities, target='selection', count=128):
    request = {k: geometry[k] for k in ('source','source_hash','document')}
    request.update(entities=entities, target=target, method='sobol', count=count,
                   seed=42, label='boundary / 壁', resolution=16)
    return sample_geometry(request)


class NormalsTest(unittest.TestCase):
    def test_annulus_hole_and_translation_invariance(self):
        geometry = load_geometry(ROOT / 'cfdbench/annulus_unstructured.geo')
        batch = sample(geometry, [[1,t] for t in range(1,9)])
        self.assertEqual(batch['normal_status'], 'available')
        self.assertEqual(batch['normal_kind'], 'outward_conormal')
        self.assertEqual(set(batch['normal_planes']), {str(t) for t in np.unique(batch['entity_tags'])})
        xyz, normals = np.array(batch['coordinates']), np.array(batch['normals'])
        radius = np.linalg.norm(xyz, axis=1)
        expected = xyz/radius[:,None] * np.where(radius>.75,1,-1)[:,None]
        np.testing.assert_allclose(normals, expected, atol=1e-9)
        config = {'schema_version':1, 'scope':'domain', 'regions':[],
                  'source_hash':geometry['source_hash'], 'translation':[2,-3,1]}
        shifted = transform_batches([batch], config)[0]
        np.testing.assert_array_equal(shifted['normals'], normals)

    def test_curved_solid_normals_and_open_shell(self):
        geometry = load_geometry(ROOT / 'cfdbench/sphere_unstructured.geo')
        batch = sample(geometry, [[2,t] for t in range(1,9)])
        self.assertEqual(batch['normal_status'], 'available')
        self.assertIn('tetrahedral', batch['normal_validation'])
        np.testing.assert_allclose(batch['normals'], batch['coordinates'], atol=1e-7)
        edge = sample(geometry, [[1,1]])
        self.assertEqual(edge['normal_status'], 'not_applicable')
        self.assertNotIn('normals', edge)
        interior = sample(geometry, [], 'interior', count=16)
        self.assertEqual(interior['normal_status'], 'not_applicable')
        self.assertNotIn('normals', interior)
        cylinder = load_geometry(ROOT / 'cad/cylinder.brep')
        side = sample(cylinder, [[2,1]])
        self.assertEqual(side['normal_status'], 'available')
        xyz = np.asarray(side['coordinates']); radial = xyz.copy(); radial[:,2] = 0
        radial /= np.linalg.norm(radial, axis=1)[:,None]
        np.testing.assert_allclose(side['normals'], radial, atol=1e-8)
        shell = load_geometry(ROOT / 'cad/cube_filet.stp')
        face = next(e['tag'] for e in shell['entities'] if e['dimension']==2)
        open_batch = sample(shell, [[2,face]])
        self.assertEqual(open_batch['normal_status'], 'unavailable')
        self.assertNotIn('normals', open_batch)
        self.assertIn('Open or shared', open_batch['normal_note'])

    def test_occ_cavity_and_shared_interface(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'cavity.geo'
            source.write_text('SetFactory("OpenCASCADE"); Sphere(1)={0,0,0,1}; Sphere(2)={0,0,0,.4}; BooleanDifference{ Volume{1}; Delete; }{ Volume{2}; Delete; }'.replace('; ', ';\n') + '\n')
            geometry = load_geometry(source)
            faces = [[2,e['tag']] for e in geometry['entities'] if e['dimension']==2]
            batch = sample(geometry, faces, count=256)
            self.assertEqual(batch['normal_status'], 'available')
            points = np.asarray(batch['coordinates']); radius = np.linalg.norm(points, axis=1)
            self.assertTrue((radius<.5).any() and (radius>.9).any())
            expected = points/radius[:,None]*np.where(radius>.5,1,-1)[:,None]
            np.testing.assert_allclose(batch['normals'], expected, atol=1e-7)
            source = Path(directory) / 'shared.geo'
            source.write_text('SetFactory("OpenCASCADE"); Box(1)={0,0,0,1,1,1}; Box(2)={1,0,0,1,1,1}; BooleanFragments{ Volume{1}; Delete; }{ Volume{2}; Delete; }'.replace('; ', ';\n') + '\n')
            geometry = load_geometry(source)
            interface = next(e['tag'] for e in geometry['topology'] if e['dimension']==2 and len(e['upward'])==2)
            batch = sample(geometry, [[2,interface]], count=16)
            self.assertEqual(batch['normal_status'], 'unavailable')
            self.assertNotIn('normals', batch)

    def test_export_project_and_corrupt_normals(self):
        geometry = load_geometry(ROOT / 'cfdbench/annulus_unstructured.geo')
        batch = sample(geometry, [[1,t] for t in range(1,9)], count=32)
        interior = sample(geometry, [], 'interior', count=16); interior['label']='interior'
        with tempfile.TemporaryDirectory() as directory:
            for suffix in ('nc','npz'):
                path = export_samples([batch,interior], Path(directory)/('normal.'+suffix))
                if suffix=='nc':
                    with xr.open_dataset(path,engine='scipy') as data:
                        metadata = json.loads(data.attrs['metadata_json'])
                        record = metadata['datasets'][0]
                        np.testing.assert_array_equal(data[record['normals']], batch['normals'])
                        self.assertEqual(data[record['array']].attrs['normals_coordinate'], record['normals'])
                else:
                    with np.load(path,allow_pickle=False) as data:
                        metadata = json.loads(data['_gs_metadata'].item())
                        record = metadata['datasets'][0]
                        np.testing.assert_array_equal(data[record['normals']],batch['normals'])
                self.assertEqual(metadata['schema_version'],2)
                self.assertNotIn('normals',record['metadata'])
                self.assertEqual(record['metadata']['normal_planes'], batch['normal_planes'])
                self.assertNotIn('normals',metadata['datasets'][1])
            saved = state(); saved['selected_area']=batch['label']
            path=Path(directory)/'normals.jorvik'
            save_project(path,geometry,[(batch,'Generated',False)],saved)
            manifest,_=load_project(path)
            self.assertEqual(manifest['schema_version'],2)
            self.assertEqual(manifest['areas'][0]['area'],batch)
            with zipfile.ZipFile(path) as archive:
                files={name:archive.read(name) for name in archive.namelist()}
                metadata=json.loads(files['manifest.json'])
                self.assertNotIn('normals',metadata['areas'][0]['area'])
                self.assertIn('areas/0_normals.npy',files)
            stream=io.BytesIO();np.save(stream,np.zeros((32,3)),allow_pickle=False)
            files['areas/0_normals.npy']=stream.getvalue()
            with zipfile.ZipFile(path,'w') as archive:
                for name,data in files.items():archive.writestr(name,data)
            with self.assertRaisesRegex(ValueError,'unit vectors'):load_project(path)
        for normals in ([[0,0,0]]*32, [[1,0,0]], [[float('nan'),0,0]]*32):
            bad=deepcopy(batch);bad['normals']=normals
            with self.assertRaisesRegex(ValueError,'unit vectors'):to_xarray([bad])
        bad=deepcopy(batch);del bad['normals']
        with self.assertRaisesRegex(ValueError,'missing'):to_xarray([bad])

    def test_scaled_annulus_conormals_and_corrupt_project_frames(self):
        from test_affine_transforms import affine_setup
        geometry = load_geometry(ROOT / 'cfdbench/annulus_unstructured.geo')
        batch = sample(geometry, [[1, t] for t in range(1, 9)], count=32)
        config = affine_setup(source_hash=geometry['source_hash'], scale=[2., 3., 4.],
                              rotation_axis=[1., 0., 0.], rotation_angle=35.)
        from jorvik.parametrisation import affine_transform
        linear, _ = affine_transform(config)
        mapped = transform_batches([batch], config)[0]
        # Analytic ellipse gradient within the scaled XY plane, then rotate.
        points = np.array(batch['coordinates'])
        expected = points / [2., 3., 4.]
        expected *= np.where(np.linalg.norm(points, axis=1) > .75, 1., -1.)[:, None]
        rotation = linear @ np.diag([.5, 1/3, .25])
        expected = expected @ rotation.T
        expected /= np.linalg.norm(expected, axis=1, keepdims=True)
        np.testing.assert_allclose(mapped['normals'], expected, atol=1e-8)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'planes.jorvik'
            saved = state(); saved['selected_area'] = batch['label']
            save_project(path, geometry, [(batch, 'Generated', True)], saved)
            restored, _ = load_project(path)
            self.assertEqual(restored['areas'][0]['area']['normal_planes'], batch['normal_planes'])
            with zipfile.ZipFile(path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            metadata = json.loads(files['manifest.json'])
            first = next(iter(batch['normal_planes']))
            metadata['areas'][0]['area']['normal_planes'][first] = [0., 0., 0.]
            files['manifest.json'] = json.dumps(metadata).encode()
            with zipfile.ZipFile(path, 'w') as archive:
                for name, content in files.items(): archive.writestr(name, content)
            with self.assertRaisesRegex(ValueError, 'plane normals'): load_project(path)

    def test_rotated_cad_records_oblique_parent_planes(self):
        from test_affine_transforms import affine_setup
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'tilted.geo'
            source = (ROOT / 'cfdbench/annulus_unstructured.geo').read_text()
            path.write_text(source + '\nRotate {{1, 0, 0}, {0, 0, 0}, Pi/4} { Surface{1}; }\n')
            geometry = load_geometry(path)
            original = sample(geometry, [[1, t] for t in range(1, 9)], count=32)
            self.assertEqual(original['normal_status'], 'available')
            expected_plane = np.array([0., -1., 1.]) / np.sqrt(2)
            for plane in original['normal_planes'].values():
                self.assertAlmostEqual(abs(float(np.array(plane) @ expected_plane)), 1.)
            setup = affine_setup(source_hash=geometry['source_hash'], scale=[2., 3., 4.])
            mapped = transform_batches([original], setup)[0]
            from jorvik.normals import validate_normals
            validate_normals(mapped)
            self.assertEqual(mapped['normal_status'], 'available')
