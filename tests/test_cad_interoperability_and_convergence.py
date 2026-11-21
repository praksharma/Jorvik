"""Tests for surface sampling bounds, mesh convergence, CAD protocols/colours/assemblies,
optional shape healing with entity remapping, and full .geo Include/Merge dependency bundling.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

from jorvik.cad_import import (
    detect_cad_header_metadata,
    discover_geo_dependencies,
    heal_and_remap,
    open_source,
)
from jorvik.convergence import (
    certified_surface_bounds,
    mesh_convergence_report,
    validate_trimming_and_containment,
)
from jorvik.geometry import load_geometry
from jorvik.project import extract_project_sources, load_project, save_project
from jorvik.regions import put_region
from jorvik.sampling import sample_geometry
from test_project import state

FIXTURES = Path(__file__).resolve().parents[1] / 'examples/geometries'


class CadInteroperabilityAndConvergenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.annulus = load_geometry(FIXTURES / 'cfdbench/annulus_unstructured.geo')
        cls.sphere = load_geometry(FIXTURES / 'cfdbench/sphere_unstructured.geo')
        cls.cylinder = load_geometry(FIXTURES / 'cad/cylinder.brep')

    def test_surface_sampling_bounds_mesh_convergence_and_containment(self):
        # 1. Mesh convergence on 2D annulus interior across (16, 32, 64)
        report_2d = mesh_convergence_report(self.annulus, target='interior', resolutions=(16, 32, 64), count=128)
        self.assertEqual(report_2d['resolutions'], [16, 32, 64])
        self.assertTrue(report_2d['monotone_converging'])
        analytic_annulus = 0.75 * math.pi
        # Resolution 64 measure is within 1% of analytic 0.75*pi
        self.assertAlmostEqual(report_2d['measures'][-1], analytic_annulus, delta=0.025)

        # 2. Certified surface Jacobian envelope bounds on 3D curved sphere surface
        import gmsh
        gmsh.initialize(['jorvik'], readConfigFiles=False, interruptible=False)
        try:
            gmsh.option.setNumber('General.Terminal', 0)
            open_source(gmsh, FIXTURES / 'cfdbench/sphere_unstructured.geo')
            gmsh.model.mesh.generate(2)
            gmsh.model.mesh.setOrder(1)
            face_tags = [t for _, t in gmsh.model.getEntities(2)]
            bounds_info = certified_surface_bounds(gmsh, face_tags)
            self.assertGreater(bounds_info['triangle_count'], 20)
            self.assertGreater(bounds_info['min_jacobian_bound'], 0.0)
            self.assertGreaterEqual(bounds_info['max_jacobian_bound'], bounds_info['min_jacobian_bound'])
            self.assertAlmostEqual(bounds_info['trimmed_area'], 4.0 * math.pi, delta=0.25)
            self.assertFalse(bounds_info['is_planar'])
        finally:
            gmsh.finalize()

        # 3. Validate trimming/containment across 1D curve, 2D planar holey sheet, and 3D OCC solid
        req_base = {k: deepcopy(self.annulus[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
        curve_batch = sample_geometry({
            **req_base, 'target': 'selection', 'entities': [[1, 1], [1, 2]],
            'method': 'sobol', 'count': 32, 'seed': 42, 'label': 'arc', 'resolution': 32,
        })
        self.assertTrue(validate_trimming_and_containment(curve_batch)['containment_verified'])

        plane_batch = sample_geometry({
            **req_base, 'target': 'interior', 'entities': [],
            'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'plane', 'resolution': 32,
        })
        self.assertTrue(validate_trimming_and_containment(plane_batch)['containment_verified'])

        cyl_req = {k: deepcopy(self.cylinder[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
        solid_batch = sample_geometry({
            **cyl_req, 'target': 'interior', 'entities': [],
            'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'solid', 'resolution': 16,
        })
        self.assertTrue(validate_trimming_and_containment(solid_batch)['containment_verified'])

    def test_step_protocols_exporter_matrix_and_unsupported_translators(self):
        exporters = [
            ('AP203', 'CONFIG_CONTROL_DESIGN', 'SolidWorks 2024', 'mm', "SI_UNIT(.MILLI.,.METRE.)"),
            ('AP214', 'AUTOMOTIVE_DESIGN', 'Ansys SpaceClaim 2024 R2', 'mm', "SI_UNIT(.MILLI.,.METRE.)"),
            ('AP242', 'MANAGED_MODEL_BASED_3D_ENGINEERING', 'FreeCAD 1.0', 'm', "SI_UNIT($,.METRE.)"),
            ('AP214', 'AUTOMOTIVE_DESIGN', 'Siemens NX 2312', 'in', "CONVERSION_BASED_UNIT('INCH',#11)"),
            ('AP242', 'AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF', 'CATIA V5', 'mm', "SI_UNIT(.MILLI.,.METRE.)"),
            ('AP214', 'AUTOMOTIVE_DESIGN', 'PTC Creo 10.0', 'mm', "SI_UNIT(.MILLI.,.METRE.)"),
            ('AP242', 'MANAGED_MODEL_BASED_3D_ENGINEERING', 'Autodesk Inventor / Fusion', 'cm', "SI_UNIT(.CENTI.,.METRE.)"),
            ('AP242', 'MANAGED_MODEL_BASED_3D_ENGINEERING', 'Onshape', 'm', "SI_UNIT($,.METRE.)"),
            ('AP214', 'AUTOMOTIVE_DESIGN', 'Rhinoceros 8.0', 'mm', "SI_UNIT(.MILLI.,.METRE.)"),
        ]
        with tempfile.TemporaryDirectory() as directory:
            for expected_proto, schema_str, system_name, expected_unit, unit_expr in exporters:
                step_file = Path(directory) / f'{expected_proto}.step'
                step_file.write_text(
                    "ISO-10303-21;\nHEADER;\n"
                    f"FILE_NAME('part.step','2026-01-01T00:00:00',('Author'),('Org'),'Preprocessor','{system_name}','');\n"
                    f"FILE_SCHEMA(('{schema_str}'));\n"
                    "ENDSEC;\nDATA;\n"
                    f"#10 = ( LENGTH_UNIT() NAMED_UNIT(*) {unit_expr} );\n"
                    "ENDSEC;\nEND-ISO-10303-21;\n",
                    encoding='utf-8',
                )
                meta = detect_cad_header_metadata(step_file, 'step')
                self.assertEqual(meta['protocol'], expected_proto)
                self.assertEqual(meta['originating_system'], system_name)
                self.assertEqual(meta['units'], expected_unit)

            for ext, keyword in [
                ('.stl', 'triangle-only'),
                ('.x_t', 'Parasolid'),
                ('.x_b', 'Parasolid'),
                ('.sat', 'ACIS'),
                ('.sab', 'ACIS'),
                ('.3dm', 'Rhino'),
            ]:
                with self.assertRaisesRegex(ValueError, keyword):
                    open_source(None, Path(directory) / f'part{ext}')

    def test_curved_holed_solid_colours_names_multi_body_assemblies_and_healing(self):
        import gmsh
        with tempfile.TemporaryDirectory() as directory:
            # 1. Create an attributable STEP fixture: curved cylinder with an off-centre cylindrical through-hole
            holed_step = Path(directory) / 'holed_cylinder.step'
            gmsh.initialize(['jorvik'], readConfigFiles=False, interruptible=False)
            try:
                gmsh.option.setNumber('General.Terminal', 0)
                gmsh.model.add('holed_cylinder')
                outer = gmsh.model.occ.addCylinder(0, 0, 0, 0, 0, 2, 1.0)
                inner = gmsh.model.occ.addCylinder(0.2, 0, 0, 0, 0, 2, 0.35)
                gmsh.model.occ.cut([(3, outer)], [(3, inner)])
                gmsh.model.occ.synchronize()
                gmsh.write(str(holed_step))
            finally:
                gmsh.finalize()

            holed_geom = load_geometry(holed_step)
            self.assertEqual(holed_geom['solid_count'], 1)
            self.assertEqual(holed_geom['assembly']['disconnected_body_count'], 1)
            self.assertTrue(holed_geom['assembly']['is_manifold'])
            self.assertFalse(holed_geom['assembly']['has_mixed_open_shells'])

            # Sample all faces & interior of the holed curved solid and verify containment + outward normals
            face_tags = [[2, e['tag']] for e in holed_geom['entities'] if e['dimension'] == 2]
            req = {k: deepcopy(holed_geom[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
            surf_batch = sample_geometry({
                **req, 'target': 'selection', 'entities': face_tags,
                'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'holed_walls', 'resolution': 16,
            })
            self.assertEqual(surf_batch['normal_status'], 'available')
            self.assertTrue(validate_trimming_and_containment(surf_batch)['containment_verified'])

            vol_batch = sample_geometry({
                **req, 'target': 'interior', 'entities': [],
                'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'holed_interior', 'resolution': 16,
            })
            # Verify no interior point falls inside the cylindrical hole (x - 0.2)^2 + y^2 < 0.35^2
            pts = np.asarray(vol_batch['coordinates'])
            hole_r = np.hypot(pts[:, 0] - 0.2, pts[:, 1])
            self.assertTrue((hole_r >= 0.35 - 1e-2).all())

            # 2. Optional OCC healing & explicit region entity remapping on holed_cylinder.step
            regions = put_region([], holed_geom, 'walls', face_tags[:2], '#41c4b8')
            heal_report = heal_and_remap(holed_step, tolerance=1e-6, regions=regions)
            self.assertFalse(heal_report['topology_changed'])
            self.assertEqual(heal_report['remapped_regions'][0]['entities'], regions[0]['entities'])

            # 3. Disconnected multi-body assembly vs. mixed open-shell + solid rejection
            disconnected_geo = Path(directory) / 'disconnected_bodies.geo'
            disconnected_geo.write_text(
                'SetFactory("OpenCASCADE");\n'
                'Box(1) = {0, 0, 0, 1, 1, 1};\n'
                'Box(2) = {3, 0, 0, 1, 1, 1};\n'
                'Color Red { Surface{1}; }\n',
                encoding='utf-8',
            )
            disc_geom = load_geometry(disconnected_geo)
            self.assertEqual(disc_geom['solid_count'], 2)
            self.assertEqual(disc_geom['assembly']['disconnected_body_count'], 2)
            self.assertEqual(disc_geom['assembly']['shared_interface_faces'], [])
            self.assertEqual(disc_geom['cad_metadata']['coloured_entity_count'], 1)
            face1 = next(e for e in disc_geom['entities'] if e['dimension'] == 2 and e['tag'] == 1)
            self.assertEqual(face1['colour'], '#ff0000')

            mixed_geo = Path(directory) / 'mixed_solid_and_open_shell.geo'
            mixed_geo.write_text(
                'SetFactory("OpenCASCADE");\n'
                'Box(1) = {0, 0, 0, 1, 1, 1};\n'
                'Rectangle(99) = {5, 0, 0, 1, 1};\n',
                encoding='utf-8',
            )
            mixed_geom = load_geometry(mixed_geo)
            self.assertTrue(mixed_geom['assembly']['has_mixed_open_shells'])
            self.assertEqual(mixed_geom['interior_domain'], 'open_or_nonplanar')

    def test_geo_include_dependency_bundling_and_checksum_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            src_dir = Path(directory) / 'src_model'
            sub_dir = src_dir / 'includes'
            sub_dir.mkdir(parents=True)
            params_inc = sub_dir / 'params.geo'
            params_inc.write_text('r_in = 0.5;\nr_out = 1.0;\nlc = 0.3;\n', encoding='utf-8')
            main_geo = src_dir / 'main_annulus.geo'
            main_geo.write_text(
                'Include "includes/params.geo";\n'
                'Point(1) = {0, 0, 0, lc};\nPoint(2) = {r_in, 0, 0, lc};\nPoint(3) = {0, r_in, 0, lc};\n'
                'Point(4) = {-r_in, 0, 0, lc};\nPoint(5) = {0, -r_in, 0, lc};\n'
                'Point(6) = {r_out, 0, 0, lc};\nPoint(7) = {0, r_out, 0, lc};\n'
                'Point(8) = {-r_out, 0, 0, lc};\nPoint(9) = {0, -r_out, 0, lc};\n'
                'Circle(1) = {2, 1, 3};\nCircle(2) = {3, 1, 4};\nCircle(3) = {4, 1, 5};\nCircle(4) = {5, 1, 2};\n'
                'Circle(5) = {6, 1, 7};\nCircle(6) = {7, 1, 8};\nCircle(7) = {8, 1, 9};\nCircle(8) = {9, 1, 6};\n'
                'Curve Loop(1) = {5, 6, 7, 8};\nCurve Loop(2) = {1, 2, 3, 4};\n'
                'Plane Surface(1) = {1, 2};\n',
                encoding='utf-8',
            )
            deps = discover_geo_dependencies(main_geo)
            self.assertEqual(len(deps), 1)
            self.assertEqual(deps[0]['path'], 'includes/params.geo')

            geometry = load_geometry(main_geo)
            self.assertEqual(geometry['source_dependencies'], deps)

            proj_path = Path(directory) / 'bundled.jorvik'
            saved_state = state()
            saved_state['selected_area'] = None
            save_project(proj_path, geometry, [], saved_state)

            # Delete original source directory and verify extraction from the bundled project archive
            params_inc.unlink()
            main_geo.unlink()
            restored_dir = Path(directory) / 'restored'
            extracted_main = extract_project_sources(proj_path, restored_dir)
            self.assertTrue(extracted_main.is_file())
            self.assertTrue((restored_dir / 'includes/params.geo').is_file())

            # Sampling on the extracted main .geo succeeds with the bundled dependency
            req = {k: deepcopy(geometry[k]) for k in ('source_hash', 'document', 'source_format', 'import_options', 'source_dependencies')}
            req['source'] = str(extracted_main)
            req.update(target='interior', entities=[], method='sobol', count=32, seed=42, label='int', resolution=16)
            batch = sample_geometry(req)
            self.assertEqual(batch['count'], 32)

            # Tampering with the extracted dependency is caught before Gmsh runs
            (restored_dir / 'includes/params.geo').write_text('r_in = 0.8;\nr_out = 1.0;\nlc = 0.3;\n', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'dependency changed'):
                sample_geometry(req)

            # Tampering with the bundled dependency inside the .jorvik archive is caught on load_project
            with zipfile.ZipFile(proj_path) as archive:
                files = {name: archive.read(name) for name in archive.namelist()}
            files['source_deps/includes/params.geo'] = b'corrupted'
            with zipfile.ZipFile(proj_path, 'w') as archive:
                for name, data in files.items():
                    archive.writestr(name, data)
            with self.assertRaisesRegex(ValueError, 'dependency checksum'):
                load_project(proj_path)


if __name__ == '__main__':
    unittest.main()
