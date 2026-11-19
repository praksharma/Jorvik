"""Tests for STEP/IGES coordinate unit detection and multi-volume 3D subdomain sampling."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from jorvik.cad_import import DEFAULT_UNITS, detect_coordinate_units
from jorvik.export import to_xarray
from jorvik.geometry import load_geometry
from jorvik.sampling import sample_geometry
from jorvik.sampling_recipe import bind_recipe, create_recipe

FIXTURES = Path(__file__).resolve().parents[1] / 'examples/geometries'

TWO_VOLUME_GEO = """SetFactory("OpenCASCADE");
Box(1) = {-1, 0, 0, 1, 1, 1};
Box(2) = {0, 0, 0, 1, 1, 1};
BooleanFragments{ Volume{1}; Delete; }{ Volume{2}; Delete; }
Physical Volume("fluid_subdomain", 101) = {1};
Physical Volume("solid_subdomain", 102) = {2};
"""


class MultiVolumeAndUnitsTest(unittest.TestCase):
    def test_step_and_iges_unit_detection(self):
        cad = FIXTURES / 'cad'
        self.assertEqual(detect_coordinate_units(cad / 't20_data.step', 'step'), 'mm')
        self.assertEqual(detect_coordinate_units(cad / 'cube_filet.stp', 'step'), 'mm')
        self.assertEqual(detect_coordinate_units(cad / 'cube1000.igs', 'iges'), 'mm')
        self.assertEqual(detect_coordinate_units(cad / 'cylinder.brep', 'brep'), DEFAULT_UNITS)

        with tempfile.TemporaryDirectory() as directory:
            step_m = Path(directory) / 'metre.step'
            step_m.write_text("#10 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT($, .METRE.) );\n", encoding='utf-8')
            self.assertEqual(detect_coordinate_units(step_m, 'step'), 'm')

            step_in = Path(directory) / 'inch.step'
            step_in.write_text("#10 = CONVERSION_BASED_UNIT('INCH', #11);\n", encoding='utf-8')
            self.assertEqual(detect_coordinate_units(step_in, 'step'), 'in')

            iges_m = Path(directory) / 'metre.igs'
            iges_m.write_text(
                "1H,,1H;,7Htest.ig,37Hfile,15Hsys,32,38,6,308,15,7Htest.ig,1.0,6,1HM,1,0.01,15H20260101.000000;G      1\n",
                encoding='utf-8',
            )
            self.assertEqual(detect_coordinate_units(iges_m, 'iges'), 'm')

    def test_multi_volume_subdomains_and_3d_physical_groups(self):
        with tempfile.TemporaryDirectory() as directory:
            geo_path = Path(directory) / 'two_volumes.geo'
            geo_path.write_text(TWO_VOLUME_GEO, encoding='utf-8')
            geometry = load_geometry(geo_path)
            self.assertEqual(geometry['solid_count'], 2)
            vol_groups = [g for g in geometry['groups'] if g['dimension'] == 3]
            self.assertEqual(len(vol_groups), 2)

            req_base = {k: deepcopy(geometry[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options', 'coordinate_units')}

            # 1. Sample Volume 1 via interior subset
            req_vol1 = {
                **req_base, 'target': 'interior', 'entities': [[3, 1]],
                'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'fluid_vol',
                'resolution': 16, 'compute_mode': 'reproducible', 'cpu_threads': 1,
            }
            recipe_vol1 = create_recipe(req_vol1)
            bound_vol1 = bind_recipe(recipe_vol1, geometry)
            self.assertEqual(bound_vol1['entities'], [[3, 1]])
            batch_vol1 = sample_geometry(bound_vol1)
            coords_1 = np.asarray(batch_vol1['coordinates'])
            self.assertTrue((coords_1[:, 0] <= 1e-9).all() and (coords_1[:, 0] >= -1.0 - 1e-9).all())
            self.assertEqual(set(batch_vol1['entity_tags']), {1})
            self.assertAlmostEqual(batch_vol1['domain_measure'], 1.0, places=4)

            # 2. Sample Volume 2 via 3D Physical Volume group
            req_grp2 = {
                **req_base, 'target': 'group', 'entities': [[3, 2]],
                'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'solid_group',
                'resolution': 16, 'compute_mode': 'reproducible', 'cpu_threads': 1,
            }
            recipe_grp2 = create_recipe(req_grp2)
            bound_grp2 = bind_recipe(recipe_grp2, geometry)
            self.assertEqual(bound_grp2['target'], 'group')
            self.assertEqual(bound_grp2['entities'], [[3, 2]])
            batch_grp2 = sample_geometry(bound_grp2)
            coords_2 = np.asarray(batch_grp2['coordinates'])
            self.assertTrue((coords_2[:, 0] >= -1e-9).all() and (coords_2[:, 0] <= 1.0 + 1e-9).all())
            self.assertEqual(set(batch_grp2['entity_tags']), {2})
            self.assertAlmostEqual(batch_grp2['domain_measure'], 1.0, places=4)

            # 3. Sample whole domain interior across both volumes
            batch_all = sample_geometry({
                **req_base, 'target': 'interior', 'entities': [],
                'method': 'sobol', 'count': 128, 'seed': 42, 'label': 'whole_domain',
                'resolution': 16, 'compute_mode': 'reproducible', 'cpu_threads': 1,
            })
            self.assertEqual(set(batch_all['entity_tags']), {1, 2})
            self.assertAlmostEqual(batch_all['domain_measure'], 2.0, places=4)

            dataset = to_xarray([batch_vol1, batch_grp2, batch_all])
            self.assertEqual(dataset.attrs['coordinate_units'], DEFAULT_UNITS)


if __name__ == '__main__':
    unittest.main()
