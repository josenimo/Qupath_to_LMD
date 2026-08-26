# facts.md

Living record of what is true about this app. Corrected in place when it goes stale — not
append-only (that is `decisions.md`). Last verified: 2026-08-26.

Restructuring into two workflows is underway: `ROADMAP.md`. Phases 0 and 1 are done, so
the app now has a workflow router; the cell workflow itself is still scaffolding.

---

## What the app is for

Web app that turns QuPath annotations into a Leica LMD7 cutting file, for Deep Visual
Proteomics (DVP) experiments. Scientists annotate tissue in QuPath, export a GeoJSON,
upload it here, choose which class goes into which well of a 96- or 384-well plate, and
download an `.xml` the LMD software can execute.

Part of the [openDVP](https://github.com/CosciaLab/openDVP) framework. >60 unique users.
Cites Makhmut et al., *Cell Systems* 14, 1002-1014.e5 (2023).

## Deployment

- Live at `https://qupath-to-lmd-mdcberlin.streamlit.app/` on Streamlit Community Cloud.
- Cloud installs from **`requirements.txt`**, which is generated:
  `uv pip compile pyproject.toml -o requirements.txt`. Local dev uses `uv sync` off
  `pyproject.toml` + `uv.lock`. All three must agree or local and deployed diverge.
- `git remote origin` = `https://github.com/josenimo/Qupath_to_LMD.git`; the README and
  `pyproject.toml` URLs point at `CosciaLab/Qupath_to_LMD`. Branches: `master` (default),
  `dev`.
- Python `>= 3.11, < 3.14`. Version in `pyproject.toml`: `0.2.0`.

## Layout

Since Phase 0 (`decisions.md` 021), `core.py` and `utils.py` are gone and the library is
split by responsibility. Library functions take explicit arguments and return values —
none of them read `st.session_state`.

```
streamlit_app.py                  session init, logging, and the workflow router. Thin.
src/qupath_to_lmd/
  === library layer: pure, no Streamlit ===
  model.py                        CollectionPlan, canonical column names, provenance
  geojson.py                      read_and_qc, explode_classes, extract_coordinates,
                                  rewrite_classification, sanitize_for_qupath,
                                  measurements_frame, area_measurement_column
  plate.py                        plate shapes, acceptable_wells, layouts, saw parse/convert
  qc.py                           triangle_qc, validate_saw, pixel_size_qc (report objects)
  export.py                       build_collection, build_bundle, ORIENTATION_TRANSFORM
  extras.py                       QuPath classes.json generation
  === UI layer: Streamlit, owns session_state ===
  ui_shared.py                    steps both workflows use
  ui_legacy.py                    annotations workflow (frozen as of Phase 1)
  ui_cells.py                     cell-segmentation workflow (scaffolding only so far)
  === other ===
  mock_streamlit.py               patch_streamlit() — stubs st.* for notebook use
  __init__.py                     empty
tools/
  golden_harness.py               byte-equality regression gate
  golden/                         8 reference artefacts
demo_Qupath_project/              real QuPath project used as test fixture
  TD_01_verysmall_mIF.geojson     9 features: 6 annotation Polygons + 3 calibration Points
  Single_cells.geojson            131 features: 121 cells + 7 annotations + 3 Points
  multiclass_cells.geojson        72 cells from a real QuPath 0.7.0 export: single-class,
                                  multi-class and unclassified, plus 3 added calib points
  demo_samples_and_wells.txt      python-dict-literal saw file for the upload path
  QuPath_scripts/*.groovy         detections_to_annotations, select_random_detections
assets/                           screenshots, example classes.json
```

Key dependency: **`py-lmd`** (`lmd.lib.Collection`) does the actual coordinate transform
and XML writing. Also geopandas/shapely for geometry, loguru for logging.

## What QuPath actually puts in the GeoJSON

Verified against both demo files.

- `id` — uuid string, stable per object. `objectType` — `"annotation"`, `"cell"`, or
  `"detection"`. **`objectType` is the natural discriminator between the two planned
  workflows**, and a single file can legitimately mix them (`Single_cells.geojson` has both).
- `classification` — arrives as a **JSON string** (`'{ "name": ..., "color": [r,g,b] }'`),
  not a dict, hence the `ast.literal_eval` on it. Absent for unclassified objects. Comes in
  three shapes, all seen in one real QuPath 0.7.0 export:
  - `{"name": "Tumor", "color": [...]}` — a single class
  - `{"names": ["Tumor", "Immune cells"], "color": [...]}` — **multi-class**, note the
    plural. Sorted and joined with `--` (`geojson.MULTICLASS_SEPARATOR`) into one flat
    combined class the user can assign a well to, e.g. `Immune cells--Tumor`. Sorted so one
    set of classes always yields one class name regardless of the order QuPath wrote them.
  - missing entirely — unclassified, dropped with a count
- `name` — set on calibration Points, `None` on annotations and cells. **QuPath omits a
  property entirely when no object in the export has it**, so a file with no calibration
  points has no `name` column at all. That is the single most common shape of a "broken"
  export and it is not broken — the user just has not added the points, or exported a
  selection that left them out.
- `isLocked` — present on annotations, NaN elsewhere.
- `measurements` — **only on cells/detections**, a JSON string with QuPath's per-object
  measurements. `Single_cells.geojson` carries **126 fields** per cell:
  `Cell: Area`, `Perimeter`, `Circularity`, `Max/Min caliper`, `Eccentricity`, and
  mean/std/max/min per marker channel (DAPI, Vimentin, CD3e, panCK, CD8, Ki67, COL1A1,
  CD20, CD68, and the `_bg` channels). This is the raw material for the planned per-class
  statistics and for measurement-ranked selection.
- **Coordinates are in image pixels**, but `Cell: Area` is in **µm²**. So the scale is
  recoverable: `sqrt(Cell:Area / polygon_area_px)` gives 0.3467 µm/px across all 121 cells
  of `Single_cells.geojson` with 0.2% spread. Used as a cross-check on the user's µm/px
  input, not to auto-fill it (`decisions.md` 011).

## py-lmd surface we use

- `Collection(calibration_points)` → `new_shape(points, well, name)` → `save(path)`;
  also `plot(save_name=...)` and `stats()`.
- `Collection` does **no** cut-path optimization. `lmd.lib` does export
  `tsp_greedy_solve(node_list, k=100, return_sorted=False)` (returns indices) and
  `tsp_hilbert_solve(data, p=3)`, but only the mask-based `SegmentationLoader` calls them.
  To order shapes we must call the solvers ourselves before `new_shape`.
- `SegmentationLoader`'s config is the field's vocabulary for shape processing —
  `shape_dilation`, `shape_erosion`, `binary_smoothing`, `convolution_smoothing`,
  `poly_compression_factor`, `path_optimization`, `hilbert_p`. It operates on label masks,
  so none of it applies to this app's vector path; shapely equivalents are ours to write.

## The pipeline, step by step

One page, top to bottom; each step gates on session state from the previous one. Steps 1–3
are shared, then the router dispatches to one of two workflows.

**Shared:** 1 upload + QC · 2 workflow choice · 3 calibration points.
**Legacy then continues:** 4 optional class split · 5 plate layout · 6 process and download.
**Cells then continues:** 4 image scale (µm/px) · 5 onwards not built yet.

Step numbers are passed into the `ui_shared` step functions rather than hard-coded, because
the two workflows reach the shared steps at different points.

1. **Upload + QC** — `geojson.read_and_qc`, cached in the app by a thin wrapper.
   `geopandas.read_file`, then `set_crs(None, allow_override=True)`. Raises `GeojsonError`
   if the file is empty or has no `name`/`classification` column. Then, in order:
   - `Point` geometries with a non-empty `name` become the **calibration point pool**
     (`{name: [x, y]}`); all `Point`s are then dropped from the GeoDataFrame.
   - Rows with NaN `classification` (unclassified QuPath objects) are dropped, count
     recorded in the report. Note the demo files' 3 NaN-classification rows *are* the
     calibration Points, which have already left the frame — so both report 0 here.
   - `classification_name` column derived from `classification` (dict or its `str` repr).
   - `MultiPolygon`s are recorded in the report and dropped — py-lmd cuts one closed path
     per shape, so they have no meaning.
   Returns `(gdf, calibration_points, GeojsonReport)`. The report is rendered by the app,
   not by the library.
1.1 **Calibration selection** — three `st.selectbox`es pick 3 names from the pool, order
   matters. `qc.triangle_qc` returns a `TriangleReport` with the calibration array and the
   fraction of Polygons/LineStrings intersecting the triangle; `is_concerning` below 25%.
1.2 **Optional class split** — `geojson.explode_classes` turns e.g. `T-Cell` into
   `T-Cell_001…`, one name per shape, for single-cell collection. Stores
   `original_classification_name` so repeated runs stay idempotent, and rewrites the
   nested `classification` dict via `geojson.rewrite_classification`.
2. **Plate layout** — plate type (384/96), margin, row step, column step feed
   `plate.acceptable_wells`. Two views: `plate.default_layout` (well names, allowed ones
   green) and `plate.sample_layout` (classes placed into allowed wells in **sorted** order,
   optionally randomized; returns the classes that did not fit).
   "Confirm and use this plate layout" → `plate.layout_to_saw` → `qc.validate_saw`.
2.3 **Custom samples-and-wells upload** — overrides the generated layout.
   `plate.parse_saw_file` reads a `.txt`/`.json` containing a **Python dict literal** and
   `ast.literal_eval`s it (trailing commas fine, `//` comments not). Raises
   `SawParseError` with a specific reason. Sets `use_plate_wells = False`.
3. **Process** — `model.plan_from_class_wells` builds a `CollectionPlan`
   (`group_key = classification_name`, `well` mapped from the saw dict, unmatched shapes
   left with no well and reported), then `export.build_collection`:
   - `geometry.simplify(1)` then `geojson.extract_coordinates` (Polygon exterior, or
     LineString coords; anything else raises).
   - `Collection(calibration_points=plan.calibration_array)` with
     `ORIENTATION_TRANSFORM = [[1, 0], [0, -1]]` — **flips Y**, because QuPath image
     coordinates grow downward and the LMD stage does not.
   - One `new_shape` per selected row, in load order, into `plan.shapes["well"]`.
   - QC image is written to a fresh temp directory, not the working directory.
   Then `export.build_bundle` zips: `<stem>.xml`, `<stem>_<plate>_wellplate.csv`,
   `samples_and_wells.json`, `provenance.json`, `<stem>_processed.geojson` (sanitised for
   QuPath re-import), `collection.png`, and the session log.

**Extra #1** (below the main flow): generates a QuPath `classes.json` from two lists of
categoricals × replicate count, cycling 6 hard-coded colours as Java signed ints.

## Workflow routing and image scale (Phase 1)

- The router suggests a workflow from `objectType` counts: more cells/detections than
  annotations suggests the cell workflow, otherwise annotations. It is a **suggestion** —
  the radio is always user-changeable, and legacy is the default before any file is loaded.
  Verified on both demo files (`Single_cells.geojson` → cells, `TD_01…` → legacy).
- `qc.pixel_size_qc` cross-checks the user's µm/px against `sqrt(Cell: Area / polygon area)`
  and warns above 5% disagreement. On `Single_cells.geojson` the implied value is
  0.3467 µm/px with 0.23% spread, so entering 3.467 is flagged as 10.00×. Files without area
  measurements are the normal case, so the check reports quietly that it could not run. The
  entered value is never overwritten (`decisions.md` 011, 029).
- `geojson.measurements_frame` explodes the `measurements` JSON into a DataFrame indexed
  like the input frame. Phase 2's per-class statistics will build on it.


## Session state keys

Initialised in the block at the top of `streamlit_app.py`. Any new key belongs here too.

| Key | Holds |
| --- | --- |
| `session_id` | uuid4 string, shown to the user for bug reports, names the log in the zip |
| `log_file_path` | temp `.log` path; loguru sink, shipped inside the download zip |
| `workflow` | `'legacy'` \| `'cells'` — which workflow the router dispatched to |
| `pixel_size_um` | µm per pixel, entered by the user; `None` until they do |
| `view_mode` | `'default'` \| `'samples'` — which plate table is rendered |
| `gdf` | the working GeoDataFrame (points removed, `classification_name` added) |
| `geojson_report` | `GeojsonReport` from the last read, re-rendered on every rerun |
| `calibration_points` | `{name: [x, y]}` calibration-point pool from the geojson |
| `calibs` | `[name1, name2, name3]` selected calibration point names, order matters |
| `calib_array` | 3×2 numpy array of the selected points, passed to `py-lmd` |
| `saw` | samples-and-wells dict `{class_name: well}` |
| `use_plate_wells` | True if `saw` came from the plate builder, False if uploaded |
| `file_name` | uploaded geojson filename; change of name triggers reprocessing |
| `plate_df` | the displayed plate DataFrame |
| `plate_gen_params` | dict of plate/margin/step/randomize; change triggers regeneration |
| `show_saw_uploader` | whether the custom-saw uploader is visible |
| `zip_buffer`, `bundle_name` | the download bundle and its filename |
| `collection_image` | path to the QC image of the last processed collection |

## Domain constants and conventions

- 384-well plate = rows A–P (16) × columns 1–24. 96-well = rows A–H (8) × columns 1–12.
- Wells are strings like `"C3"`: row letter + column number, no zero padding.
- On the LMD7 with a 384-well plate, **rows A/B and columns 1/2 collect unreliably** —
  hence the margin control; margin 2 is the documented suggestion for 384.
- Row/column *step* leaves blank wells between samples for easier pipetting.
- Calibration points should sit **close to the annotations**; a wide triangle warps small
  shapes and you cut the wrong tissue. README has worked examples.
- `simplify(1)` tolerance is in image pixel units; it reduces vertex count for the LMD.

## Conventions in the code

- **4-space indentation throughout.** The old 3-space style went out with `core.py` and
  `utils.py` in Phase 0; every remaining file is 4-space.
- **Library code does not call `st.*`.** `geojson.py`, `plate.py`, `qc.py`, `export.py` and
  `extras.py` return report objects or raise domain exceptions
  (`GeojsonError`, `SawParseError`); `streamlit_app.py` decides what to show and whether to
  stop. Only `mock_streamlit.py` mentions `st` at all, for notebook use.
- Every meaningful step logs via loguru, and the app surfaces the same information via
  `st.*`. The log file goes into the download zip, so log lines are part of the support story.
- **`st.number_input` rules, learned the hard way** (a value snapping back after entry):
  - Never pass `value=` on reruns to a widget that also has `key=`. With a key, the widget's
    own state is the source of truth; re-seeding `value` from `session_state` fights it.
    Pass `value=None` once and read the return value.
  - `step` is rendered as the HTML input's `step` attribute and **browsers snap entries to
    that grid**. A `step` coarser than `format` can display silently rounds typed input —
    `step=0.01` with `format="%.4f"` turns 0.3467 into 0.35. Keep them matched.
  - `value=None` makes the field start genuinely empty and return `None` until the user
    types, which is better than a `0.0` sentinel that has to be told apart from a real entry.
  - Pixel size is therefore `step=1e-4`, `format="%.4f"`, `min_value=1e-4` — see the
    `PIXEL_SIZE_*` constants in `ui_shared.py`. Values with more than 4 decimals are rounded.
- `ruff` configured in `pyproject.toml`: line-length 120, target py311, double quotes,
  google docstring convention, `E501` ignored.
- No test suite in the repo (pytest was removed in `0530833`), though `pytest` is still a
  declared dependency.

## Known quirks and open issues

Not scope creep — recorded so nobody rediscovers them, and so a fix is a deliberate choice.

Still open:

- **Nothing in the app requires QuPath `measurements`.** Areas are computed from the shape
  geometry and the user's µm/px, so a plain export without measurements is fully supported —
  which matters because QuPath only includes them if the user ticks the option, and a real
  14145-cell export had none. `qc.pixel_size_qc` is therefore **opportunistic**: it
  cross-checks µm/px only when area measurements happen to be present, and says so quietly
  (`st.caption`, not a warning) when they are not. Do not build anything that depends on
  measurements being there.

- **`randomize` has no seed.** `plate.sample_layout` calls `random.sample` unseeded, so a
  randomized layout cannot be reproduced or reported in a methods section. Phase 4
  introduces seeds for the selection engine; this should join them.
- **`py-lmd`'s `Collection.plot` calls `plt.show()`** (`lmd/lib.py:182`), so it blocks
  forever under a GUI matplotlib backend. A plain `python` run on macOS hangs unless
  `MPLBACKEND=Agg` is set; under Agg it merely warns "FigureCanvasAgg is non-interactive".
  Harmless in the deployed app and under `streamlit run` (no display, so Agg is chosen), but
  any headless script that builds a collection must set it — `tools/golden_harness.py` does.
- `pytest` is still a declared dependency with no tests (`decisions.md` 006/008).
- `ruff check .` reports **5 findings**, all in `mock_streamlit.py` (I001, D205, D212,
  2×W293) — down from 32 before Phase 0, because the files carrying the rest are gone.
  Pre-existing backlog, untouched by Phase 0 per `CLAUDE.md` rule 6.

Fixed in Phase 0 (`decisions.md` 021), kept here briefly so the history is legible:

- The `EPSG:4326` CRS mislabelling — `geojson.read_and_qc` now clears it, so `.area` and
  `.distance` work on pixels. This was blocking every area-based feature in Phases 2–4.
- Wells were validated against a hard-coded 384 grid, so `K5` passed on a 96-well plate.
  `qc.validate_saw` now takes the plate type.
- The plate CSV in the bundle was always named `_384_wellplate.csv`.
- The QC image was written to `./TheCollection.png` and Extra #1 wrote `./classes.json`,
  both into the working directory — a real problem on a shared server, where concurrent
  users overwrite each other. Both now go to a temp dir or straight to the download.
- Surplus classes beyond the available wells were dropped after a warning that did not say
  which ones; `plate.sample_layout` now returns them and the app names them.
- Class→well assignment iterated a `set`, so the layout changed between reruns. Now sorted.
- `parse_dictionary_from_file` returned `{}` on a parse error, and the app then reported
  "loaded and checked" for an empty dict. Now raises `SawParseError` with the reason.
- The MultiPolygon warning selected an `annotation_name` column that QuPath never writes,
  so that branch would have raised `KeyError` for any user who actually had a MultiPolygon.
  Verified against a synthetic file, then fixed.

## Regression harness

`tools/golden_harness.py`, with the reference output in `tools/golden/` (8 files, ~220 KB).

```
uv run python tools/golden_harness.py check      # compare against the golden files
uv run python tools/golden_harness.py capture    # re-bless, only when output should change
```

Five cases, each covering a path where a change could silently move coordinates:
`annotations` (ordinary mini-bulk), `cells` (128 shapes with measurements),
`cells_exploded` (one well per shape), `annotations_96` (different plate geometry),
`multiclass_cells` (real QuPath 0.7.0 export shape). Each produces an XML and a CSV, so
10 artefacts.

- The committed golden files are **byte-identical to output captured from the pre-Phase-0
  code**, so the reference traces back to the version that had been in production.
- Verified to fail as well as pass: replacing `export.ORIENTATION_TRANSFORM` with the
  identity matrix (a broken Y flip) makes all four XML comparisons differ and `check` exit 1.
- It sets `MPLBACKEND=Agg` itself, so it needs no special invocation.
- What it does **not** cover: the UI, the QC/warning behaviour, LineString geometries (no
  demo file has one), and any input outside the four cases. It also proves "unchanged", not
  "correct" — a pre-existing coordinate bug would be faithfully preserved.

`CLAUDE.md` rule 6 makes running it mandatory before committing anything that touches
geometry, calibration, well assignment or export.
