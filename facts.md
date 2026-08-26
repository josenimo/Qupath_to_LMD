# facts.md

Living record of what is true about this app. Corrected in place when it goes stale — not
append-only (that is `decisions.md`). Last verified: 2026-08-26.

Planned restructuring into two workflows: `ROADMAP.md`. Everything below describes the app
**as it is today**, before that work.

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

```
streamlit_app.py                  single-page UI, top-to-bottom, ~390 lines
src/qupath_to_lmd/
  core.py                         pipeline: load/QC, triangle QC, saw QC, split, collection
  utils.py                        plates, wells, dataframes, parsing, geometry helpers
  mock_streamlit.py               patch_streamlit() — stubs st.* so core/utils run headless
  __init__.py                     empty
demo_Qupath_project/              real QuPath project used as test fixture
  TD_01_verysmall_mIF.geojson     9 features: 6 annotation Polygons + 3 calibration Points
  Single_cells.geojson            131 features: 121 cells + 7 annotations + 3 Points
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
  not a dict, hence the `ast.literal_eval` on it. `None` for unclassified objects.
- `name` — set on calibration Points, `None` on annotations and cells.
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

The UI is one linear page; each step gates on session state from the previous one.

1. **Upload + QC** — `core.load_and_QC_geojson_file` (`@st.cache_data`).
   `geopandas.read_file` on the uploaded file. Requires a `name` column or it stops.
   Then, in order:
   - `Point` geometries with a non-empty `name` become the **calibration point pool**
     (`{name: [x, y]}`); all `Point`s are then dropped from the GeoDataFrame.
   - Rows with NaN `classification` (unclassified QuPath objects) are dropped, count shown.
   - `classification_name` column derived from `classification` (dict or its `str` repr).
   - `MultiPolygon`s are tabled on screen and dropped — not supported.
1.1 **Calibration selection** — three `st.selectbox`es pick 3 names from the pool, order
   matters. `core.perform_triangle_qc` builds the triangle, reports the % of
   Polygons/LineStrings intersecting it, warns below 25% (distortion risk).
1.2 **Optional class split** — `core.make_classes_unique` turns e.g. `T-Cell` into
   `T-Cell_001…`, one name per shape, for single-cell collection. Stores
   `original_classification_name` so repeated runs stay idempotent, and rewrites the
   nested `classification` dict via `utils.update_classification_column`.
2. **Plate layout** — plate type (384/96), margin, row step, column step feed
   `utils.create_list_of_acceptable_wells`. Two views: `default` (well names, allowed ones
   green) and `samples` (classes auto-placed into allowed wells, optionally randomized).
   "Confirm and use this plate layout" → `utils.dataframe_to_saw_dict` →
   `core.load_and_QC_SamplesandWells`.
2.3 **Custom samples-and-wells upload** — overrides the generated layout.
   `utils.parse_dictionary_from_file` reads a `.txt`/`.json` containing a **Python dict
   literal** and `ast.literal_eval`s it (trailing commas fine, `//` comments not).
   Returns `{}` on any parse failure. Sets `use_plate_wells = False`.
3. **Process** — `core.create_collection`:
   - `geometry.simplify(1)` then `utils.extract_coordinates` (Polygon exterior, or
     LineString coords; anything else stops).
   - `Collection(calibration_points=calib_array)` with
     `orientation_transform = [[1, 0], [0, -1]]` — **flips Y**, because QuPath image
     coordinates grow downward and the LMD stage does not.
   - One `new_shape` per row whose `classification_name` is a key in the saw dict; others
     are logged as skipped.
   - Outputs a zip: `<stem>.xml`, `<stem>_384_wellplate.csv`, `samples_and_wells.json`,
     `<stem>_processed.geojson` (sanitised for QuPath re-import), `collection.png` QC
     image, and the session log.

**Extra #1** (below the main flow): generates a QuPath `classes.json` from two lists of
categoricals × replicate count, cycling 6 hard-coded colours as Java signed ints.

## Session state keys

Initialised in the block at the top of `streamlit_app.py`. Any new key belongs here too.

| Key | Holds |
| --- | --- |
| `session_id` | uuid4 string, shown to the user for bug reports, names the log in the zip |
| `log_file_path` | temp `.log` path; loguru sink, shipped inside the download zip |
| `view_mode` | `'default'` \| `'samples'` — which plate table is rendered |
| `gdf` | the working GeoDataFrame (points removed, `classification_name` added) |
| `available_points_dict` | `{name: [x, y]}` calibration-point pool from the geojson |
| `calibs` | `[name1, name2, name3]` selected calibration point names, order matters |
| `calib_array` | 3×2 numpy array of the selected points, passed to `py-lmd` |
| `saw` | samples-and-wells dict `{class_name: well}` |
| `use_plate_wells` | True if `saw` came from the plate builder, False if uploaded |
| `file_name` | uploaded geojson filename; change of name triggers reprocessing |
| `plate_df` | the displayed plate DataFrame |
| `plate_gen_params` | dict of plate/margin/step/randomize; change triggers regeneration |
| `show_saw_uploader` | whether the custom-saw uploader is visible |
| `xml_content`, `csv_content`, `zip_buffer` | processed outputs held for download |

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

- **3-space indentation** dominates `streamlit_app.py`, `core.py`, `utils.py`; a few
  functions (`load_and_QC_geojson_file`'s point loop, `dataframe_to_saw_dict`,
  `perform_triangle_qc` internals) are 4-space. Match the block being edited.
- Every meaningful step logs via loguru **and** surfaces to the user via `st.*`. The log
  file goes into the download zip, so log lines are part of the support story.
- `ruff` configured in `pyproject.toml`: line-length 120, target py311, double quotes,
  google docstring convention, `E501` ignored.
- No test suite in the repo (pytest was removed in `0530833`), though `pytest` is still a
  declared dependency.

## Known quirks and open issues

Not scope creep — recorded so nobody rediscovers them, and so a fix is a deliberate choice.

- **`geopandas.read_file` tags QuPath GeoJSON as `EPSG:4326`.** The coordinates are image
  pixels, so the CRS is meaningless: every `.area` / `.distance` / `.centroid` call runs
  against a geographic projection and emits "Geometry is in a geographic CRS. Results from
  'area' are likely incorrect". Harmless today because nothing measures area; blocking for
  anything area- or distance-based. Fix is `crs = None` on read (`ROADMAP.md` Phase 0).
- `core.load_and_QC_SamplesandWells` validates wells against a **hard-coded 384** grid
  (`utils.py:141`), so a well like `H20` passes even when the user picked a 96-well plate.
- The plate CSV in the zip is always named `<stem>_384_wellplate.csv`
  (`streamlit_app.py:301`) even for 96-well runs; `utils.sample_placement` itself does
  respect the chosen plate type.
- `core.create_collection` writes the QC image to `./TheCollection.png`
  (`core.py:244`), and Extra #1 writes `./classes.json`, both into the process working
  directory rather than a temp dir. Shared-server side effect; also means concurrent users
  can overwrite each other.
- `utils.create_dataframe_samples_wells` zips classes to wells with `strict=False`, so when
  there are more classes than allowed wells the surplus classes are silently unplaced after
  the "More classes than allowed wells" warning.
- Class→well assignment iterates a `set` of class names, so ordering is not stable between
  runs even with randomize off.
- `utils.parse_dictionary_from_file` returns `{}` on a parse error and only logs it; the
  caller then reports "loaded and checked" for an empty dict. The `#TODO` in
  `load_and_QC_SamplesandWells` about typos causing index errors is the same area.
- Missing classes (in geojson, absent from saw) `st.error` but deliberately do not stop —
  the `st.stop()` is commented out at `core.py:157`.
- `ruff check .` currently reports **32 findings** (12 W291, 9 W293, 3 I001, plus B007,
  BLE001, C416, D103, D205, D212, F401, UP015). Pre-existing baseline, not caused by
  current work.
- Extra #1 writes `classes.json` to the repo root at runtime; `.gitignore` does not cover
  it, so it shows up as an untracked file after any local run of that feature.
  (`assets/classes.json` is a tracked example and unrelated.)
