"""Tests for .geo parameter discovery, dimensional CAD regeneration and parametric CAD sweeps."""
from copy import deepcopy
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np

from jorvik.cad_regeneration import apply_geo_overrides_text, discover_geo_parameters
from jorvik.geometry import load_geometry
from jorvik.parameter_design import generate_configurations, validate_design
from jorvik.parametrisation import transform_batches, validate_setup
from jorvik.sampling import sample_geometry
from jorvik.sweep_export import export_sweep
from jorvik.sweep_results import iter_sweep_datasets
from test_affine_transforms import affine_setup

PARAMETRIC_ANNULUS_GEO = """// Parametric annulus for dimensional CAD regeneration
r_in = 0.4;
r_out = 1.0;
lc = 0.25;

Point(1) = {0, 0, 0, lc};
Point(2) = {r_in, 0, 0, lc};
Point(3) = {0, r_in, 0, lc};
Point(4) = {-r_in, 0, 0, lc};
Point(5) = {0, -r_in, 0, lc};
Point(6) = {r_out, 0, 0, lc};
Point(7) = {0, r_out, 0, lc};
Point(8) = {-r_out, 0, 0, lc};
Point(9) = {0, -r_out, 0, lc};

Circle(1) = {2, 1, 3};
Circle(2) = {3, 1, 4};
Circle(3) = {4, 1, 5};
Circle(4) = {5, 1, 2};
Circle(5) = {6, 1, 7};
Circle(6) = {7, 1, 8};
Circle(7) = {8, 1, 9};
Circle(8) = {9, 1, 6};

Curve Loop(1) = {5, 6, 7, 8};
Curve Loop(2) = {1, 2, 3, 4};
Plane Surface(1) = {1, 2};

Physical Curve("inner", 1) = {1, 2, 3, 4};
Physical Curve("outer", 2) = {5, 6, 7, 8};
Physical Surface("domain", 3) = {1};
"""


class CadRegenerationTest(unittest.TestCase):
    def test_discover_and_override_geo_parameters(self):
        with tempfile.TemporaryDirectory() as directory:
            geo_path = Path(directory) / 'parametric_annulus.geo'
            geo_path.write_text(PARAMETRIC_ANNULUS_GEO, encoding='utf-8')
            params = discover_geo_parameters(geo_path)
            self.assertEqual(params, [
                {'name': 'r_in', 'default': 0.4},
                {'name': 'r_out', 'default': 1.0},
                {'name': 'lc', 'default': 0.25},
            ])
            rewritten = apply_geo_overrides_text(PARAMETRIC_ANNULUS_GEO, {'r_in': 0.65})
            self.assertIn('r_in = 0.65;', rewritten)
            self.assertIn('r_out = 1.0;', rewritten)

            geometry = load_geometry(geo_path)
            self.assertEqual(geometry['geo_parameters'], params)

            req_base = {k: deepcopy(geometry[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
            inner_batch = sample_geometry({
                **req_base, 'target': 'group', 'entities': [[1, 1], [1, 2], [1, 3], [1, 4]],
                'method': 'sobol', 'count': 64, 'seed': 42, 'label': 'inner', 'resolution': 32,
                'compute_mode': 'reproducible', 'cpu_threads': 1,
            })
            interior_batch = sample_geometry({
                **req_base, 'target': 'interior', 'entities': [],
                'method': 'sobol', 'count': 128, 'seed': 42, 'label': 'interior', 'resolution': 32,
                'compute_mode': 'reproducible', 'cpu_threads': 1,
            })

            # Regenerate with r_in = 0.7 (feature-level dimensional change, unlike affine scaling)
            setup = affine_setup(source_hash=geometry['source_hash'])
            setup['geo_parameters'] = deepcopy(params)
            setup['geo_overrides'] = {'r_in': 0.7}
            validate_setup(setup, [inner_batch, interior_batch])
            regenerated = transform_batches([inner_batch, interior_batch], setup)

            reg_inner, reg_interior = regenerated
            inner_radii = np.linalg.norm(np.asarray(reg_inner['coordinates'])[:, :2], axis=1)
            np.testing.assert_allclose(inner_radii, 0.7, atol=1e-6)
            self.assertAlmostEqual(reg_inner['domain_measure'], 2.0 * math.pi * 0.7, places=3)

            int_radii = np.linalg.norm(np.asarray(reg_interior['coordinates'])[:, :2], axis=1)
            self.assertTrue((int_radii >= 0.7 - 1e-3).all() and (int_radii <= 1.0 + 1e-3).all())
            self.assertAlmostEqual(reg_interior['domain_measure'], math.pi * (1.0 - 0.7 ** 2), delta=0.04)

    def test_parametric_cad_sweep_export_and_verified_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            geo_path = Path(directory) / 'parametric_annulus.geo'
            geo_path.write_text(PARAMETRIC_ANNULUS_GEO, encoding='utf-8')
            geometry = load_geometry(geo_path)
            req_base = {k: deepcopy(geometry[k]) for k in ('source', 'source_hash', 'document', 'source_format', 'import_options')}
            inner_batch = sample_geometry({
                **req_base, 'target': 'group', 'entities': [[1, 1], [1, 2], [1, 3], [1, 4]],
                'method': 'sobol', 'count': 32, 'seed': 42, 'label': 'inner', 'resolution': 32,
                'compute_mode': 'reproducible', 'cpu_threads': 1,
            })

            design = {
                'schema_version': 1,
                'method': 'cartesian',
                'seed': 42,
                'count': 3,
                'parameters': [{
                    'name': 'hole_radius',
                    'target': 'geo:r_in',
                    'mode': 'values',
                    'values': [0.3, 0.5, 0.7],
                    'reference': 0.4,
                    'unit': 'source',
                }],
            }
            base = affine_setup(source_hash=geometry['source_hash'], design=design)
            base['geo_parameters'] = deepcopy(geometry['geo_parameters'])
            validate_design(design, base)
            records = generate_configurations(base, design)
            self.assertEqual(len(records), 3)
            self.assertEqual([r['setup']['geo_overrides']['r_in'] for r in records], [0.3, 0.5, 0.7])

            sweep_dir = Path(directory) / 'cad_sweep'
            report = export_sweep([inner_batch], base, records, sweep_dir, file_format='nc', consolidated=True)
            self.assertEqual(report['status'], 'complete')
            self.assertEqual(report['succeeded_count'], 3)
            self.assertTrue((sweep_dir / 'sweep_bundle.nc').is_file())

            radii_seen = []
            for item in iter_sweep_datasets(sweep_dir):
                coords = item['datasets'][0]['coordinates']
                radius = float(np.mean(np.linalg.norm(coords[:, :2], axis=1)))
                radii_seen.append(round(radius, 3))
            self.assertEqual(radii_seen, [0.3, 0.5, 0.7])


if __name__ == '__main__':
    unittest.main()
