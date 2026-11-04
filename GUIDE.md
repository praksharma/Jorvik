# Jorvik guide

## Development preview and release checks

This remains **0.1.0.dev0**, a development preview. The current release slice
supports import/selection, user-defined boundary regions, labelled sampling,
applicable outward normals, NetCDF/NPZ exports, project persistence and explicit
transform previews. Region labels carry metadata; physics and solvers remain
outside the application.

On 16 October 2026 the built wheel was installed into a separate environment
using the locked dependencies and validated on macOS arm64 (27.0.1), Python
3.12.13. The installation check imported from that environment's `site-packages`,
verified packaged offline assets/licences, rendered samples in the real WebGL
viewport, and checked CAD topology, boundary/interior sampling, normals,
NetCDF/NPZ round trips and project restoration. The command-line entry point
reported `Jorvik 0.1.0.dev0`. This does not establish Windows/Linux support.

To repeat installation validation from a checkout on macOS/Linux:

```sh
uv build
uv export --locked --no-dev --no-emit-project --no-hashes --output-file /tmp/jorvik-requirements.txt
uv venv /tmp/jorvik-release-check
uv pip install --python /tmp/jorvik-release-check/bin/python -r /tmp/jorvik-requirements.txt dist/jorvik-0.1.0.dev0-py3-none-any.whl
/tmp/jorvik-release-check/bin/python -I scripts/validate_installation.py --installed --render
/tmp/jorvik-release-check/bin/jorvik --version
```

`--render` needs a desktop session; omit it for geometry/export checks alone.
The validator uses temporary files and isolated Qt settings. It does not change
an existing user's project or settings. Use a fresh environment directory for
each installation check. The historical macOS check above used Gmsh 4.15.2,
NumPy 2.5.3, SciPy 1.18.1, xarray 2026.9.0 and PySide6 6.11.2.

On 29 October 2026, Linux/Wayland compatibility was checked with Python 3.14.8
and PySide6 6.12.0. All 53 Jorvik tests passed in a separate compatibility
environment. The built wheel was then installed into an existing application's
Python 3.14 environment with its existing packages constrained; NumPy 2.5.2,
PySide6 6.12.0 and the CUDA-enabled Torch installation remained unchanged.
All 53 tests also passed using the package installed in the shared environment.
The installed-package validator passed with Gmsh 4.15.2, SciPy 1.18.1 and
xarray 2026.9.0, including the offline viewport, sampling, normals, NetCDF/NPZ
round trips and project restoration. Fourteen focused host-application tests
also passed, including CUDA numerical checks, dataset import, sampling controls
and model-file lifecycle. The standalone Python 3.12.15 environment passed the
same geometry/export/rendering validator with PySide6 6.12.0. These are local
installation checks; Windows and broader distribution coverage remain pending.

Jorvik still requires Python 3.12 or newer. Its standalone `.python-version`
remains 3.12; this does not require another application's environment to use
3.12. Install Jorvik with the receiving environment's interpreter. From a source
checkout, build the wheel, then target that existing environment explicitly:

```sh
uv build
uv pip install --python /path/to/host/.venv/bin/python dist/jorvik-0.1.0.dev0-py3-none-any.whl
uv pip check --python /path/to/host/.venv/bin/python
```

Jorvik pins PySide6 6.12.0 to share Qt with the receiving application. Include
Jorvik in that application's dependency declarations if its environment manager
should retain/reinstall it during future synchronisation. A wheel installation
is a snapshot; rebuild and reinstall it after making Jorvik changes.

Run the attributed annulus example from a checkout:

```sh
uv run python examples/sample_annulus.py --output-dir /tmp/jorvik-annulus --count 128
```

It creates independent `inner`, `outer` and `interior` arrays in `annulus.nc` and
`annulus.npz`, with outward boundary co-normals and immutable region provenance.
It reloads both exports, checks matching arrays, and prints the resulting shapes.
The Python example shows how to obtain separate training coordinates and normal
arrays for downstream scientific ML. Its face/edge identifiers are specific to
the included CFDBench annulus; do not reuse them blindly with other CAD.

Before a general release, finish broader CAD trim/containment validation,
small-feature and normal-orientation checks, real exporter compatibility,
large-project memory reductions/viewport measurements and cross-platform
installation checks. Unknown units and assembly metadata remain
explicit limitations. Full parameter sweeps, rotation/scaling, CAD regeneration
and licensed translators are later roadmap work, not implemented release claims.

## Installation and first dataset

Use Python 3.12 or newer and [uv](https://docs.astral.sh/uv/). From a checkout:

```sh
uv sync --locked
uv run jorvik examples/geometries/cfdbench/annulus_unstructured.geo
```

1. Select **Edges** (`E`) and click an inner arc. Ctrl/Shift-click adds arcs.
2. Click **Boundary regions…**, name the selection `inner`, then **Save region**.
3. In **Sampling**, choose **Region: inner**, **Sobol**, 128 points and a seed.
4. Click **Generate samples** and inspect the new dataset in **Sampled Areas**.
5. Choose **Domain interior** to generate a separate interior dataset.
6. Use **Export samples…** for NetCDF/NPZ, and **Save project** (`Ctrl+S`) to
   preserve the editable workspace.

A boundary region labels CAD entities; a dataset label names one generated point
cloud. Region edits do not rewrite previously generated dataset provenance.
Use **Help → Graphics information** to inspect the renderer. For compatibility,
try `uv run jorvik --software-rendering`.

The fixture's arc IDs are specific to that geometry. With unfamiliar CAD, inspect
original faces/edges and confirm domain validity and sampling accuracy first.

## Desktop foundation

Drag with the left mouse button to orbit; right-drag to pan; scroll to zoom.
Use the camera selector or the bottom-right orientation gizmo for standard views.
The reference grid is hidden by default. Use Grid to show it in the XY plane
when needed. Grid and axes can be toggled independently.
Reset view returns to the default isometric camera; it fits the loaded geometry.

Left: Geometry and Sampled Areas. Right: Properties and Sampling. The left pane
contains independently scrollable geometry and sampled-area trees; drag their
divider to adjust their heights. Properties and Sampling scroll within their
panes when content grows. Selecting many entities keeps neighbouring panes
available. Properties text can be selected and copied. There is no bottom Results
pane; older saved layouts do not recreate it.
Use View to show/hide panes; drag their titles to rearrange or detach them.
View → Reset layout restores the initial layout. Window geometry, panel layout,
and theme persist between launches. Use File → Save project to preserve CAD, sampled datasets and workspace settings.

## Architecture

`geometry.py` runs Gmsh in a separate process, returning entity identities,
physical groups and per-entity display tessellation. Supported inputs are `.geo`,
STEP (`.step`, `.stp`), OCC BREP (`.brep`) and IGES (`.iges`, `.igs`). Geometry is retained in the source file; display triangles
are not a substitute for exact geometry in future sampling.

`src/jorvik/app.py` owns the Qt window, menus, docks and settings.
`viewport.py` embeds Qt WebEngine and exposes a small QWebChannel bridge.
`viewport3d/` contains local Three.js assets, OrbitControls and the orientation gizmo.
No CDN is required. Structural models, solvers, supports, loads and force diagrams
from PyniteGUI are excluded. Keep future CAD and sampling services independent of Qt.

## Agreed product scope

- CAD contains the faces/edges needed to distinguish boundary-condition regions.
- Users label one or more existing entities per region; labels are metadata.
- No CAD editing, boundary partitioning, BC-value assignment, or PDE solver.
- Sample interiors and individual boundary regions; include normals where applicable.
- Primary Python interface: xarray Dataset, persisted as NetCDF. Alternative: named
  NumPy arrays in NPZ. No Parquet. Exports use a versioned dataset schema.
- Preserve seeds, geometry references and sampling settings for reproducibility.
- Validate containment, surface distributions, normals, and labels before release.

## Development validation

```sh
uv run python -m unittest discover -s tests -v
uv build
```

The smoke test uses the real WebEngine viewport, checks renderer readiness,
camera changes, theme updates, dock persistence, and graphics errors. Run with a
desktop display; the test selects software rendering. Headless backend support
depends on the host graphics stack.
Check an interactive launch for orbit, zoom, pan, gizmo clicks and dock dragging.

## Documentation site

The documentation site uses [Zensical](https://zensical.org/docs/), installed only
through the optional `docs` dependency group. Preview it from a checkout:

```sh
uv sync --locked --group docs
uv run --group docs python scripts/sync_docs.py
uv run --group docs zensical serve
```

Open the local address printed by the preview command. To produce static HTML:

```sh
uv run --group docs python scripts/sync_docs.py --check
uv run --group docs zensical build --strict
```

The build writes ignored output to `site/`. The site uses local theme assets and
system fonts. The published URL is <https://praksharma.github.io/Jorvik/>, set as
`site_url` in `zensical.toml` so project-site links and canonical URLs include
`/Jorvik/`.

In GitHub Settings → Pages, select **GitHub Actions** as the source. The
Documentation workflow checks canonical pages and performs a clean, strict
Zensical build. Pull requests are checked without deploying. Matching pushes to
`main`, or a manual workflow run on `main`, upload the generated `site/` artifact
and deploy it through the `github-pages` environment. Publication must upload
`site/`, not raw `docs/` Markdown: GitHub's default Markdown/Jekyll rendering does
not apply the Zensical theme. After pushing, check both the build and deploy jobs
in Actions; changing the Pages source alone does not publish a Zensical build.

Keep detailed workflows, architecture, limitations and test instructions in this
`GUIDE.md`. Edit `TODO.md` for the roadmap and `THIRD_PARTY.md` for attribution.
`scripts/sync_docs.py` creates the corresponding committed pages under `docs/`;
do not edit those generated pages directly. Add new guide headings to the script's
page map so every section remains visible. The check fails on missing, stale or
unmapped content. Edit `docs/index.md` for the landing page.

## Provenance

See THIRD_PARTY.md for the source revision and retained licences. roadmap.txt is
the original planning reference; README, this guide and TODO describe current scope.

## Foundation validation record

Validated on 2026-10-10 with Python 3.12 and PySide6 6.12: real WebEngine
startup, camera/bridge response, pane visibility and persistence, and theme
settings passed on Wayland using software rendering. The initial window was
visually inspected. Source and wheel distributions built successfully. This host
emits graphics-driver warnings; its offscreen and X11 paths were not usable.
Interactive mouse gestures and other operating systems still need manual checks.

## Geometry import and selection

Open a supported geometry file with File → Open Geometry, or pass its path on the command
line. Import runs outside the UI process; a failed import retains the previous
model. Closing the window stops an active import. File replacement clears old
selections. Face and edge IDs are scoped to the loaded document.

Select faces (F) uses a crosshair cursor; Select edges (E) uses a cell cursor.
Switching modes clears selection. Click selects one entity; Ctrl/Shift-click
toggles additional entities; clicking empty space or Esc clears selection.
Dragging orbits without selecting. The geometry tree can also select entities
and automatically switches to their face/edge mode. Properties shows IDs,
geometry types and imported physical group IDs. Imported groups are read-only.

Hover is teal; selection is orange. Edge hover uses a 4-pixel stroke and
selected edges use a 6-pixel stroke, maintaining their screen width while
zooming. Ordinary edges remain thin. Surface triangles map to original faces;
only original geometry edges participate in edge selection. Edges hidden behind
nearer visible faces cannot be picked. Edge picking uses a fixed six-pixel
screen-space radius with depth tested at the nearest candidate edge point.
Boundary region editing and face hide/isolate controls are available below.
Sampling is available through the Sampling tab; its numerical mesh is separate
from the display tessellation.

Display tessellation follows the source geometry's surface meshing settings;
coarse inputs may look faceted. It is not a sampling accuracy guarantee.

Validation includes asynchronous import of both reference files, face clicks,
annulus hole rejection, edge clicks and multi-selection, tree synchronization,
file replacement, import failure preservation, and drag suppression. The real
Qt WebEngine renderer and QWebChannel bridge are exercised on Wayland.

## NVIDIA rendering on Linux/Wayland

Normal launches automatically apply the NVIDIA workaround inherited from
PyniteGUI when one NVIDIA GPU drives the connected display: Qt Vulkan backend,
NVIDIA EGL/Vulkan driver selection, and exclusion of Mesa Vulkan layers. These
settings apply only to this application process and respect existing graphics
environment overrides. Do not pass `--software-rendering` for GPU rendering.

PySide6 is now pinned to 6.12.0 for shared-environment compatibility. An earlier
Quadro RTX 4000 check found a Qt 6.12 startup stall and used 6.11.2 successfully.
On 29 October 2026, automatic rendering was verified with Python 3.14.8 and
PySide6 6.12.0 on the current Linux/Wayland host: the live WebGL context reported
`ANGLE (NVIDIA Corporation, NVIDIA GeForce RTX 3060 Laptop GPU/PCIe/SSE2, OpenGL ES 3.2)`
with no lost context. This does not establish Qt 6.12 behaviour on the earlier
Quadro system. Check Help → Graphics information for the observed renderer;
`--software-rendering` remains available when a host's graphics setup needs it.

Close the main window or use File → Quit (`Ctrl+Q`). Terminal `Ctrl+C` (SIGINT)
and SIGTERM are routed through the same close workflow rather than raising
`KeyboardInterrupt` inside a Qt callback. Unsaved changes still offer
Save/Discard/Cancel. When a project file operation is in progress, closing waits
for it to finish and retries automatically; it does not interrupt an atomic save.
The `qt.qpa.wayland: EGL not available` warning alone does not mean shutdown failed.
Process-level tests check terminal signals and normal close, with separate UI
checks for deferred closing and cancelled unsaved-change prompts.

## Sampling

The right dock contains Properties and Sampling tabs. Choose a target:
selected faces/edges (Ctrl/Shift-click for multiple entities), Domain interior,
an imported physical face/edge group, or a saved boundary region. Point count is the total across that
target, not a count per constituent entity. Each dataset has a unique label.
After successful generation, the label field automatically suggests the next
available numbered name: `boundary`, `boundary_1`, `boundary_2`, and so on.
Existing names are skipped; manually reusing a name also selects an available
numbered variant. You can enter a different base name at any time. Failed or
cancelled generation does not advance the suggested name. Each generation adds
a separate dataset and preserves earlier coordinates and entity tags. Sampled Areas
lists the generated datasets with their source entities.

Methods implemented:

| Method | Candidate design |
| --- | --- |
| Uniform random | Independent pseudorandom unit-cube coordinates |
| Regular grid | Complete Cartesian lattices or evenly spaced curve positions |
| LHS | SciPy Latin hypercube |
| Sobol | Scrambled SciPy Sobol, generated in power-of-two blocks |
| Halton | Scrambled SciPy Halton |
| Hammersley | Finite Hammersley set with a seeded random shift |
| Stratified | One random point per recursively partitioned equal-volume stratum |

The seed reproduces the candidate design for a fixed source, method, accuracy
and software version. Regular grid is deterministic and does not use the seed.
Sobol is strongest at power-of-two counts; truncation, geometric transformation
and rejection can change its balance properties. Likewise, final samples on an
irregular domain do not necessarily retain Cartesian LHS or stratification.
These coverage properties apply to the candidate space. See the
[SciPy QMC documentation](https://docs.scipy.org/doc/scipy/reference/stats.qmc.html).

### Geometry and accuracy

Curve coordinates are evaluated on the original Gmsh curves. Numerical
arc-length inversion distributes samples by length across the selected edges.
Surface coordinates are evaluated on the original Gmsh surfaces, with derivative
Jacobian weighting and rejection to account for varying area in parameter space.
The trimmed parameter domain is represented by a separate surface tessellation;
curved trim boundaries consequently have numerical tolerance. Planar face
candidates are also checked using Gmsh containment, including the annulus hole.
The Jacobian rejection envelope is estimated numerically with a safety margin;
generation fails if a candidate exceeds it. This is not a certified global bound.

3D interiors use a separate tetrahedral mesh and volume-weighted barycentric
sampling. Curved-domain approximation depends on mesh accuracy; this is not an
exact CAD solid containment guarantee for arbitrary concave shapes. Standard
and Fine cap the sampling mesh size at the geometry's largest bounding-box
extent divided by 32 and 64 respectively. This is independent of viewport
triangles. Compare accuracy levels before using unfamiliar curved domains for
scientific training. Surface parameter grids have regular parameter spacing,
not necessarily constant spacing measured on the curved surface.

Regular grid clips a complete grid to the target; actual counts can be lower
than the budget. Multi-face grids divide the budget equally between faces.
Other methods return the requested count. Curves use a length-weighted entity
allocation; random surface and volume sampling are area/volume weighted.

### Boundary normals

Boundary sampling evaluates derivatives on the original geometry and returns
finite unit `normals` when every point's outward orientation can be checked.
Faces bounding exactly one solid receive `outward_surface` normals. Edges
bounding one planar face receive in-plane `outward_conormal` vectors; hole
boundaries point into the hole. Interior samples and 3D edges have no unique
outward boundary normal. Open faces and shared interfaces do not receive guessed
orientations. If a derivative, parent domain or containment check is ambiguous,
the whole dataset omits normals and records `normal_status` and `normal_note`.
Properties and Sampled Areas show this availability.

Orientation probes test both sides of each evaluated normal using parent-domain
containment. Built-in volumes use a separate tetrahedral mesh because Gmsh does
not implement direct volume containment for that kernel. Probe distances range
from 1e-6 to 1e-2 of the parent extent to account for CAD/mesh tolerances; the first
unambiguous result determines the sign. These are numerical checks, not a proof
for arbitrary thin or invalid CAD. Broader containment validation and certified
sampling bounds remain open release work. Normals do not alter point coordinates.

Export schema **2** stores each available normal array under the manifest's
`normals` key, with shape `(point, xyz)` alongside its coordinate array. In xarray,
use `ds[ds[label].attrs['normals_coordinate']]`; in NPZ, load the key named by the
region manifest. `normal_kind`, `normal_status` and `normal_validation` remain
in metadata; numeric normals are not repeated in JSON. Project schema **2** also
stores normals as numeric arrays and continues to read schema-1 projects.
Malformed, non-finite, non-unit or missing advertised normal arrays are rejected.

### Previews and generation lifecycle

Points use circular markers with dark outlines, starting with warm white for
visibility against selected orange faces and under Night Light. Further datasets
use contrasting colours. The Point size slider changes the marker diameter
(3–16 screen pixels, default 7) immediately without regenerating coordinates;
its value is remembered between sessions. The Sampling pane scrolls when space
is limited so controls retain their natural height. Show sample points toggles the
preview; Show points through geometry reveals interior samples. Clear generated
datasets removes all in-memory datasets. Samples are generated in a separate
process, with cancellation; errors retain previous datasets. Geometry replacement
clears datasets. If the source geometry file changes, reopen it before sampling.
Metadata includes source SHA-256, target entities, method, seed, requested/actual
counts, accuracy and approximation notes. Export saves the current datasets;
full projects preserve application configurations, samples and applicable normals.

Validation covers all methods on curves, 2D and 3D interiors; reproducibility;
unit-design properties; annulus containment and area coverage; sphere surface
membership, face membership and area coverage; changed-source rejection; and
GUI generation, preview clearing and cancellation.


## Exporting samples

Use **Export samples…** in Sampling, or **File → Export samples…**
(Ctrl+Alt+E). It exports all generated datasets, including hidden point previews.
NetCDF (`.nc`) is the default; NumPy archive (`.npz`) is the alternative.
The controls become available after generation and are disabled while sampling.
Cancelling the save dialog leaves datasets untouched. Writes use a temporary file
in the destination folder and replace the destination only after a complete write;
a failed save preserves an existing file.

### Schema version 2

Each label is one float64 coordinate array of shape `(N, 3)`, in `x, y, z` order.
Different labels have independent point dimensions (`_gs_points_0`, etc.), so
unequal point counts never produce padding or NaNs. Entity tags are int32 arrays
with one tag per point. Coordinates retain source geometry units; the importer
does not currently identify or convert those units. Applicable normals are
exported as separate numeric arrays. Export is a data snapshot, not a saved editable application project.

Common identifier labels such as `boundary`, `boundary_1`, and `interior` are
array names in both formats. Labels containing spaces, punctuation or Unicode,
reserved names (`xyz`, `file`, `allow_pickle`, `like`), and names starting `_gs_`
use internal `_gs_region_N` names. Original labels are preserved in metadata.
The `_gs_` prefix is reserved for dimensions, entity-tag arrays and metadata.
No user label is silently lost or changed in the metadata.

The xarray interface is `jorvik.export.to_xarray(batches)`. NetCDF is written
through xarray's SciPy engine as NetCDF3 64-bit offset, using the existing SciPy
dependency. The Dataset has only coordinate arrays as data variables; `xyz` and
`_gs_entity_tags_N` and optional `_gs_normals_N` arrays are coordinates. Per-variable attributes include `label`,
`method`, `seed`, `entity_dimension`, `entity_tags_coordinate`, and
`metadata_json`. The latter preserves all generation metadata, including target
entities, source path/SHA-256, document identity, requested/actual counts,
accuracy and approximation notes. Global `metadata_json` contains the ordered
label-to-array map, schema/app versions and source-unit description.

```python
import json
import xarray as xr

with xr.open_dataset('samples.nc', engine='scipy') as ds:
    points = ds['boundary'].values  # (N, 3)
    tags = ds['boundary'].coords[ds['boundary'].attrs['entity_tags_coordinate']].values
    metadata = json.loads(ds['boundary'].attrs['metadata_json'])
```

NPZ uses the same coordinate array names, plus `xyz`, `_gs_entity_tags_N`,
optional `_gs_normals_N`, and
`_gs_metadata` (a Unicode scalar containing JSON). It contains no Python objects
and loads with `allow_pickle=False`. Its manifest maps each original label to
its coordinate and entity-tag array names and retains generation metadata.

```python
import json
import numpy as np

with np.load('samples.npz', allow_pickle=False) as data:
    points = data['boundary']  # (N, 3)
    manifest = json.loads(data['_gs_metadata'].item())
    by_label = {item['label']: item for item in manifest['datasets']}
    tags = data[by_label['boundary']['entity_tags']]
```

Validation covers unequal array lengths, exact coordinate/tag round trips,
Unicode/reserved labels, invalid data, preservation of existing files on save
failure, and both export options from the actual sampled-face UI workflow.


## Sampled Areas tree

The lower part of the left pane replaces the former bottom Results dock. Each
label expands to Entities, Points, Method, Seed and Status. Generated point
counts include thousands separators. Check a generated label to show its points;
uncheck it to hide only that dataset. The global Show sample points control
still hides all previews and preserves individual visibility choices when turned
back on. Point-size and through-geometry settings respect those choices too.
Export always includes all generated datasets, even hidden ones.

Selecting a label or one of its detail rows shows its metadata and approximation
notes in Properties. This does not change the active CAD entity selection or
sampling settings. A running request appears with status Generating; a cancelled
or failed request remains as Not generated, with no visibility checkbox or
exported coordinates. These entries represent attempted runs, not saved region
configurations. Clear generated datasets clears the tree, including unfinished
attempts; replacing geometry clears it as well.


## Toolbar and visibility

The viewport tools use theme-aware icons and hover tooltips. Separators group
file operations, camera controls, grid/axes aids, selection, visibility and region
isolation. Open/export are also available in File; visibility actions have named
entries under View → Visibility. The camera orientation dropdown retains view
names so each direction remains explicit. Keyboard shortcuts remain available.

Geometry is the master visibility switch. Faces and Edges independently control
shading and wireframe; hiding Faces retains enabled edges. Hiding Geometry hides
both while remembering the separate Face/Edge choices. Hidden CAD entities cannot
be picked in the viewport; the geometry tree still allows their selection.
Visibility toggles do not clear the existing selection.

All samples mirrors Show sample points in Sampling. Selected region samples
mirrors the checkbox of the currently selected generated Sampled Areas label.
Both controls preserve the coordinates and export data.

Select a generated label and toggle Isolate region to filter the viewport to that
label's sample points. Isolation initially hides geometry; Geometry in isolation
optionally shows the region's faces and their boundary edges (or its sampled
edges). For domain-interior samples, the geometry context is the whole domain,
because interior generation currently targets all volumes. Global visibility
switches and region checkboxes still apply during isolation. Selecting another
generated label changes the isolation target. Turning isolation off restores the
normal visibility choices without changing them. Clearing datasets or replacing
the geometry exits isolation. These controls affect only display: geometry,
selection, sampling settings and generated coordinates are retained, and export
includes hidden and non-isolated datasets.


## Sampling performance and CPU modes

Sampling remains isolated from Qt in a cancellable background process. It now
starts in **Multicore** mode in the GUI, with up to four CPU cores by default
(capped by the process's available CPUs). The CPU cores control sets the maximum
Gmsh meshing thread count. Curves/surfaces can mesh in parallel across entities;
3D interiors use Gmsh's parallel HXT tetrahedral mesher. Some geometry queries,
trimming/grid clipping and candidate-generation steps remain serial. This is
multithreaded native meshing inside one background process, not several GUI
processes. Source meshing options that affect geometry sizing remain in effect.

**Reproducible** mode uses one meshing thread, the source geometry's original
mesher, and the existing fixed meshing seed. It preserves the previous sampling
pipeline's coordinates on the tested fixtures. The Qt-independent API defaults
to this mode when `compute_mode` is omitted. Multicore meshing can produce a
different numerical mesh and coordinates across runs or core counts, even with
the same sampling seed. Gmsh's experimental reproducible-mesh option is enabled
in Multicore mode, but exact repeatability is not promised. Export metadata
records `compute_mode`, `cpu_threads`, `mesh_algorithm_2d`, and
`mesh_algorithm_3d`, in addition to the seed and resolution. For 2D/edge sampling,
the recorded 3D algorithm is a model setting, not an algorithm used by that run.

Volume connectivity preparation now uses NumPy lookup arrays instead of Python
loops over every tetrahedron. SciPy's sequence generators are loaded only for
LHS, Sobol or Halton, reducing fresh-worker startup for the other methods. No GPU
sampling or Numba dependency is introduced; the profiled bottleneck was CPU
geometry meshing. The NVIDIA GPU continues rendering the viewport.

Measured on the development machine, for 1,000 Uniform random sphere samples:

| Case | Earlier pipeline | Current Multicore, 4 cores |
| --- | --- | --- |
| One face, Standard | 1.12 s fresh worker | 0.30 s fresh worker |
| Interior, Standard | 7.54 s calculation in the initial test | 1.01 s fresh worker |
| Interior, Fine | 68.07 s profiled calculation (64.19 s meshing) | 3.74 s fresh worker |

The timing columns use different instrumentation for the initial investigation;
they describe observed latency, not a controlled scaling experiment. A direct
fresh-worker comparison of the final modes gave 7.30 s Reproducible versus 1.01 s
Multicore for Standard interior sampling. Speeds depend on source sizing,
geometry, core count and method. Rendering/result display time is excluded.
Reproduce the complete fresh-worker comparison with:

```sh
uv run python tools/benchmark_sampling.py \
  examples/geometries/cfdbench/sphere_unstructured.geo --mode both --cores 4
```

Use `--face 6` for boundary sampling or `--resolution 64` for Fine interior.
The benchmark includes imports, meshing, sampling and JSON result transfer.
Validation covers multicore HXT interior membership and methods, serial fixture
reproducibility, unchanged saved baseline coordinates/tags, and GUI generation,
cancellation, visibility and export with the default Multicore mode.

### Undo, redo and dataset editing

The Edit menu provides **Undo** (`Ctrl+Z`) and **Redo** (`Ctrl+Shift+Z` or
`Ctrl+Y`). Session history retains the last 30 edits: sampling generation and
regeneration (including cancelled/failed recipe entries), dataset renaming and
colour changes, duplication, deletion, clearing datasets, saved boundary-region
edits, and transform-setup changes when closing the parametrisation workspace.
Undo restores affected dataset visibility, selection/isolation and parameter
bindings. New edits after undo discard the redo branch. Saving keeps history;
undoing back to saved content clears the unsaved-change state.

Right-click a sampled dataset and choose **Duplicate dataset** to create a
uniquely named copy, or **Delete dataset** to remove just that entry. Duplication
shares immutable coordinate/tag/normal storage while keeping metadata separate;
it does not resample the CAD. Delete and Clear generated datasets are undoable.
Deleting the last region bound by a translation setup clears that setup; undo
restores it together with its datasets.

History is local to the open document and is not stored in `.jorvik` archives.
Opening geometry or a project starts fresh history. Undo/redo is unavailable
during import, generation, project I/O and region/translation dialogs. Camera
navigation, ordinary visibility changes, standalone sampling-control edits and
unsaved dialog fields are not separate history entries. Numeric point buffers
are shared between history snapshots, but retaining older generated datasets can
still use memory until their history entries expire or the document is replaced.

## Face and sampled-area context menus

Right-click a face in the geometry tree to hide the selected faces, isolate them,
or show all faces again. Right-clicking an unselected face selects it; right-clicking
one of several selected faces keeps the group. These controls affect shaded faces;
edges retain their separate visibility setting. Picking uses the nearest visible face at the cursor. Edge picking uses a fixed
six-pixel radius at every zoom level and tests occlusion at the candidate edge
point; an edge behind a visible face cannot be selected through it. The nearest
edge in screen distance wins, with camera depth breaking ties. Geometry outside
the camera clipping range is ineligible. Hidden faces cannot be picked in
3D, but remain available in the tree. Show all faces also restores the Geometry and
Faces switches and exits sampled-region isolation. Importing geometry clears face masks.

Right-click a Sampled Areas label (or its details) to rename it, change its colour,
show/hide its points, isolate it, or show all regions. Colour swatches match the point
clouds; defaults use warm white, cyan, lime, lavender and white. Changes take effect
immediately. Renaming requires a non-empty, unique label and is disabled during
generation. Hidden/isolation state and coordinates survive renaming. Exports use
the new labels and include each region's colour in its metadata. Canceling either
dialog leaves the region unchanged. These display choices are included when saving a project.

### User-defined boundary regions

Open geometry, select faces or edges, then click **Boundary regions…** below the
geometry tree. Use **New from current selection**, enter a non-empty region
label, choose a colour, check the desired entities and click **Save region**.
A region contains multiple faces or multiple edges; mixing dimensions in one
region is rejected. Labels support spaces, Unicode and slashes. Region labels
are unique among boundary definitions and are independent of dataset labels.
Selecting an existing region lets you rename it, change membership/colour,
select its saved entities in the viewport, or delete its assignment. Closing the
editor discards unsaved form edits; saved edits take effect immediately.

Regions are metadata on prepared CAD, not geometry partitions or physics values.
An entity can belong to several regions. Each region samples independently, so
overlapping regions may yield overlapping point clouds; no deduplication or
single-owner rule is implied. Shared entities display the first-created region's
colour, with hover/selection highlights taking precedence. Unlabelled entities
remain selectable and can be sampled through the normal selection target.

Choose **Region: <label>** in Sampling to generate from its current members.
Each generated dataset stores an immutable `boundary_region` snapshot with the
region's identifier, label, colour, members, document, source format and source
hash. Renaming/deleting a definition does not rename a dataset or change its
existing samples/provenance. Editing a sampled area's recipe manually makes it
an explicit selection target instead of silently retaining an obsolete binding.
NetCDF and NPZ metadata preserve the snapshot.

Full projects preserve region definitions and the chosen region sampling target,
including when the original CAD file has moved. Hash checks reject changed source
files before sampling or project saving; loaded assignments must reference the
saved document and valid entities. A successful new geometry import clears region
assignments rather than guessing that numeric tags still identify the same CAD.
Failed imports preserve the current assignments and datasets. Legacy projects
without boundary definitions open with an empty list.

### Parametrisation: transform configuration

Generate boundary/interior samples, then click **Parametrisation…** below
Sampled Areas. The Parameters tab supports one configuration with translation,
axis-angle rotation, positive uniform/non-uniform scaling and a shared pivot.
Use equal X/Y/Z scale factors for uniform scaling. Axis vectors are normalized;
the UI takes angles in degrees, while saved setup files also support radians.
Scale factors must lie between 1e-8 and 1e8; zero, negative and non-finite values
are rejected. The UI rejects setup values outside its range or precision before
changing any controls.

The order is fixed and recorded: **scale → rotate → translate**, using world
coordinates. Both scale and rotation use the supplied pivot in source geometry
units; scale factors are dimensionless. A point is mapped as
`p' = pivot + R × S × (p - pivot) + translation`. Every preview and export starts
from original coordinates. This maps the prepared geometry and point sets; it
is not CAD dimensional regeneration or a resampling operation.

**Whole domain** maps the geometry display and every sampled cloud together.
**Selected regions** maps only checked point clouds and leaves CAD fixed; its
boundary/interior consistency is explicitly unverified. Labels, original CAD,
sampling recipes, point counts, entity tags, region assignments, colours and
visibility are retained. Applicable existing surface normals follow the normalized inverse transpose of
the linear map. Positive scaling preserves orientation; translation leaves
normals unchanged. Planar boundary co-normals support rotation and uniform scale.
After non-uniform scaling their numeric arrays are omitted and marked unavailable:
correct transport also needs the original plane frame, which these datasets do
not retain. Points still transform/export, with this limitation in their metadata.
No normals are invented for open shells or datasets whose normals were unavailable.

Scaling changes point density per unit length/area/volume, and non-uniform
scaling can change relative surface density. Exports contain transformed points,
not Jacobian/integration weights, so downstream integrations need their own
measure treatment. No point count adjustment or automatic resampling is applied.

Parameter edits only mark the preview stale. Click **Preview configuration**
explicitly to update, or process/export directly. Restore, Close and Escape
remove the preview without accumulating transforms; the original datasets remain
intact. Opening geometry or editing original data is disabled while the workspace
is open. Hidden/unaffected regions are included in exports.

Save/load a lightweight JSON setup containing scope, source geometry hash,
region labels and transform values. New setups use parametrisation schema **2**
with axis, angle/units, scale, pivot, order and frame. Legacy schema **1**
translation setups still load with identity rotation/scaling. These schema
versions are independent of the ordinary dataset and project archive schemas.
Projects retain the complete setup, and session undo/redo can restore setup edits.
Loading rejects changed geometry, missing labels or invalid transforms atomically.
**Reset setup** restores the original view and identity values: zero translation
and angle, Z rotation axis, unit scale, zero pivot, whole domain and no bindings.
Restore original view removes only the preview and retains configured values.

Each NetCDF/NPZ export retains all datasets with their independent point counts,
source/sampling metadata and transform provenance. `_gs_translation` and its
`_gs_parameter` labels remain available. Schema 2 also writes
`_gs_transform_matrix` as a 4×4 row/column affine matrix, `_gs_scale`, `_gs_pivot`,
`_gs_rotation_axis` and `_gs_rotation_angle`. The global JSON manifest retains
the full setup and configuration count; each dataset records whether it was
affected, the transform matrix/order/frame and the density limitation. NPZ uses
`allow_pickle=False`; writes preserve existing destinations on failure. The
NetCDF backend remains NetCDF3.

#### Parameter designs and selected configurations

In **Combinations**, enable **Use a parameter design** and add named parameters.
Each name binds to exactly one translation component, rotation angle, individual
scale component or uniform scale. Uniform scale sets all three scale factors and
cannot overlap individual scale bindings. Values assign the target absolutely;
the reference value is descriptive metadata, not an offset or normalization.
Rotation uses the base angle units, distances use the unspecified source units,
and scales are dimensionless. No physical unit conversion is inferred.

For **Cartesian product**, each parameter supplies an inclusive range/value count
or an explicit comma-separated list; the first parameter changes slowest. For
**Paired values**, all lists/counts must have the same length and values are paired
by position. One-point ranges use their start value. **Seeded random**, **LHS** and
**Sobol** use parameter ranges plus a separate total configuration count and seed.
Those seeds/designs are independent of spatial sampling methods and seeds. Sobol
balance is strongest at power-of-two sizes; other sizes truncate a larger balanced
sequence. Exact numeric sequences can change with scientific-library versions;
the exported actual parameter values are the record of what was applied.

The summary shows configurations, points per region/configuration, total points
and an estimated uncompressed numeric payload before generation. Estimates cover
coordinates/tags/available normals; metadata/container overhead and compression
are excluded. At most 10,000 configurations are allowed, and oversized Cartesian
products are rejected before allocating the grid. Scalar configurations are
paged in sets of 200 rows. Literal, case-insensitive search matches configuration
IDs, parameter names and numeric values, retaining the original IDs and row order.
No matches disables selected-row actions. Clearing search restores the full table;
search does not change the design or its generated values. Generating this table
does not create transformed point clouds or regenerate spatial samples.

Click **Generate configurations**, select a row, then **Preview selected
configuration** or **Export selected configuration**. The Processing/Export tabs
also use the selected row when the design is enabled. Selection alone never
changes the viewport. Edits to base transforms or design definitions invalidate
the table and require explicit regeneration; the old viewport preview stays
visible until Preview/Restore. Invalid configurations fail generation explicitly
with their configuration ID; there is no silent filtering or replacement.

Exports still contain **one** selected configuration and every original dataset
with its independent point count. `_gs_design_parameter`, `_gs_design_values`
and `_gs_design_units` record the named values once as aligned arrays. Metadata
retains stable IDs (`configuration_000001`, ...), algorithm, seed, target bindings,
units and actual values. The complete design definition is retained once in the
global manifest; long explicit lists are not copied into every generated row or
region. Setups/projects retain nested design schema **1** inside transform schema
**2**. Reopening restores definitions but requires Generate configurations again;
the generated table and preview are not saved. Invalid unfinished design drafts
are discarded on close with a status message, retaining the last valid setup and
unlocking the workspace.

#### Background sweep export

After generating a current design in Combinations, open **Processing** and choose
**Export generated design…**. It exports **all generated configurations**, even
if the table search shows only a few matches. This is independent of the selected
row. Choose NetCDF (`.nc`, the default) or compressed NumPy (`.npz`) files and
**Stop at the first failed configuration** or **Continue and record failures**.
Select a new output-folder name; existing files and folders are never reused.

A single background worker reads the original datasets and transforms/writes one
configuration at a time. The viewport and original samples stay unchanged; no
full collection of transformed clouds is held in RAM. Parameter/design-definition
editing and other exports are disabled during work. Progress shows attempted,
written and failed counts plus elapsed time. **Cancel export** finishes the current
atomic file, then stops before the next configuration. Closing the workspace or
application requests the same cancellation and waits before destroying the worker.
Completed outputs and partial-run records are retained.

The new folder contains:

- `manifest.json`: sweep schema **1**, source hash, original dataset recipes,
  full base/design definition, format, counters, elapsed time and final status.
- `configurations.json`: scalar configuration IDs, actual named values, transform
  setups and expected filenames; written once before data processing.
- `results.jsonl`: one result per attempted configuration, appended only after its
  export completes or fails. Successful entries name their output file; failed
  entries record an error and have no successful filename.
- `configuration_000001.nc` or `.npz`, etc.: independently loadable configuration
  files with original label maps, unequal region point counts, entity tags,
  applicable normals, actual parameter values and transform provenance.

`complete` with `all_outputs_complete: true` means every shard succeeded.
`complete_with_errors` means all configurations were attempted but some failed;
`failed` stops on an error; `cancelled` leaves remaining configurations pending.
An interrupted process may leave `running`, a missing manifest or an incomplete
last journal line; none indicates a complete run. Resume/retry is not implemented:
choose a new folder for another attempt. Each shard and each manifest checkpoint
is written atomically. Original recipes, region seeds, methods and point counts
are retained; affine mapping does not regenerate CAD or improve spatial accuracy.

For example, read only successful NetCDF configurations (NumPy shards use the
same journal and carry JSON metadata in `_gs_metadata`):

```python
import json
from pathlib import Path
import xarray as xr

run = Path("parameter_sweep")
for line in (run / "results.jsonl").read_text().splitlines():
    result = json.loads(line)
    if result["status"] != "complete":
        continue
    with xr.open_dataset(run / result["filename"], engine="scipy") as dataset:
        regions = json.loads(dataset.attrs["metadata_json"])["datasets"]
        parameters = dataset["_gs_design_values"].values
        for region in regions:
            coordinates = dataset[region["array"]].values
            # Use region['label'] and its independent N×3 coordinates.
```

#### Reading sweeps for training

`jorvik.sweep_results.inspect_sweep(path)` checks the run manifest, scalar plan,
result counters and completed-file presence without loading point arrays.
`iter_sweep_datasets(path)` then yields one completed configuration at a time,
validating each shard's source/setup/parameter provenance, shapes, finite values,
entity tags, label/count mapping and applicable unit normals. These are structural
checks; sweep files do not currently carry cryptographic content checksums.
Both APIs reject incomplete/error runs by default. Pass `allow_partial=True`
explicitly to use successful files from cancelled, failed or interrupted runs.
Only an interrupted `running` journal's malformed final unterminated line can
be ignored; malformed earlier records remain errors. No resume is performed.

```python
from jorvik.sweep_results import iter_sweep_datasets, training_arrays

for configuration in iter_sweep_datasets("parameter_sweep"):
    columns = ["x", "y", "z", *configuration["parameters"]]
    arrays = training_arrays(configuration)
    for label, inputs in arrays.items():
        # Each region has its own N×(3+P) array, with columns ordered as above.
        pass
    # Normals, entity tags and sampling metadata remain in configuration['datasets'].
```

Parameter columns are expanded only by `training_arrays`, leaving persisted
parameter values stored once per configuration. Boundary/interior datasets are
not merged, padded or assigned physical conditions. Readers close each file
before yielding independent arrays. Consume the iterator incrementally to bound
memory; converting it to a list intentionally retains all loaded configurations.

From a checkout, inspect a run without modifying it:

```sh
uv run python tools/inspect_sweep.py parameter_sweep --verify
# Explicit partial-output inspection:
uv run python tools/inspect_sweep.py parameter_sweep --allow-partial --verify
```

Size estimates concern numeric payloads, not peak Python/Qt memory or compressed
file size. Memory still scales with the original datasets and one transformed
configuration. Multiple concurrent workers, within-file cancellation, automatic
resume/retry, constraints, NetCDF4 grouped output, Jacobian weights and CAD
regeneration remain later work.

### Neutral CAD import

New imports and generated datasets record `source_hash`, `source_format` and
`import_options`: the Gmsh version, disabled user configuration loading, and
the loader used. STEP/BREP/IGES also record the explicit format and
`highest_dim_only=false` argument. These describe Jorvik's loader settings;
they do not claim to capture exporter settings or every option inside `.geo`
scripts. Project archives and NetCDF/NPZ metadata retain these records. Older
projects can omit import options; unknown historical settings are not inferred.

Generation rejects changed source content or a changed importer version/settings
when the original import record is available. Reopen the geometry to import it
again: this clears prior regions and datasets, so numeric entity IDs cannot
silently acquire old assignments. Automatic entity remapping and optional
healing remain future work. Main-file hashes do not cover external `.geo`
Include/Merge dependencies.

File → Open Geometry and the command line accept STEP, Open CASCADE BREP and
IGES alongside `.geo`. `cad_import.py` is the shared Qt-independent loader used
by both display and sampling workers. Neutral formats use Gmsh's OCC import and
synchronisation; scripts retain their original kernel. Available CAD entity names
appear in geometry-tree tooltips. Names are not automatically interpreted as BCs
or Gmsh physical groups, and face naming fidelity depends on the source exporter.

The backend model exposes `topology` separately from the face/edge display
`entities`. Each original vertex (dimension 0), edge (1), face (2) and solid (3)
has a document-scoped `(dimension, tag)` identity, CAD type/name, six coordinate
bounds, physical-group memberships, immediate `boundary` references and `upward`
adjacency references. Both adjacency lists contain `[dimension, tag]` pairs;
orientation is not encoded and must not be used to infer outward normals.
Construction vertices and degenerate edges remain in this model even if they
have no visible primitives. Topology contains no display triangles and is not an
editable or standalone exact-geometry representation; geometric evaluation still
uses the source CAD through Gmsh. Numeric tags are not stable across reimports.

Both built-in and Open CASCADE `.geo` scripts retain their kernels. Regression
checks construct the same cube in each kernel and verify original entity counts,
adjacency, physical groups and edge/face/interior sampling. STEP/BREP/IGES fixtures
also expose this topology. Projects preserve it and reject inconsistent entity
references, adjacency or bounds before replacing the workspace. Older projects
without topology remain readable; reopen the source to populate the new model.

Interior domains are explicitly classified as `solid`, `planar` or
`open_or_nonplanar`. A `.geo` file without volumes qualifies for 2D interior
sampling only when its faces are recognized analytic planes lying in one plane.
An open sphere, a non-planar shell or disconnected planes at different heights
cannot silently become a 2D interior dataset. Unsupported planar NURBS domains
are conservatively rejected until their planarity can be verified. Boundary
sampling remains available. Declared solid meshing failures identify open
boundaries/invalid topology instead of replacing existing sampled datasets.

Files containing faces but no solid volumes can be displayed and sampled on faces
and edges. Neutral-format interior sampling requires imported solid volumes and
reports a clear error for surface-only files. No automatic sewing is introduced
in this importer. This deliberately does not treat a set of cube-shaped surfaces
as a solid merely because it looks closed. Source units remain unspecified in
exports; unit detection/conversion, comprehensive assembly metadata and optional
healing remain roadmap work. Display topology retains zero-length CAD edges with
an explanatory tooltip, omits their rendering and rejects sampling them.

Try the downloaded fixtures under `examples/geometries/cad/`: `t20_data.step`
(a solid supporting interior sampling), `cylinder.brep` (curved solid),
`cube_filet.stp` (filleted surfaces with degenerate edges), and `cube1000.igs`
(surface-only cube). README, provenance.json and LICENSE.txt record pinned
upstream sources, checksums and licensing. They are not converted `.geo` files.

STEP/BREP/IGES support is validated on these small fixtures; industrial CAD,
assemblies, additional STEP application protocols, units, healing and stable
cross-import region identifiers still require broader testing. No STL, Parasolid,
ACIS or proprietary native CAD importer is added.

### Save and reopen projects

Launch directly into a project document:

```sh
jorvik path/to/model.jorvik
# From a source checkout:
uv run jorvik path/to/model.jorvik
```

The positional argument also continues to accept supported geometry files
(`.geo`, `.step`/`.stp`, `.brep`, `.iges`/`.igs`). Missing, unsupported or corrupt
launch files produce a clear error on stderr and a nonzero exit status; they are
never replaced as a side effect of opening.

New project documents use `.jorvik`. Existing `.gsproj` files remain supported by
the creation API, command line, Open project dialog and recovery workflow. The
archive format and schema are unchanged: only the preferred filename extension
has changed. Saving an opened legacy file keeps its `.gsproj` filename; Save As
suggests `.jorvik` and leaves the old file intact. No files are automatically
renamed. External applications should create new documents with `.jorvik`.

A `.jorvik` can be empty. File → Save project saves the normal empty workspace;
opening an empty document leaves it ready for geometry import. Import geometry,
then Save to update that same document without choosing a filename again. The
import counts as an unsaved edit, including when closing immediately afterward.

Other Python applications can create a document without importing Qt or launching
the interface:

```python
from jorvik.project import create_empty_project

project_path = create_empty_project("model.jorvik")
# Then launch: jorvik model.jorvik
```

`create_empty_project(path, *, overwrite=False)` returns a `pathlib.Path` and
accepts `.jorvik` (preferred) or legacy `.gsproj` and requires an existing parent
directory. It atomically creates a complete archive, raising `FileExistsError` if the destination already
exists. Pass `overwrite=True` only when replacement is intended. Empty documents
use project schema **3**, with `geometry: null`, no datasets, no source and empty
state; they cannot contain geometry references or embedded source data. Their
loader returns `(manifest, None)`. Populated projects keep schema **2**, and
existing schema **1/2** projects retain their normal validation. Empty workspace
view preferences are supplied by the application's settings rather than stored
in the document.


#### Saving and opening

Use **File → Save project** (`Ctrl+S`) or **Save project as…** (`Ctrl+Shift+S`)
to write a `.jorvik` archive. **Open project…** (`Ctrl+Shift+O`) restores it;
**Open Geometry** remains `Ctrl+O`. Dataset export is now `Ctrl+Alt+E`.
Saving/opening is unavailable during import, generation or an active parametrisation
workspace; close that workspace first to keep transient previews out of projects.

#### Unsaved changes

Changes to datasets, boundary assignments, sampling settings and parametrisation
setups prompt **Save / Discard / Cancel** before replacing the workspace or
closing. Cancel leaves the current workspace open. Save must complete successfully
before replacement proceeds; cancelling Save As, changed CAD, invalid data or a
failed disk write cancels the original replacement/close. Failed imports are
validated before a replacement prompt, retaining the current workspace. Ordinary
camera/selection navigation does not make a project dirty. Dataset visibility and
colour changes do count as content changes.

#### Background file operations

Archive reads/writes, validation and content fingerprints use one background
worker while Qt keeps processing events. Editing and replacement controls are
locked during the operation; the viewport remains interactive and an indeterminate
progress indicator shows file work. Worker errors restore the controls and retain
existing destinations. Viewport serialisation/restoration can still be expensive
for large clouds and has not been certified responsive at one million points.

#### Recovery copies

Every two minutes, an idle dirty workspace writes an atomic recovery copy in the
application data `recovery` directory (beside settings for explicit INI profiles).
Unchanged copies are skipped. Autosave never changes the normal project path,
marks the workspace saved, regenerates geometry, or overwrites the user's project.
Source/hash failures retain the previous recovery copy and show a status message.
Active import/generation, editors and project jobs postpone recovery writes.

#### Recover an interrupted session

On startup, abandoned copies offer Recover, Discard or Cancel. Lock files prevent
a live window's copy being offered by another window. Recovery loads validated
cached geometry/data, keeps the workspace dirty and requires **Save As** to choose
a destination; it never silently overwrites an original project. A recovered copy
is transferred to the new window before the old copy is deleted. Successful manual
saving or explicit discard/clean close removes that window's recovery copy;
cancelling close retains it. The two-minute interval means very recent edits can
still be lost on interruption. External `.geo` dependencies remain outside the
embedded main-source recovery guarantee.

#### Measured archive performance

Persistence measurements on the validation host (fresh process per size):

| Points | Save | Load | Archive | Peak process RSS |
| --- | --- | --- | --- | --- |
| 100,000 | 0.112 s | 0.050 s | 3.19 MiB | 201.30 MiB |
| 1,000,000 | 1.099 s | 0.417 s | 31.70 MiB | 1123.52 MiB |

Run `uv run python tools/benchmark_projects.py --count 1000000` to repeat.
This synthetic analytic annulus benchmark verifies exact coordinates, entity tags
and normals. Peak RSS includes original and restored list-based clouds, scientific
libraries and verification arrays. It measures backend archive persistence, not
geometry generation or viewport rendering; these figures are not UI latency or
cross-machine guarantees. Reducing numeric/list copies remains open work.

#### What a project stores

Projects contain the main CAD source, cached display topology, original generated
coordinates and entity tags, generated/failed region records, sampling recipes,
colours, per-region/global visibility, individual face filters, CAD selection,
point appearance, camera, dock layout and the current translation setup. Geometry
and coordinate arrays use compressed numeric storage rather than large JSON lists
on disk, and load without pickle. Opening restores cached samples and display
without CAD execution, remeshing or regeneration. Writes use atomic replacement;
invalid versions, checksums or content are rejected before replacing the workspace.
Save explicitly after editing to update the project file; periodic recovery copies are separate from manual saves.

#### Moved or changed CAD sources

The project uses the original CAD path if it still matches the saved hash.
Otherwise it restores the embedded source into an application-managed temporary
folder for subsequent sampling, and keeps it alive until the project is replaced
or the application closes. Loading the project again does not require that folder
to survive. Source geometry changes are not silently applied to saved entity IDs.
The archive embeds only the main source: `.geo` files using external Include/Merge
files still need those dependencies for regeneration. Their saved display and
samples can be inspected/exported without the dependencies. Packaging and hashing
complete `.geo` dependency graphs remains future work.

### Edit a sampled area

Right-click a Sampled Areas label or its details and choose **Edit…**. Change its
label, face/edge/interior target, constituent entities, sampling method, point/grid
budget, seed, accuracy or CPU settings, then click **Regenerate area**. Face/edge
lists support independent checkboxes; switching target types does not force edits
to the current viewport selection. Existing imported-group recipes can be edited
as an explicit set of their constituent faces or edges.

The replacement runs in the normal background worker. Existing points remain
visible/exportable in memory until successful completion. Success replaces one
area, preserving colour, visibility and region isolation, and updates any current
translation-setup label binding. Canceling the editor does nothing; generation
cancellation/failure retains the original recipe, label and samples. A failed or
canceled area with no existing points can also be edited and generated. Use the
existing Rename… and Change colour… actions for display-only changes without
regeneration. Saving/opening projects while a replacement is running is blocked.
