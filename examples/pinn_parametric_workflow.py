"""End-to-end parametric PINN workflow: separate boundary/interior clouds, quadrature weights,
boundary-distance fields d(x), .geo dimensional CAD regeneration, topology-change rejection,
and xarray.DataTree consolidated sweep loading.

Run from a checkout: uv run python examples/pinn_parametric_workflow.py --output-dir <dir>
"""
import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import numpy as np

from jorvik.geometry import load_geometry
from jorvik.parameter_design import generate_configurations
from jorvik.parametrisation import transform_batches
from jorvik.regions import put_region, region_snapshot
from jorvik.sampling import sample_geometry_batch
from jorvik.sweep_container import load_consolidated_sweep
from jorvik.sweep_export import export_sweep
from jorvik.sweep_results import iter_sweep_datasets, training_arrays

PARAMETRIC_GEO = """// Parametric 2D annulus for Laplace / heat-conduction PINN workflow
r_in = 0.4;
r_out = 1.0;
lc = 0.22;

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
"""


def run(output_dir, boundary_count=64, interior_count=128):
    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    geo_path = output_dir / 'parametric_annulus.geo'
    geo_path.write_text(PARAMETRIC_GEO, encoding='utf-8')

    geometry = load_geometry(geo_path)
    regions = []
    for label, tags, colour in [('inner', range(1, 5), '#ffc857'), ('outer', range(5, 9), '#41c4b8')]:
        regions = put_region(regions, geometry, label, [[1, t] for t in tags], colour)

    req_common = {
        key: deepcopy(geometry[key])
        for key in ('source', 'source_hash', 'document', 'source_format', 'import_options', 'coordinate_units')
    }
    requests = []
    for region in regions:
        snap = region_snapshot(regions, geometry, region['id'])
        requests.append({
            **req_common,
            'label': region['label'],
            'target': 'region',
            'entities': region['entities'],
            'boundary_region': snap,
            'method': 'sobol',
            'count': boundary_count,
            'seed': 42,
            'resolution': 32,
            'compute_mode': 'reproducible',
            'cpu_threads': 1,
        })
    requests.append({
        **req_common,
        'label': 'interior',
        'target': 'interior',
        'entities': [],
        'method': 'sobol',
        'count': interior_count,
        'seed': 42,
        'resolution': 32,
        'compute_mode': 'reproducible',
        'cpu_threads': 1,
        'refinement': {
            'mode': 'boundary_distance',
            'entities': regions[0]['entities'],
            'bias': 3.0,
            'decay': 0.1,
            'include_distance': True,
        },
    })
    batches = sample_geometry_batch(requests)

    # Verify topology-change / degenerate-geometry rejection when r_in collapses to 0.0
    base_setup = {
        'schema_version': 2,
        'scope': 'domain',
        'regions': [],
        'source_hash': geometry['source_hash'],
        'translation': [0.0, 0.0, 0.0],
        'rotation_axis': [0.0, 0.0, 1.0],
        'rotation_angle': 0.0,
        'angle_units': 'degrees',
        'scale': [1.0, 1.0, 1.0],
        'pivot': [0.0, 0.0, 0.0],
        'order': ['scale', 'rotate', 'translate'],
        'frame': 'world',
        'geo_parameters': deepcopy(geometry['geo_parameters']),
    }
    collapsed_setup = dict(base_setup, geo_overrides={'r_in': 0.0})
    topology_rejected = False
    try:
        transform_batches(batches, collapsed_setup)
    except Exception:
        topology_rejected = True
    if not topology_rejected:
        raise RuntimeError('Expected collapsed r_in=0.0 regeneration to be rejected.')

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
    base_setup['design'] = design
    configurations = generate_configurations(base_setup, design)

    sweep_dir = output_dir / 'pinn_sweep'
    manifest = export_sweep(batches, base_setup, configurations, sweep_dir, file_format='nc', consolidated=True)
    if manifest['status'] != 'complete':
        raise RuntimeError(f"Sweep export failed: {manifest}")

    # Evaluate weak/strong-form harmonic PINN balance across every regenerated configuration
    flux_balances = []
    for config in iter_sweep_datasets(sweep_dir, require_integrity=True):
        r_in = config['parameters']['hole_radius']
        inputs = training_arrays(config)
        assert inputs['inner'].shape == (boundary_count, 4)
        assert inputs['outer'].shape == (boundary_count, 4)
        assert inputs['interior'].shape == (interior_count, 4)

        by_label = {b['label']: b for b in config['datasets']}
        # Exact harmonic solution u(r) = ln(r / r_in) / ln(1 / r_in) satisfies grad(u) = (x, y, 0) / (r^2 * ln(1 / r_in))
        scale_factor = 1.0 / math.log(1.0 / r_in)
        net_flux = 0.0
        for bnd_label in ('inner', 'outer'):
            bnd = by_label[bnd_label]
            pts = np.asarray(bnd['coordinates'], dtype=np.float64)
            normals = np.asarray(bnd['normals'], dtype=np.float64)
            weights = np.asarray(bnd['weights'], dtype=np.float64)
            r2 = np.sum(pts[:, :2] ** 2, axis=1, keepdims=True)
            grad_u = np.column_stack([pts[:, :2] / r2, np.zeros(len(pts))]) * scale_factor
            flux = np.sum(weights * np.sum(grad_u * normals, axis=1))
            net_flux += float(flux)
        flux_balances.append(round(abs(net_flux), 6))

    tree = load_consolidated_sweep(sweep_dir / manifest['consolidated_bundle'])
    return {
        'sweep_dir': str(sweep_dir),
        'consolidated_bundle': str(sweep_dir / manifest['consolidated_bundle']),
        'configurations': len(configurations),
        'topology_rejection_verified': topology_rejected,
        'harmonic_flux_residuals': flux_balances,
        'datatree_inner_shape': list(tree['regions/inner'].ds['coordinates'].shape),
        'datatree_interior_shape': list(tree['regions/interior'].ds['coordinates'].shape),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=Path('pinn-workflow-output'))
    parser.add_argument('--boundary-count', type=int, default=64)
    parser.add_argument('--interior-count', type=int, default=128)
    args = parser.parse_args()
    print(json.dumps(run(args.output_dir, args.boundary_count, args.interior_count), indent=2))
