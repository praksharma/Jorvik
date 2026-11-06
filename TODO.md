# Development roadmap

## Agreed direction

Start with **Gmsh** as the geometry backend. The first milestone is opening the
existing `.geo` fixtures and reliably selecting their original faces and edges
in Jorvik. Sampling comes after that workflow is verified.

Keep the geometry interface independent of Qt, Three.js and Gmsh-specific objects
so a direct Open CASCADE/OCP backend can be evaluated later. Prepared CAD already
contains the boundary partitions; Jorvik does not create or edit them.

## Documentation

- [x] GUI-first onboarding and transform/sweep walkthroughs with real screenshots;
      keep Python and terminal recipes in the advanced reference.

- [x] Copyable recipes for installation, project hand-off, sampling, labelled exports,
      transforms, parameter designs, verified sweep reads and desktop workflows.

## Completed foundation

- [x] Initialise package and Git repository.
- [x] Reuse PyniteGUI's offline renderer, orientation gizmo and Qt appearance.
- [x] 3D viewport with dockable left/right panes and a Sampled Areas tree.
- [x] Menus, camera controls, grid/axes toggles, themes and layout persistence.
- [x] Hide the reference grid by default to keep geometry unobstructed.
- [x] Grouped icon toolbar with tooltips, layered geometry/sample visibility,
      and reversible region isolation with optional geometry context.
- [x] Scrollable Properties alongside Sampling and the geometry tree.
- [x] Replace the Results dock with a Sampled Areas tree, per-dataset visibility,
      generation status and region details in Properties.
- [x] Remove structural-analysis tools and dependencies.
- [x] Add source-attributed CFDBench annulus and sphere geometry fixtures.
- [x] Verify face/edge identity, physical groups and display tessellation with Gmsh.
- [x] Choose Gmsh for the first implementation.

## 1. Geometry import

- [x] Add and lock the Gmsh dependency; verify installation on the development host.
- [x] Define a backend-neutral geometry model: source file, dimension, entities,
      entity identifiers, adjacency, existing groups and bounds.
- [x] Implement a Gmsh adapter that opens `.geo` files and extracts that model.
- [x] Retain `(dimension, tag)` identities within each loaded Gmsh model; scope
      selections to the current document and clear them on replacement.
- [x] Preserve upstream physical groups and names when present; unnamed entities
      remain valid. Do not interpret group names as physical BC values.
- [x] Add File → Open Geometry and show the loaded geometry in the left pane.
- [x] Handle loading errors without losing the previously opened geometry.
- [x] Keep loading responsive and manage Gmsh initialization/cleanup explicitly.

## 2. Display geometry with original entity identities

- [x] Generate display triangles per face and line segments per original edge.
- [x] Map every display primitive to its source entity. Triangulation edges must
      not become selectable CAD edges.
- [x] Send only serializable display data to the existing offline Three.js viewport.
- [x] Fit the camera and scale reference aids to the geometry bounds.
- [x] Display face shading and optional geometry edges; support 2D and 3D cases.
- [x] Keep display tessellation separate from future sampling data and geometric
      evaluation; rendered triangles are not the authoritative curved geometry.

## 3. Face and edge selection — first usable milestone

- [x] Add explicit face-selection and edge-selection modes.
- [x] Add hover highlighting and click selection mapped to original entity IDs.
- [x] Make edge hover and selection clearly visible with thick screen-space strokes.
- [x] Support multi-selection, deselection and clearing the current selection.
- [x] Synchronize selections between the geometry tree and viewport.
- [x] Show entity type, ID, group membership and relevant details in Properties.
- [x] Support hide, isolate and show-all controls.
- [x] Prevent orbit drags from being interpreted as selection clicks.
- [x] Apply a consistent visible-entity picking policy; test edges at different
      zoom levels and faces partly obscured by other geometry.

Acceptance checks:

- Annulus: select all 8 arcs independently and the single domain face; its
  central hole remains visually open and does not produce a face hit.
- Sphere: select all 8 surface patches and all 12 original edges independently.
- A face remains one selectable entity regardless of its display triangle count.
- Tree selection and viewport clicks resolve to the same entity.
- Switching files clears stale selections; failed imports preserve the old model.
- Existing camera, gizmo, theme and panel controls continue to work.

## 4. User-defined boundary regions

- [x] Create, rename and delete arbitrary region labels.
- [x] Assign multiple selected faces or edges to a region.
- [x] Colour regions and inspect their constituent entities.
- [x] Keep unlabelled entities available for selection and sampling.
- [x] Define and document overlapping-region membership behaviour.
- [x] Save/reopen geometry references and region assignments; detect changed
      source geometry instead of silently trusting old entity tags.

## 5. First end-to-end sampling release

- [x] Seven roadmap sampling methods with seeds and target point counts.
- [x] Selected entities, imported groups and interior targets with labelled datasets.
- [x] Automatically numbered unique dataset labels, retaining each dataset’s entity associations.
- [x] Background generation, cancellation and simultaneous point previews.
- [x] Save per-region recipes in projects and edit/regenerate existing areas.
- [x] Sampling previews.
- [x] Live point-size slider, outlined high-contrast markers and scrollable sampling controls.
- [x] Outward normals where applicable: oriented surface normals for single-parent
      solids and co-normals for planar boundaries, with explicit unavailable states.
- [x] Reference-fixture boundary membership, coverage and interior checks.
- [ ] Validate arbitrary CAD trimming/containment and normal orientation; add
      certified surface sampling bounds and mesh-convergence checks.
- [x] Primary xarray Dataset interface with NetCDF export; alternative named
      arrays in NPZ. No Parquet.
- [x] Versioned export schema for coordinates, dataset labels, per-point entity
      tags and reproducibility metadata; round-trip NetCDF and NPZ checks.
- [x] Add applicable normals to exports as finite per-point numeric arrays;
      preserve them in projects and through translation.
- [x] Save/load full sampling configurations and regenerate datasets.
- [x] Development-preview installation validation, runnable export example and
      release documentation (macOS arm64 / Python 3.12).
- [x] Verify Python 3.14.8 / PySide6 6.12.0 compatibility, isolated and shared-root
      installation, offline viewport, automatic NVIDIA rendering, and preservation
      of an existing host application's dependency versions; retain Python 3.12 support.
- [ ] Validate Windows and broader Linux installations/graphics before a cross-platform release.

## 6. Broader CAD import — next priority

Release target: **Gmsh .geo + STEP + Open CASCADE BREP + IGES**. All four are now
implemented and tested on small fixtures; broader CAD compatibility and metadata
checks remain below. Prioritise topology-preserving CAD exchange, with
original faces/edges and valid solids rather than triangle-only meshes.

Current backend is the Gmsh Python API, not PythonOCC/OCP. `.geo` scripts can use
Gmsh's built-in kernel or Open CASCADE; do not assume every current model is OCC.
Gmsh documents STEP/IGES import through Open CASCADE and
[`gmsh.model.occ.importShapes`](https://gmsh.info/doc/texinfo/#gmsh_002fmodel_002focc_002fimportShapes)
for BREP, STEP and IGES. Start there without introducing a second geometry backend.

### Format order and implementation

- [x] **1. STEP (`.step`, `.stp`)**: first neutral-format implementation; retain
      analytic/curved geometry, original topology and closed-solid definitions.
      A surface STEP and a solid STEP fixture are covered.
- [ ] Support 2D interior sampling on validated planar STEP sheets, without
      requiring a solid volume. Verify coplanarity and trimmed-face containment,
      including inner holes; reject non-planar or ambiguous domains clearly.
      Add rectangle, rotated-plane, trimmed/holey-sheet and non-planar rejection
      fixtures, plus sampling, export and GUI coverage. Preserve current solid
      sampling and `.geo` behaviour; do not imply general shell interior support.
- [ ] Evaluate AP203/AP214/AP242 and richer product metadata; do not promise
      face-level labels will survive every exporter/importer combination.
- [x] **2. Open CASCADE BREP (`.brep`)**: support OCC's native topology format;
      document the accepted BREP dialect rather than treating all B-Rep files alike.
- [x] **3. IGES (`.iges`, `.igs`)**: accept surface-only and solid models;
      disconnected/unsewn surfaces remain usable for boundary sampling, while
      interior sampling requires a validated closed domain.
- [x] Share format detection and geometry loading between the display importer
      and sampling workers, so a file can both render and generate valid samples.
- [x] Update file filters, CLI help, errors and documentation with actual supported
      extensions; validate contents and handle malformed/unsupported files clearly.
- [x] Keep `.geo` and both Gmsh kernels working; do not require conversion to OCC
      or assume built-in-kernel scripts can be converted losslessly to STEP.
- [x] Expose vertices, edges, faces, solids and adjacency consistently in the
      backend model; keep tessellation separate from authoritative CAD geometry.
- [ ] Detect source units and coordinate scale where available; retain original
      units/metadata and explicitly record any requested conversion.
- [ ] Support multiple bodies and assembly placements; distinguish disconnected
      solids, open shells and non-manifold geometry before interior sampling.
- [ ] Evaluate optional healing/sewing with explicit reporting of modifications,
      tolerance and entity remapping; never silently change prepared BC partitions.

### Names, metadata and region assignment

- [ ] Import available CAD entity names, colours and assembly/body names separately
      from Gmsh physical groups; expose what actually survived translation.
- [ ] Preserve supplied boundary partitions without claiming topology preservation
      guarantees face names, simulation BC labels or persistent numeric IDs.
- [x] Let users assign region labels to multiple faces/edges when CAD names are
      absent; separate these assignments from generated dataset labels.
- [x] Track source hash, format and loader options/backend version in projects and
      exports; reject changed sources/import settings before generation. Reopening
      geometry clears old region references rather than reusing numeric entity IDs.
- [ ] Define explicit entity remapping if optional healing or topology-changing
      reimport is introduced; do not silently retain old region references.
- [ ] Evaluate direct OCP access only if Gmsh metadata/topology queries become
      insufficient; keep the backend-independent application interface.

### Validation and release checks

- [x] Download and verify pinned Gmsh STEP/BREP/IGES fixtures with provenance
      and licence; test topology, face/edge sampling, solid interior sampling,
      surface-only rejection, degenerate edges, GUI visibility and exports.

- [ ] Add small, attributable STEP/BREP/IGES fixtures: curved solid with a hole,
      prepared boundary patches, open surface, multiple bodies and assembly placement.
- [ ] Verify face/edge selection, hide/isolate, names, point membership, interior
      containment, cancellation and export round trips on every supported format.
- [ ] Test real exports from SolidWorks and Ansys SpaceClaim first; expand the
      interoperability matrix to NX, CATIA, Creo, Inventor, Fusion, Onshape,
      FreeCAD and Rhino as accessible fixtures allow. Record exporter version,
      settings and units rather than assuming every product exports every format.
- [x] Preserve the current model/datasets after failed imports and ensure import
      and sampling errors identify open domains or invalid topology clearly.

### Later translators and exclusions

- [ ] **4. Parasolid (`.x_t`, `.x_b`)**: evaluate translator availability, licence,
      distribution terms, cost and topology/metadata fidelity before committing
      to direct import. STEP conversion is the initial workflow.
- [ ] Evaluate ACIS (`.sat`, `.sab`) and Rhino `.3dm` only when demand and a suitable
      licensed reader justify them; they are not implied by OCC exchange support.

STL remains excluded. Native SolidWorks, CATIA, NX, Creo and Ansys project readers
are outside the initial release; use the verified neutral export formats instead.

## Later

- [ ] Additional sampling strategies and local density refinement.
- [ ] Parametric geometry families and stable region tracking.
- [x] Profile generation and add multicore Gmsh/HXT meshing with CPU-core control,
      reproducible fallback, lazy scientific imports and vectorized volume setup.
- [ ] Cache/reuse geometry preparation and support batch generation.
- [ ] Evaluate GPU/JIT acceleration if candidate mapping or clipping becomes
      the measured bottleneck.

## Region inspection controls

- [x] Geometry tree context menu: hide selected faces, isolate selected faces, show all faces.
- [x] Sampled Areas context menu: rename, change colour, visibility and isolation.
- [x] Preserve coordinates and visibility across renaming; export new labels and colour metadata.

## Parametrisation workspace

### Implemented first slice

- [x] Separate workspace accessible below Sampled Areas, with four tabs.
- [x] Single affine configuration: translation, rotation and positive scaling,
      whole domain or selected-region augmentation; legacy translations still load.
- [x] Explicit reversible preview from originals; preserve coordinates, visibility and colours.
- [x] NetCDF/NPZ export with named parameter values and transformation provenance.
- [x] Save/load lightweight setup; validate source geometry and region references.

### Parameters and transformation engine

- [x] Add multiple named parameters with units, reference/default values, ranges,
      explicit value lists or counts, and unambiguous mapping to transformations.
- [x] Add rotation with axis, angle units and pivot; add positive uniform and
      non-uniform scaling with explicit origin/pivot.
- [x] Define fixed scale → rotate → translate order in the world coordinate frame;
      persist the order and frame and always evaluate from original coordinates.
- [x] Retain whole-domain transformations as the default consistent-domain mode;
      label independent region transformations as augmentation with unverified
      consistency until domain-aware validation is available.
- [ ] Allow explicit parameter-to-region bindings and multiple transformations on
      a region; define overlapping-region behaviour and shared-boundary consistency.
- [x] Reject non-finite values, singular transforms and non-positive scaling;
      keep reflections out of the initial supported transform set.
- [x] Generate applicable boundary normals first, with orientation validation;
      rotate/scale normals with the normalized inverse transpose, preserving
      translation invariance. Never silently invent outward normals for open shells.
- [x] Retain validated per-edge source-plane normals for anisotropic transformation
      of planar boundary co-normals; preserve frames through projects/exports.
      Legacy samples without frames retain explicit unavailable-normal behaviour.
- [x] Document density changes after scaling and retain original point counts.
- [ ] Evaluate surface/volume Jacobian weights for downstream integration.

### Configuration generation and constraints

- [x] Cartesian products, paired explicit configurations, seeded random, LHS and
      Sobol designs; distinguish per-parameter value counts from total design size.
- [x] Generate parameter-space designs independently of spatial sampling settings;
      record design algorithm, seed, bounds and actual parameter values.
- [x] Show configuration count, points per region/configuration, estimated output
      size and memory needs before starting; avoid allocating huge Cartesian grids.
- [x] Literal parameter constraints with GUI editing, visible rejection reasons,
      stable candidate IDs, same-unit comparisons and no value changes/replacements.
- [ ] Geometry/model validity checks beyond literal parameter comparisons.
- [x] Record effects of filtering on LHS/Sobol coverage; retain actual rejected
      values/reasons and checksum the persisted report.
- [x] Paginated configuration table/selection and explicit preview/export of one
      chosen configuration without processing the entire sweep.
- [x] Literal case-insensitive configuration search with stable filtered pagination;
      preserve unfiltered design scope for full sweep exports.

### Preview and execution

- [x] Extend reversible preview to rotation/scaling while keeping original CAD,
      datasets, region assignments, camera and visibility intact.
- [ ] Extend preview to regenerated CAD geometry when regeneration is implemented.
- [x] Keep parameter edits lightweight: mark stale previews/results and require
      explicit Preview or Process configurations; never auto-run complete sweeps.
- [x] Preview one selected configuration or export the full generated design in
      the background without changing the viewport; stream one file at a time.
- [x] Single background export worker with one transformed configuration at a time;
      keep scalar plans/progress separate from clouds and lock borrowed sources.
- [ ] Configurable bounded concurrency and within-file chunk/cancellation checkpoints.
- [x] Progress bar, elapsed time, attempted/written/failed counts and Cancel.
- [x] Stop-on-error/continue policies, explicit partial-run manifests, atomic shards,
      cooperative cancellation and safe deferred workspace/application closure.
- [x] Resume/retry into a new folder after verifying original sample/recipe
      fingerprints, setup/plan and completed outputs; retain source files unchanged.
- [x] Preserve each region's method, seed, count and geometry-accuracy provenance
      in affine sweep outputs; include hidden/unaffected datasets.
- [ ] Geometry-aware membership checks against the intended transformed/regenerated
      domain; clearly distinguish outside points, invalid geometry and unverified
      checks. Moving just a wall must not imply the interior remains valid.

### CAD dimensional regeneration — later stage

- [x] Store sampling recipes independently of generated output: entity/region/group
      references, target, method, count, seed, accuracy and compute settings;
      GUI save/load with validation and undo/redo, plus Qt-independent JSON APIs.
- [ ] Define an explicit CAD parameter source (e.g. supported `.geo` variables or
      a geometry-generation adapter); imported STEP/BREP need not contain editable
      design history or named dimensions.
- [ ] Regenerate models for radius, width, length and thickness changes and rerun
      recipes for every valid configuration; keep this separate from point mapping.
- [ ] Track entities/regions across regeneration and topology changes; reject
      missing, split, merged or ambiguous boundaries until explicitly remapped.
- [ ] Validate regenerated closed domains, holes and material regions, then
      recompute interior points and applicable normals rather than moving only
      boundary points for a dimension change.
- [ ] Cache reusable preparation keyed by source/configuration/recipe and preserve
      deterministic configuration ordering and provenance across worker counts.

### Parametric exports and persistence

- [x] Version the parametric schema independently of ordinary dataset exports.
- [ ] NetCDF4 backend and xarray DataTree: shared parameter values indexed by
      configuration, one group per region with coordinates `(configuration, point,
      xyz)`, entity tags, applicable normals and complete recipe/transform metadata.
- [ ] Use stable safe group/key identifiers plus an original-label map; arbitrary
      labels, slashes and reserved names must round-trip without collisions.
- [x] Write parameter values once per shard and provide streaming sweep readers
      plus `(x,y,z,mu...)` array construction with independent region counts.
- [x] Metadata/run inspection and per-file structural validation; explicit partial-run
      opt-in and a read-only inspection command.
- [x] GUI sweep inspector with background verification, complete/failed/pending
      states, searchable paginated rows, legacy-checksum notice and cancellation.
- [x] SHA-256 plan/shard checksums with streaming verification, byte counts and
      optional rejection of legacy sweeps without integrity records.
- [ ] Incremental/chunked disk writes, compression and configurable precision;
      do not assume xarray append mode automatically extends configuration arrays.
- [ ] Handle varying point counts explicitly through ragged representations or
      separate configuration groups; never silently pad, truncate or duplicate points.
- [x] NPZ/NetCDF configuration files plus versioned manifest, scalar plan and result
      journal; stable safe filenames and per-file original region-label maps.
- [ ] Preserve source hashes, units, original recipes, algorithms/seeds, parameter
      configurations, transform order/pivots, warnings and completed/failed status.
- [x] Atomic files and explicit complete/error/cancelled/incomplete run states;
      require a new output folder and preserve all existing destinations.
- [x] Persist multiple named parameters, transform bindings, units/reference values
      and versioned design definitions in JSON/projects; retain legacy setup support.
- [x] Persist literal constraints in setup JSON/projects and export provenance.
- [ ] Extend setup JSON with CAD regeneration recipes.
- [x] Full project persistence for geometry, regions, recipes, generated points and
      translation setup; embed the main CAD source and validate source hashes.
- [x] Optional point-cloud omission with a separate lightweight project copy,
      validated pending recipes, unchanged active data, and explicit regeneration.
- [ ] Full `.geo` dependency bundling/checksums.
- [x] Retain setup changes when reopening the workspace and include them in projects;
      remap region renames and clear bindings when datasets/model are cleared.
- [x] Explicit new/reset parametrisation setup action; restore the original view,
      clear region bindings and retain the reset configuration on reopening.

### Acceptance checks

- [x] Rotation/pivot and transform-order cases; inverse-transpose normals under
      non-uniform scaling; zero-motion and identity configurations.
- [ ] Repeated preview, scope changes, Restore, Close and Escape never accumulate
      transforms or overwrite original geometry, samples or region visibility.
- [ ] Whole-domain boundary/interior consistency and explicit warnings for unsafe
      independent region changes; invalid/missing constraints and source changes.
- [ ] Reproducible Cartesian/LHS/Sobol/random configurations and stable provenance;
      verify coverage limitations after constraint filtering.
- [ ] Unequal region counts and varying configuration counts round-trip through
      DataTree/NetCDF4 and NPZ shards with labels, parameters, tags and normals.
- [ ] Cancellation, partial failures, disk-full, resume/retry, changed setup versions
      and multi-million-point runs with bounded memory and responsive UI.
- [ ] End-to-end PINN example loading parameter-conditioned coordinates and separate
      boundary/interior datasets, plus regeneration with topology-change rejection.

## Documentation

- [x] Add a searchable Zensical site with focused user/developer pages, light/dark
      themes, canonical-guide sync checks and a strict CI build.
- [x] Publish built Zensical assets to GitHub Pages from `main`, using the project
      site URL, a Pages artifact and a deployment job; keep pull requests build-only.

## Project persistence and sampled-area editing

- [x] Use `.jorvik` for new documents, launch examples and recovery copies; keep
      legacy `.gsproj` opening/saving/API/recovery support without changing archives.

- [x] Empty `.jorvik` documents and Qt-independent creation API; retain the same
      document path and unsaved-change protection when importing the first geometry.
- [x] Launch with `.jorvik` or supported geometry files using normal loaders;
      report invalid launch files with nonzero status and preserve their contents.

- [x] File menu save/open/save-as `.jorvik` archives with CAD and numeric arrays.
- [x] Restore cached topology and samples without regeneration, including moved sources.
- [x] Preserve recipes, colours, visibility, selection, camera, layout and translation setup.
- [x] Right-click Edit: label, target entities, method, count, seed, accuracy and CPU settings.
- [x] Replace edited area only after success; preserve originals on cancellation/failure.
- [x] Versioned validation, checksums, no pickle, atomic writes and failed-load preservation.
- [x] Background project archive I/O and content fingerprints with editing locks;
      measure 100k/1M-point persistence time, exact round trips and peak RSS.
- [ ] Reduce large-project numeric/list copies; the 1M-point backend benchmark
      peaks near 1.1 GiB, and large viewport restoration still needs profiling.
- [x] Atomic two-minute recovery copies, live-session locks, startup recovery,
      explicit Save As after recovery, and cleanup after successful save/discard.
- [x] Unsaved-change Save/Discard/Cancel prompts before workspace replacement or
      closing; cancelled or failed saves retain the current workspace.

## Shutdown reliability

- [x] Route Ctrl+C/SIGINT and SIGTERM through the Qt close workflow, preserving
      unsaved-change protection and restoring signal handlers after the event loop.
- [x] Remember close requests during project I/O/save and retry after completion;
      prevent nested close handlers from opening duplicate prompts.
- [x] Test real process exit, deferred close, completed save and Cancel behaviour;
      manually verify Ctrl+C in automatic NVIDIA and software rendering modes.

## Session editing history

- [x] Undo/redo for dataset generation, regeneration, renaming, colours, clearing,
      duplication/deletion, boundary-region edits and saved translation setup.
- [x] Edit-menu shortcuts, labelled actions, 30-edit history, redo branching,
      busy-state locks, document reset and saved-content dirty-state checks.
- [x] Share immutable numeric buffers across snapshots; restore dataset visibility,
      selection, isolation and parameter references without rerunning generation.
