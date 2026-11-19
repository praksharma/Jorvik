"""An open 3D shell must never silently become a 2D interior dataset."""
from pathlib import Path
import tempfile
import unittest
from jorvik.geometry import load_geometry
from jorvik.sampling import sample_geometry

FIXTURES=Path(__file__).resolve().parents[1]/'examples/geometries/cfdbench'


def interior(geometry):
    request={k:geometry[k] for k in ('source','source_hash','document')}
    request.update(label='interior',target='interior',entities=[],method='sobol',count=16,seed=42,resolution=16)
    return sample_geometry(request)


class DomainsTest(unittest.TestCase):
    def test_open_sphere_rejected_but_boundary_sampling_remains_available(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'open.geo'
            source.write_text((FIXTURES/'sphere_unstructured.geo').read_text().replace('Volume(1)={1};','').replace('Physical Volume(100)={1};',''))
            geometry=load_geometry(source)
            self.assertEqual(geometry['interior_domain'],'open_or_nonplanar')
            self.assertEqual(geometry['dimension'],3)
            with self.assertRaisesRegex(ValueError,'closed solid'):interior(geometry)
            request={k:geometry[k] for k in ('source','source_hash','document')}
            request.update(label='boundary',target='selection',entities=[[2,1]],method='sobol',count=16,seed=42,resolution=16)
            batch=sample_geometry(request)
            self.assertEqual(batch['count'],16)
            self.assertEqual(batch['normal_status'],'unavailable')

    def test_planar_annulus_and_declared_solid_keep_their_dimensions(self):
        annulus=load_geometry(FIXTURES/'annulus_unstructured.geo')
        self.assertEqual(annulus['interior_domain'],'planar')
        self.assertEqual(interior(annulus)['entity_dimension'],2)
        sphere=load_geometry(FIXTURES/'sphere_unstructured.geo')
        self.assertEqual(sphere['interior_domain'],'solid')
        self.assertEqual(interior(sphere)['entity_dimension'],3)

    def test_disconnected_planes_at_different_heights_are_not_one_2d_domain(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'planes.geo'
            source.write_text('SetFactory("OpenCASCADE");\nRectangle(1)={0,0,0,1,1};\nRectangle(2)={0,0,1,1,1};\n')
            geometry=load_geometry(source)
            self.assertEqual(geometry['interior_domain'],'open_or_nonplanar')
            with self.assertRaisesRegex(ValueError,'closed solid'):interior(geometry)

    def test_planar_step_and_brep_sheets_with_rotation_holes_and_nonplanar_rejection(self):
        import gmsh
        import numpy as np
        import xarray as xr
        from jorvik.export import export_samples
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # 1. Rotated planar STEP sheet with an inner circular hole
            gmsh.initialize(['jorvik'], readConfigFiles=False)
            try:
                gmsh.option.setNumber('General.Terminal', 0)
                gmsh.model.add('holey_sheet')
                rect = gmsh.model.occ.addRectangle(0, 0, 0, 4, 2)
                hole = gmsh.model.occ.addDisk(2, 1, 0, 0.5, 0.5)
                cut, _ = gmsh.model.occ.cut([(2, rect)], [(2, hole)])
                gmsh.model.occ.rotate(cut, 0, 0, 0, 1, 0, 0, np.pi / 4)
                gmsh.model.occ.synchronize()
                holey_step = root / 'holey_rotated.step'
                holey_brep = root / 'holey_rotated.brep'
                gmsh.write(str(holey_step))
                gmsh.write(str(holey_brep))
            finally:
                gmsh.finalize()

            for path in (holey_step, holey_brep):
                geometry = load_geometry(path)
                self.assertEqual(geometry['interior_domain'], 'planar')
                self.assertEqual(geometry['dimension'], 2)
                for method in ('sobol', 'grid'):
                    request = {k: geometry[k] for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
                    request.update(label=f'interior_{method}', target='interior', entities=[],
                                   method=method, count=64, seed=42, resolution=16)
                    sampled = sample_geometry(request)
                    self.assertEqual(sampled['entity_dimension'], 2)
                    points = np.asarray(sampled['coordinates'])
                    # In the rotated frame (45 deg about X), y == z on the sheet
                    np.testing.assert_allclose(points[:, 1], points[:, 2], atol=1e-7)
                    # Unrotate about X by -45 deg to verify hole exclusion and rectangle bounds
                    unrotated_y = (points[:, 1] + points[:, 2]) / np.sqrt(2)
                    self.assertTrue(((points[:, 0] >= -1e-7) & (points[:, 0] <= 4 + 1e-7)).all())
                    self.assertTrue(((unrotated_y >= -1e-7) & (unrotated_y <= 2 + 1e-7)).all())
                    hole_dist = np.hypot(points[:, 0] - 2.0, unrotated_y - 1.0)
                    self.assertTrue((hole_dist >= 0.5 - 1e-3).all())
                edge_req = {k: geometry[k] for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
                edge_req.update(label='edges', target='selection',
                                entities=[[1, e['tag']] for e in geometry['entities'] if e['dimension'] == 1 and not e['degenerate']],
                                method='sobol', count=32, seed=42, resolution=16)
                edges = sample_geometry(edge_req)
                self.assertEqual(edges['normal_status'], 'available')
                self.assertEqual(edges['normal_kind'], 'outward_conormal')
                nc_path = export_samples([sampled, edges], root / f'{path.stem}.nc')
                with xr.open_dataset(nc_path, engine='scipy') as ds:
                    self.assertIn(sampled['label'], ds.data_vars)
                    self.assertIn('edges', ds.data_vars)

            # 2. Non-planar bent STEP sheet must be rejected for interior sampling
            gmsh.initialize(['jorvik'], readConfigFiles=False)
            try:
                gmsh.option.setNumber('General.Terminal', 0)
                gmsh.model.add('bent_sheet')
                r1 = gmsh.model.occ.addRectangle(0, 0, 0, 1, 1)
                r2 = gmsh.model.occ.addRectangle(0, 0, 0, 1, 1)
                gmsh.model.occ.rotate([(2, r2)], 0, 0, 0, 1, 0, 0, np.pi / 2)
                gmsh.model.occ.synchronize()
                bent_step = root / 'bent.step'
                gmsh.write(str(bent_step))
            finally:
                gmsh.finalize()
            bent = load_geometry(bent_step)
            self.assertEqual(bent['interior_domain'], 'open_or_nonplanar')
            self.assertEqual(bent['dimension'], 3)
            with self.assertRaisesRegex(ValueError, 'solid volumes'):
                interior(bent)
