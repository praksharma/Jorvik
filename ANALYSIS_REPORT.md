# Jorvik Codebase & Architecture Analysis Report

## 1. Executive Summary

**Jorvik** (`v0.1.0.dev0`, licensed under `AGPL-3.0-only`) is a desktop graphical application and Qt-independent Python library for **geometry-aware spatial and parametric point sampling** targeting Physics-Informed Neural Networks (PINNs), neural operators, surrogate modelling, and scientific machine learning.

Rather than requiring researchers to write custom ad-hoc sampling scripts for curved CAD boundaries, internal holes, and parametric domain variations, Jorvik provides an interactive, reproducible workflow:
1. **Import prepared CAD geometry** (Gmsh `.geo`, STEP `.step`/`.stp`, Open CASCADE `.brep`, and IGES `.iges`/`.igs`).
2. **Inspect and select original topological entities** (faces and edges) in an offline Three.js 3D viewport synchronized with a hierarchical geometry tree.
3. **Define arbitrary boundary regions** as pure metadata (independent of dataset labels and free of solver-specific boundary-condition semantics).
4. **Generate interior and boundary point clouds** using seven sampling strategies with optional multicore Gmsh/HXT meshing, reproducibility seeds, and verified outward boundary normals / planar co-normals.
5. **Configure affine parametrisations and parameter-space sweeps** (translation, pivoted rotation, uniform/anisotropic scaling) across Cartesian, paired, random, LHS, and Sobol designs with literal comparison constraints.
6. **Persist and export** portable `.jorvik` project archives (full or lightweight recipe-only copies), reusable JSON sampling recipes, single-configuration datasets, and checksum-verified multi-configuration sweeps in **xarray / NetCDF (`.nc`)** or **NumPy (`.npz`)** formats.

---

## 2. Repository Metrics & Module Inventory

At commit `02c69ba`, the repository comprises **12,479 lines of Python** across source modules, automated tests, benchmarks, and build scripts, plus an offline JavaScript 3D viewer and a synchronized documentation site:

| Category | Path | Files | Lines of Code | Purpose |
| :--- | :--- | ---: | ---: | :--- |
| **Application & Core Library** | `src/jorvik/*.py` | 36 | 5,705 | Qt-independent CAD/sampling/sweep core + PySide6 desktop GUI |
| **Offline 3D Viewport** | `src/jorvik/viewport3d/` | 4 (+ vendor) | ~450 (+ Three.js r180) | WebGL Three.js scene, screen-space picking, gizmo, affine preview |
| **Automated Test Suite** | `tests/test_*.py` | 39 | 5,199 | Unit, numerical validation, provenance, and WebEngine GUI tests |
| **Documentation & Build Scripts** | `scripts/*.py` | 4 | 472 | `GUIDE.md` → Zensical page sync, screenshot capture, install checks |
| **Benchmarks & CLI Tools** | `tools/*.py` | 3 | 126 | Project I/O RSS benchmarks, fresh-worker sampling benchmarks, sweep CLI |
| **Runnable Examples** | `examples/*.py` | 1 (+ fixtures) | 56 | Reproducible CFDBench annulus sampling and NetCDF/NPZ round-trip |

### Architectural Layer Breakdown

#### A. Geometry, Topology & Region Metadata Layer (Qt-Independent)
* `src/jorvik/cad_import.py` (63 LOC): Shared format detection (`.geo`, `.step`, `.stp`, `.brep`, `.iges`, `.igs`), loader dispatch (`gmsh.open` for `.geo` scripts; `gmsh.model.occ.importShapes(..., highestDimOnly=False)` for neutral CAD), native OCC stdout redirection to stderr (`os.dup2(2, 1)`), and import provenance validation.
* `src/jorvik/geometry.py` (99 LOC): Isolated subprocess entry point (`python -m jorvik.geometry`) that extracts authoritative topology, physical groups, SHA-256 source hash, and 1st-order display tessellations while detecting degenerate zero-length OCC edges.
* `src/jorvik/topology.py` (63 LOC): Backend-neutral validation of entity identities `(dimension, tag)` across dimensions 0–3, bounding boxes, group memberships, and reciprocal upward/downward adjacency relations.
* `src/jorvik/domains.py` (36 LOC): Conservative domain classifier distinguishing `solid` (3D volumes present), `planar` (`.geo` models composed exclusively of coplanar analytic `Plane` faces), and `open_or_nonplanar` (rejected for interior sampling).
* `src/jorvik/regions.py` (65 LOC): Immutable user-defined boundary region management (`put_region`, `region_snapshot`, `validate_regions`) with SHA-256 source hash binding and overlapping region support.

#### B. Sampling Engine, Normals & Recipes Layer (Qt-Independent)
* `src/jorvik/sampling_methods.py` (6 LOC): Catalogue of the 7 supported sampling methods (`random`, `grid`, `lhs`, `sobol`, `halton`, `hammersley`, `stratified`).
* `src/jorvik/sampling.py` (354 LOC): Subprocess worker (`python -m jorvik.sampling`) implementing 1D curve arc-length inversion (`_edge_samples`), 2D parameter-space triangle Jacobian rejection and grid clipping (`_surface_samples`), 3D tetrahedral volume sampling (`_volume_samples`), and multicore Gmsh/HXT thread control.
* `src/jorvik/normals.py` (129 LOC): Geometry-evaluated outward surface normals (3D solids) and in-plane edge co-normals (2D planar domains) oriented via multi-scale containment probes ($10^{-6}$ to $10^{-2}$ of parent bounding box), plus per-edge parent plane frame recording (`normal_planes`).
* `src/jorvik/sampling_recipe.py` (183 LOC): Versioned settings-only JSON recipes (`jorvik_sampling_recipe`, schema v1) that record target entities/groups/regions, sampling method, count, seed, resolution, and compute settings without point arrays, supporting strict binding (`bind_recipe`) and extraction from project areas (`request_from_area`).

#### C. Parametrisation, Parameter Designs & Sweep Layer (Qt-Independent)
* `src/jorvik/parametrisation.py` (303 LOC): Schema v1 (translation) and v2 (affine `scale → rotate → translate` in world coordinates about an explicit pivot) engine. Evaluates coordinates from originals, transports surface normals via $\mathbf{A}^{-T}$, and reconstructs planar edge co-normals under anisotropic scaling using stored parent plane frames.
* `src/jorvik/parameter_design.py` (278 LOC): Bounded parameter-space design generator (up to 8 named parameters, 10,000 configurations, and 32 literal comparison constraints) across `cartesian`, `paired`, `random`, `lhs`, and `sobol` methods without allocating spatial point clouds.
* `src/jorvik/sweep_export.py` (306 LOC): Sequential, bounded-memory sweep exporter (`export_sweep`) and verified continuation engine (`resume_sweep`) producing atomic `.nc`/`.npz` configuration shards, `manifest.json`, `configurations.json`, `results.jsonl`, and `rejections.json`.
* `src/jorvik/sweep_results.py` (228 LOC): Read-only sweep validator (`inspect_sweep`), streaming shard iterator (`iter_sweep_datasets`), and PINN training array builder (`training_arrays` returning $N \times (3 + P)$ arrays).
* `src/jorvik/sweep_integrity.py` (28 LOC): Chunked (1 MiB) SHA-256 and byte-size verifier for sweep plans, rejection reports, and output shards.
* `src/jorvik/sweep_inspection_worker.py` (59 LOC): Subprocess worker streaming line-delimited JSON verification events to the GUI without transferring point clouds over IPC.

#### D. Dataset Export & Project Persistence Layer (Qt-Independent)
* `src/jorvik/export.py` (123 LOC): Schema v2 `xarray.Dataset` builder (`to_xarray`) and atomic exporter (`export_samples`, `atomic_write`) writing NetCDF (`NETCDF3_64BIT` via SciPy) or compressed NumPy `.npz` archives (`allow_pickle=False`), mapping non-identifier labels safely to `_gs_region_<i>` variables while preserving original labels in metadata.
* `src/jorvik/project.py` (283 LOC): Portable `.jorvik` (and legacy `.gsproj`) ZIP archive persistence (`save_project`, `load_project`, `create_empty_project`, `without_samples`) supporting schema v1–v3, embedded CAD files, pickle-free `.npy` numeric buffers, full UI state validation prior to workspace replacement, and lightweight recipe-only copies (`samples_omitted=True`).

#### E. Desktop GUI, Offline Viewport & Session Management Layer (PySide6)
* `src/jorvik/app.py` (846 LOC): Main window shell, menus, grouped icon toolbar, dockable panes (`Geometry and Sampled Areas`, `Properties`, `Sampling`), face/edge selection synchronization, visibility/isolation controls, and CLI startup.
* `src/jorvik/viewport.py` (144 LOC) & `src/jorvik/viewport3d/viewer.js` (274 LOC): Offline `QWebEngineView` and `QWebChannel` bridge rendering shaded CAD faces, thick screen-space edge highlights (`LineSegments2`), shared-texture point clouds, reversible matrix previews, and occlusion-aware screen-space picking.
* `src/jorvik/project_ui.py` (283 LOC) & `src/jorvik/project_worker.py` (18 LOC): Background thread wrapper (`QThread` + local `QEventLoop` with UI control locking) for responsive project archive I/O and SHA-256 workspace dirty-state hashing.
* `src/jorvik/history.py` (150 LOC): 30-step session Undo/Redo system sharing immutable coordinate/tag/normal lists across snapshots via custom `deepcopy` memoization (`copy_records`).
* `src/jorvik/recovery.py` (90 LOC) & `src/jorvik/shutdown.py` (47 LOC): Per-window 2-minute atomic autosave recovery with `QLockFile` ownership and a 100 ms `SIGINT`/`SIGTERM` poller routing terminal interrupts through Qt's close and unsaved-changes workflow.
* `src/jorvik/graphics.py` (63 LOC), `src/jorvik/window_controls.py` (185 LOC), `src/jorvik/theme.py` (76 LOC), `src/jorvik/icons.py` (35 LOC), `src/jorvik/cli.py` (16 LOC): Automatic Linux Wayland NVIDIA Vulkan/EGL configuration, client-side window title bar for frameless Wayland Vulkan surfaces, light/dark Fusion themes, dynamic SVG toolbar icons, and CLI argument validation.
* Specialized UI Panels & Dialogs: `sampling_panel.py` (341 LOC), `sampled_areas.py` (122 LOC), `regions_panel.py` (126 LOC), `area_editor.py` (67 LOC), `recipe_ui.py` (93 LOC), `parametrisation_panel.py` (296 LOC), `parameter_design_panel.py` (409 LOC), `constraint_panel.py` (114 LOC), `sweep_panel.py` (247 LOC), `sweep_inspector.py` (188 LOC).

---

## 3. Deep-Dive Architectural Analysis

### 3.1 Strict Layer Separation & Process Isolation
A defining architectural strength of Jorvik is that **every core domain operation is independent of Qt and the WebEngine rendering layer**:
* **Subprocess Isolation for Gmsh (`geometry.py`, `sampling.py`)**: The Gmsh C++ library maintains process-wide singleton state and can emit diagnostics directly to file descriptor 1 (`stdout`). Jorvik runs geometry loading and point generation in dedicated child processes (`sys.executable -m jorvik.geometry` and `jorvik.sampling`), redirecting OS-level stdout to stderr during Open CASCADE shape imports so the parent Qt process receives clean, deterministic JSON (`allow_nan=False`) and can cancel long runs immediately via `QProcess.kill()`.
* **Subprocess Isolation for Sweep Verification (`sweep_inspection_worker.py`)**: Inspecting and verifying multi-shard sweep folders runs in a child process that validates each NetCDF/NPZ shard and streams lightweight JSON status lines (`metadata`, `verified`, `done`) without sending point arrays across IPC.
* **Synchronous Widget Locking for Thread Workers (`project_ui.py`, `sweep_panel.py`)**: When saving/loading projects (`ProjectWorker`) or exporting multi-configuration sweeps (`SweepWorker`), Jorvik disables mutating UI controls before starting the `QThread`, allowing the worker to borrow in-memory coordinate buffers safely without copying gigabytes of data on the GUI thread.

### 3.2 Geometry Representation & Sampling Mathematics
* **Authoritative Geometry vs. Display Tessellation**:
  * Display meshes (`geometry['entities']`) use coarse 1st-order triangles and line segments solely for Three.js visualization and picking.
  * Sampling (`sampling.py`) re-opens the verified CAD file (checking its SHA-256 `source_hash` and `import_options`) and evaluates points directly on the CAD curves/surfaces or on an independent sampling mesh controlled by `resolution` $\in \{16, 32, 64\}$.
* **1D Curve Sampling (`_edge_samples`)**:
  * Evaluates 8,193 uniformly spaced parameter values $t \in [t_{\min}, t_{\max}]$ per edge to build a cumulative chordal arc-length table, allocates sample draws across edges proportional to arc length, inverts the arc-length CDF via `np.interp`, and evaluates exact 3D points on the curve with `gmsh.model.getValue(1, tag, t)`.
* **2D Surface Sampling (`_surface_samples`)**:
  * Meshes faces in $(u, v)$ parameter space and computes each parameter triangle's area and surface Jacobian $\| \partial_u \mathbf{x} \times \partial_v \mathbf{x} \|$ at the centroid and three interior corner probes ($0.98 v_i + 0.02 \bar{v}$).
  * Uses $2\times$ the maximum probed Jacobian as an upper envelope for measure-weighted rejection sampling in 4D unit space (1 dimension for triangle selection, 2 for uniform triangle barycentric coordinates via $\sqrt{u_1}$, 1 for Jacobian acceptance). If any candidate point exceeds the envelope by more than $10^{-8}$, generation aborts explicitly rather than returning a silently biased distribution.
* **3D Volume Sampling (`_volume_samples`)**:
  * Extracts 4-node tetrahedra using vectorized NumPy `argsort`/`searchsorted` node lookups, weights tetrahedra by exact determinant volume $|\det[\mathbf{v}_1-\mathbf{v}_0, \mathbf{v}_2-\mathbf{v}_0, \mathbf{v}_3-\mathbf{v}_0]| / 6$, and maps 3D unit draws $(u_1, u_2, u_3)$ uniformly into each tetrahedron via $r = u_1^{1/3}, s = u_2^{1/2}, t = u_3$.

### 3.3 Outward Normals & Anisotropic Co-Normal Transport
* **Verification by Containment Probes (`normals.py`)**:
  * Rather than trusting raw surface parameter orientation (which may point inward depending on CAD face orientation), Jorvik requires a unique upward parent entity (`len(upward) == 1`) and tests $\mathbf{x} \pm \epsilon L \mathbf{n}$ across scales $\epsilon \in \{10^{-6}, 10^{-5}, 10^{-4}, 10^{-3}, 10^{-2}\}$ using `gmsh.model.isInside` (for OCC solids/planes) or tetrahedral element lookup (for built-in Gmsh volumes). If `inside(plus) == inside(minus)` at all scales for any point, normals are marked `unavailable` rather than returning partial or guessed vectors.
* **Co-Normals under Anisotropic Scaling (`parametrisation.py`)**:
  * While codimension-1 surface normals in $\mathbb{R}^3$ transform via the ambient inverse transpose $\mathbf{n}' \propto \mathbf{A}^{-T}\mathbf{n}$, a 1D boundary edge of a 2D planar domain embedded in $\mathbb{R}^3$ has an in-plane outward co-normal that is not codimension-1 in $\mathbb{R}^3$.
  * Jorvik stores the validated unit normal $\mathbf{p}$ of each edge's parent plane in `normal_planes`. Under an arbitrary affine map $\mathbf{x}' = \mathbf{A}\mathbf{x} + \mathbf{b}$, it transports the plane normal via $\mathbf{p}' = \frac{\mathbf{A}^{-T}\mathbf{p}}{\|\mathbf{A}^{-T}\mathbf{p}\|}$ and the in-plane edge tangent via $\mathbf{t}' = \mathbf{A}(\mathbf{p} \times \mathbf{n})$, recovering the exact transformed outward co-normal as $\frac{\mathbf{t}' \times \mathbf{p}'}{\|\mathbf{t}' \times \mathbf{p}'\|}$.

### 3.4 Viewport Picking & Reversible Preview
* **Occlusion-Aware Screen-Space Edge Picking (`viewer.js`)**:
  * WebGL native line widths are capped at 1 px on most drivers. Jorvik renders hover/selection highlights using screen-space `LineSegments2` ribbons (4 px hover, 6 px selected) and performs custom CPU screen-space segment picking (`pickAt`): each visible edge segment is clipped to the camera near/far depth slab in view space, projected to pixel coordinates, tested against a 6-pixel radius, interpolated with perspective correction $u = \frac{t / (-z_b)}{(1-t)/(-z_a) + t/(-z_b)}$ to recover its true 3D world distance, and checked against the frontmost visible face intersection so occluded back-Edges cannot be accidentally picked through solid geometry.
* **Zero-Drift Reversible Previews**:
  * `applyTranslationPreview()` never mutates the `Float32BufferAttribute` vertex arrays of CAD objects or sample point clouds. Instead, it applies the backend's authoritative $4 \times 4$ column-major affine matrix directly to Three.js object matrices (`object.matrixAutoUpdate = false`) and updates the camera frustum by the relative delta matrix $\mathbf{M}_{\text{new}} \mathbf{M}_{\text{old}}^{-1}$.

---

## 4. Implemented Capabilities vs. Planned Roadmap

In accordance with project guidelines, the table below distinguishes **implemented and tested features** from **planned future work** tracked in `TODO.md`:

| Area | Implemented & Verified (`v0.1.0.dev0`) | Planned / Future Work (`TODO.md`) |
| :--- | :--- | :--- |
| **CAD Import & Formats** | Gmsh `.geo` (built-in & OCC kernels), STEP (`.step`, `.stp`), OCC BREP (`.brep`), IGES (`.iges`, `.igs`); degenerate OCC edge handling; 2D interior sampling on validated coplanar planar STEP/BREP sheets (including rotated planes and inner holes); SHA-256 source & import-option provenance. | CAD source unit/scale detection; multi-body & assembly placement handling; optional sewing/healing with explicit entity remapping; Parasolid/ACIS/Rhino evaluation. |
| **Topology & Regions** | Full 0D–3D topology extraction & reciprocal adjacency validation; face/edge selection modes; hide/isolate faces; user-defined overlapping boundary regions with colours. | Importing native CAD entity names, colours, and assembly/body names separately from Gmsh physical groups; interoperability matrix across commercial CAD exporters (SolidWorks, SpaceClaim, NX, CATIA, etc.). |
| **Spatial Sampling & Normals** | 7 methods (Random, Grid, LHS, Sobol, Halton, Hammersley, Stratified); Multicore (HXT) & Reproducible CPU modes; verified outward surface normals & planar co-normals; settings-only JSON sampling recipes. | Local spatial density refinement around corners/edges and distance-based distributions; certified surface curvature bounds and mesh-convergence checks; surface/volume Jacobian integration weights. |
| **Parametrisation & Designs** | Single-configuration & multi-parameter affine transforms (`scale → rotate → translate` with pivot); anisotropic planar co-normal transport; Cartesian, paired, random, LHS, and Sobol designs (up to 10,000 configs, 8 parameters) with up to 32 literal comparison constraints and rejection reports. | Explicit per-region multiple transform bindings; geometry-aware domain consistency checks when moving individual walls; **CAD dimensional regeneration** (varying lengths, radii, thicknesses in `.geo`/CAD adapters with topological entity tracking). |
| **Sweeps & Exports** | Schema v2 `xarray.Dataset` NetCDF (`.nc`) and NumPy (`.npz`) exports; sequential background sweep export; SHA-256 plan/shard checksums; streaming input fingerprints; verified resume/retry into a new folder; read-only GUI Sweep Inspector and CLI (`tools/inspect_sweep.py`). | NetCDF4 / `xarray.DataTree` multi-configuration single-file container backend; incremental/chunked disk writes and configurable float precision; ragged representations for varying point counts across regenerated geometries; bounded multi-worker sweep concurrency. |
| **Projects, History & Reliability** | `.jorvik` (and legacy `.gsproj`) ZIP archives (schema v1–v3, empty projects, full projects, and lightweight recipe-only copies); 30-step buffer-sharing Undo/Redo; 2-minute `QLockFile` crash recovery; `SIGINT`/`SIGTERM` safe Qt close routing; Linux Wayland NVIDIA Vulkan auto-config. | Reducing Python list-to-NumPy memory copies for 1M+ point projects (currently peaking near ~1.1 GiB RSS in `tools/benchmark_projects.py`); profiling large viewport restoration; Windows & broader Linux desktop validation. |

---

## 5. Technical Assessment & Actionable Recommendations

### Key Engineering Strengths
1. **Strict Security & Data Integrity Posture**: Pickle is explicitly forbidden across the entire codebase (`allow_pickle=False` in all `.npy`/`.npz` reads and writes; JSON manifests with `allow_nan=False`). Parameter constraints use literal dictionary lookups and `operator` comparisons rather than `eval()`. All file writes use temporary files in the target directory followed by `os.replace()` (`atomic_write`) or exclusive hard-linking (`os.link` in `create_empty_project`).
2. **Pre-Mutation Validation**: Project loaders (`load_project`), recipe loaders (`apply_sampling_recipe`), and parametrisation loaders (`apply_setup`) validate schemas, checksums, entity references, and spinbox bounds *before* mutating any active workspace state or UI widgets.
3. **Scientific Honesty in Metadata & Warnings**: Whenever numerical approximations occur—such as chordal arc-length inversion on edges, parameter-space tessellation along curved trim boundaries, tetrahedral meshing of curved volumes, QMC sequence distortion under rejection sampling, or constraint filtering on LHS/Sobol designs—Jorvik records explicit human-readable notes in both the GUI and exported dataset metadata.

### Implemented Improvements from This Analysis
1. **Eliminated Duplicate Array Allocations & Re-Parsing in Project Persistence (`src/jorvik/project.py`, `src/jorvik/export.py`, `src/jorvik/normals.py`, `src/jorvik/viewport3d/viewer.js`, `tools/benchmark_projects.py`)**
   * *Problem*: `save_project` previously constructed a full in-memory `xarray.Dataset` via `to_xarray` (converting nested Python lists to NumPy arrays and copying them) and then converted the Python lists to NumPy arrays a second time when writing `.npy` archive entries. Similarly, `load_project` converted loaded `.npy` arrays to Python lists via `.tolist()` and then invoked `to_xarray(batches)`, re-parsing millions of Python floats and ints back into duplicate NumPy buffers. In addition, `to_xarray` called `set(tags.tolist())` on full $N$-element tag arrays, and `viewer.js` called `batch.coordinates.flat()`, allocating an intermediate $3N$-element JavaScript array.
   * *Resolution*: Factored shared zero-copy array validators (`validate_batch_header`, `validate_points`, `validate_tags`, and `validate_normals` with `np.einsum` unit-length checks and `np.unique` tag set validation). `save_project` and `load_project` now validate and stream one numeric buffer at a time and release temporary `np.ndarray` buffers immediately, cutting 1M-point `load_seconds` nearly in half (~1.10 s → ~0.56 s) and reducing `save_seconds` (~2.44 s → ~2.01 s). `viewer.js` now populates a pre-allocated `Float32Array` directly without `.flat()`.
2. **Supported 2D Interior Sampling on Validated Planar STEP/BREP Sheets (`src/jorvik/domains.py`, `src/jorvik/sampling.py`, `tests/test_domains.py`)**
   * *Problem*: `domains.py` previously restricted `'planar'` interior classification to `source_format == 'geo'`, and `sampling.py` rejected all non-`.geo` files lacking 3D volume entities.
   * *Resolution*: Generalized `domain_kind()` to recognize validated coplanar analytic `'Plane'` domains across neutral CAD formats (`.step`, `.stp`, `.brep`, `.iges`, `.igs`) while continuing to reject open or non-planar 3D surface shells. Verified 2D interior sampling, hole exclusion (`gmsh.model.isInside`), outward planar edge co-normals, and NetCDF export on rotated and holey STEP/BREP sheets.
3. **Added Adaptive Jacobian Envelope Refinement in `_surface_samples` (`src/jorvik/sampling.py`, `tests/test_sampling.py`)**
   * *Problem*: `_surface_samples` estimated each parameter triangle's upper Jacobian bound as $2\times$ the maximum across 4 probe points and aborted immediately if any candidate draw exceeded that envelope.
   * *Resolution*: Preserved the exact initial envelope and bit-for-bit sampling output for normal surfaces, while adding bounded adaptive envelope refinement (up to 4 updates via `np.maximum.at` with proposal restart) when finite candidate Jacobians exceed the initial probe envelope.
4. **Unified Resolution Constants Across Recipes, Project Validation & Area Editor (`src/jorvik/sampling_methods.py`, `src/jorvik/sampling.py`, `src/jorvik/sampling_recipe.py`, `src/jorvik/project.py`, `src/jorvik/area_editor.py`, `tests/test_project.py`)**
   * *Problem*: `sampling.py` and `sampling_recipe.py` allowed `resolution` $\in \{16, 32, 64\}$, whereas `project.py:validate_state` only permitted `(32, 64)` and `AreaEditor` coerced coarse (`16`) areas to `32`.
   * *Resolution*: Defined `RESOLUTIONS = (16, 32, 64)` in `sampling_methods.py`, shared it across `sampling.py`, `sampling_recipe.py`, and `project.py:validate_state`, and preserved coarse (`16`) resolution in `AreaEditor`.
