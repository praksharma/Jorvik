# Jorvik

Geometry-aware point sampling for PINNs and scientific machine learning.

**Current status:** import Gmsh `.geo`, STEP, OCC BREP or IGES geometry, inspect it in the offline 3D
viewport, and select original faces or edges using separate tools. Includes
synchronized tree selection, multi-selection, imported group information,
dockable panes, a Sampled Areas tree and light/dark themes. Generate labelled datasets with seven
sampling methods, preview interior or boundary coordinates, and export labelled
xarray/NetCDF or NumPy NPZ arrays with applicable outward normals. Create boundary
regions independently of dataset labels, and save full portable projects.

## Quick start

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run jorvik
# Open an existing project document:
uv run jorvik path/to/model.jorvik
```

Open a fixture directly:

```sh
uv run jorvik examples/geometries/cfdbench/annulus_unstructured.geo
```

Select existing faces or edges, save boundary regions, generate datasets in
**Sampling**, then export NetCDF/NPZ or save a full `.jorvik` workspace.
For graphics compatibility, try `uv run jorvik --software-rendering`.

## Documentation

Read the [user guide](GUIDE.md), copy the [practical recipes](GUIDE.md#practical-recipes),
try the [annulus example](examples/sample_annulus.py),
or preview the searchable Zensical site:

```sh
uv sync --locked --group docs
uv run --group docs python scripts/sync_docs.py
uv run --group docs zensical serve
```

The site includes setup, geometry, regions, sampling, normals, exports, projects,
recovery, performance and development instructions. See the
[documentation workflow](GUIDE.md#documentation-site) for building and updating it.

## Intended workflow

Import prepared CAD → label existing edges/faces → sample regions and interior →
export xarray/NetCDF or NumPy NPZ datasets. CAD already contains the required
boundary partitions. Jorvik does not prescribe boundary values or solve PDEs.

Try the reproducible [annulus export example](examples/sample_annulus.py).
See [GUIDE.md](GUIDE.md), [TODO.md](TODO.md), and the original [roadmap](roadmap.txt).
Application shell adapted from PyniteGUI: [attribution](THIRD_PARTY.md).
Licensed under [GNU AGPL v3](LICENSE).
