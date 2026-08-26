# Rules for developing Qupath_to_LMD

These are the rules I (Claude) follow when working on this repo. They are binding, not
suggestions. If a rule blocks me, I say so and stop rather than route around it.

Companion documents:
- `facts.md` — what is true about this app (living document, I keep it current)
- `decisions.md` — why things are the way they are (append-only log)

---

## 1. Git workflow — I branch and commit, Jose pushes and PRs

- **Never** `git push`. **Never** `gh pr create`. No exceptions, not even when asked to
  "finish up" or when a push seems obviously wanted. If a push is needed, I say
  "ready to push" and stop.
- Every change starts with a branch cut from an up-to-date `dev`:
  `git fetch origin && git checkout -b <type>/<slug> origin/dev`
- Branch naming: `feat/`, `fix/`, `refactor/`, `docs/`, `chore/` + short kebab slug.
- Never commit directly to `master` or `dev`. PRs target `dev`; `dev` → `master` is Jose's call.
- Commits are small and self-describing. Message: imperative subject ≤ 72 chars, then a
  body explaining *why* when it isn't obvious.
- Every commit ends with the trailer:
  `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`
- I only stage files I actually touched — `git add <paths>`, never `git add -A`.
- I never rewrite published history, never force-push, never `git checkout --` over
  uncommitted work that isn't mine.

## 2. Nothing is "done" until Jose has clicked through it

- Every feature or behaviour change ends with me handing over an explicit **manual test
  script**: what to run, which file to upload, which buttons to press in order, and what
  Jose should expect to see. Written as numbered steps, not prose.
- Before handing over, I smoke-test locally myself:
  ```
  uv sync
  uv run streamlit run streamlit_app.py --server.headless true --server.port 8599
  ```
  and confirm the app boots with no exception in the log. Booting is the floor, not the
  ceiling — where I can, I also exercise the changed code path directly (see rule 6).
- I use `demo_Qupath_project/TD_01_verysmall_mIF.geojson` (annotations, 4 classes) and
  `demo_Qupath_project/Single_cells.geojson` (many small shapes) as the standard fixtures,
  and `demo_Qupath_project/demo_samples_and_wells.txt` for the custom-upload path.
- I report test results honestly. If I only booted the app and did not exercise the
  feature, I say exactly that.

## 3. Scientist-facing behaviour: warn, do not block

The users are scientists cutting real, irreplaceable tissue. The app's job is to make
what will happen legible, then let them decide.

- Default to `st.warning` + a clear explanation of the consequence. Reserve `st.error` for
  states where continuing would silently produce a wrong `.xml`.
- **`st.stop()` is a last resort.** Only when continuing cannot produce a meaningful
  result at all (no geometry, no calibration points, wells that do not exist on the plate).
  Every new `st.stop()` gets a line in `decisions.md` saying why blocking beat warning.
- Never silently drop data. If shapes are excluded (MultiPolygons, unclassified objects,
  classes missing from samples-and-wells), the count and the reason go on screen, not just
  into the log.
- Quantities that affect the cut — well assignments, calibration triangle coverage,
  simplification, how many shapes made it into the collection — must be visible before the
  user downloads, not discoverable only afterwards in the log.
- Wording is plain and specific: what happened, what it means for their collection, what
  to do about it. No jargon-only messages.

## 4. GeoJSON from QuPath is the only input contract

- The input is a QuPath-exported **FeatureCollection** `.geojson`. Everything downstream
  derives from it. I do not add parallel input formats (CSV of coordinates, raw XML,
  images) without an explicit decision logged in `decisions.md`.
- Anything the app hands back that is meant to re-open in QuPath must survive a QuPath
  round-trip: no NaN-bearing columns, `classification` kept as a dict-shaped value,
  `id` / `objectType` / `classification` / `geometry` preserved (see `utils.sanitize_gdf`).
- Assume QuPath exports are messy: missing `classification`, `MultiPolygon`s, unnamed
  points, duplicate class names, `LineString`s. New code handles these explicitly rather
  than assuming the demo file's shape.
- Coordinate handling is load-bearing. I do not touch `orientation_transform`,
  the calibration-point ordering, or `geometry.simplify()` without a logged decision —
  a silent change here mis-cuts tissue and nobody notices until the mass spec.

## 5. Where code goes

- `streamlit_app.py` — UI, layout, `st.session_state` wiring, control flow. Thin.
- `src/qupath_to_lmd/core.py` — the pipeline steps: load/QC geojson, QC calibration,
  QC samples-and-wells, split classes, build the collection.
- `src/qupath_to_lmd/utils.py` — pure-ish helpers: plates, wells, dataframes, parsing,
  geometry extraction, sanitising.
- New logic goes in `core.py`/`utils.py` with a signature that takes its inputs as
  arguments; `streamlit_app.py` calls it. I do not grow `streamlit_app.py` with
  computation that could be tested outside Streamlit.
- Reaching into `st.session_state` from inside `core`/`utils` is an existing pattern, not
  a good one. New functions take explicit parameters and return values. I do not add new
  hidden `st.session_state` reads/writes in the library layer.
- Every new `st.session_state` key is initialised in the block at the top of
  `streamlit_app.py` and added to the key table in `facts.md`.

## 6. Verification

- `uv run ruff check <files-I-touched>` must be clean for those files before I commit.
  The repo has a pre-existing backlog of ruff findings (see `facts.md`) — I do not
  bulk-fix it as a side effect of a feature commit; that is its own `chore/` branch.
- There is no test suite (pytest was deliberately removed, see `decisions.md`). So for
  logic changes I write a throwaway script in the scratchpad that imports from
  `src/qupath_to_lmd/` and exercises the function on a demo geojson, and I paste the
  relevant output into my report. Throwaway scripts stay out of the repo.
- `src/qupath_to_lmd/mock_streamlit.py` (`patch_streamlit()`) lets `core`/`utils` run
  outside Streamlit — I use it for those scripts instead of standing up a fake session.

## 7. Dependencies

- `requirements.txt` is generated, and it is what Streamlit Community Cloud installs from.
  Adding or bumping a dependency means **both**: edit `pyproject.toml`, then
  `uv pip compile pyproject.toml -o requirements.txt`. A dep added to only one of the two
  breaks the deployed app while working locally.
- I propose new dependencies before adding them, with a one-line reason. The bar is high;
  this app is deployed on a free tier with a memory ceiling.

## 8. Documentation duties, every time

- **`facts.md`**: I update it in the same commit whenever I change something it describes —
  session-state keys, data contracts, pipeline behaviour, known quirks. It is memory, so I
  correct stale entries rather than appending contradictions.
- **`decisions.md`**: append-only. A new entry for every non-obvious choice: blocking vs
  warning, a data-format change, a dependency, an architectural direction, a rejected
  alternative. Never edit or delete an old entry — a later entry supersedes an earlier one
  and says so explicitly by number.
- `README.md` is user-facing: I update it when a change alters what a scientist does or
  sees, not for internal refactors.
- I do not add code comments that restate the code. Comments explain domain reasoning
  (why Y is flipped, why margin 2 on a 384 plate, why simplify tolerance 1).

## 9. Scope

- I do the task asked. I do not refactor adjacent code, rename things, reformat files, or
  "clean up while I'm here" — drive-by changes inflate the diff Jose has to review and
  hide the real change.
- If I spot a genuine bug outside scope, I finish the task, then report the bug with
  file:line and let Jose decide. Candidates already found are listed in `facts.md`.
- Style follows the surrounding file, including its indentation, even where that differs
  from my preference. `streamlit_app.py`, `core.py` and `utils.py` are largely
  **3-space indented** — I match the file I am editing rather than normalising it.
