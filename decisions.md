# decisions.md

Append-only log of decisions about this app. **Never edit or delete an entry.** If a
decision changes, add a new entry and state which number it supersedes. Newest at the
bottom.

Entry template:

```
## NNN — <short title>
**Date:** YYYY-MM-DD · **Status:** active | superseded by NNN
**Decision:** what we do.
**Why:** the reasoning, including what we gave up.
**Alternatives rejected:** and why.
```

---

## 001 — Claude branches and commits; Jose pushes and opens the PR
**Date:** 2026-08-26 · **Status:** active
**Decision:** Claude works on a branch cut from `dev` and commits there. Claude never runs
`git push` and never opens a PR. Jose pushes and opens the PR into `dev`, every time.
**Why:** Jose stays the gate between local work and anything that reaches the shared
repo or the deployed app. Reviewing a local branch is cheap; unwinding a pushed branch is not.
**Alternatives rejected:** Claude pushing to a feature branch and letting Jose review the
PR — still puts code on the remote without a human having read it first.

## 002 — Manual verification in the running app is required before every push
**Date:** 2026-08-26 · **Status:** active
**Decision:** No feature is finished until Jose has opened the app locally and exercised
the change by hand. Claude's job is to smoke-test first and then hand over explicit
numbered manual test steps.
**Why:** Output correctness here means "the laser cut the tissue the scientist meant",
which no automated check in this repo currently asserts. A human clicking through the real
UI is the only end-to-end test that exists.
**Alternatives rejected:** Trusting a boot-without-exception smoke test — it catches import
errors and nothing about coordinates, wells, or plate layout.

## 003 — Warn rather than block
**Date:** 2026-08-26 · **Status:** active
**Decision:** When the app detects something suspicious, it explains the consequence and
lets the user proceed. `st.stop()` is reserved for states where no meaningful output is
possible at all, and each new one must be justified in this log.
**Why:** Users are scientists with a legitimate need for unusual setups, working on
irreplaceable samples. A hard stop on a heuristic ("only 20% of shapes are inside the
calibration triangle") blocks valid work; a clear warning preserves both agency and
informed consent. Transparency over paternalism.
**Alternatives rejected:** Strict validation gates — would make the app unusable for the
edge cases that motivate it, and pushes users to hand-edit XML instead.

## 004 — QuPath GeoJSON is the only input format
**Date:** 2026-08-26 · **Status:** active
**Decision:** All processing starts from a QuPath-exported GeoJSON FeatureCollection.
Alternative inputs (coordinate CSVs, raw XML, images) require a new entry here first.
**Why:** One input contract means one set of assumptions about geometry, classification and
calibration points to get right, and one thing to document for users. It is also what
QuPath already exports well.
**Alternatives rejected:** Accepting XML or CSV for re-editing existing collections —
plausible future work, but it would fork the QC logic and the coordinate handling.

## 005 — Two documents: facts.md (living) and decisions.md (append-only)
**Date:** 2026-08-26 · **Status:** active
**Decision:** `facts.md` records what is true about the app and is corrected in place when
it goes stale. `decisions.md` records why, is append-only, and later entries supersede
earlier ones by number. Working rules for Claude live in `CLAUDE.md`.
**Why:** Separating the two keeps facts trustworthy (no stale contradictions to sift) while
keeping reasoning auditable (no silently rewritten history).
**Alternatives rejected:** A single changelog-style document — mixes "what is" with "what
we decided", and one of the two always ends up wrong.

## 006 — No pytest suite for now; verification is manual plus scratch scripts
**Date:** 2026-08-26 · **Status:** active
**Decision:** The repo has no test suite (removed in commit `0530833`) and Claude will not
reintroduce one unprompted. Logic changes are verified with throwaway scripts in the
scratchpad that import from `src/qupath_to_lmd/` and use `mock_streamlit.patch_streamlit()`,
plus the manual UI pass from 002.
**Why:** Records the existing state rather than quietly reversing a deliberate removal.
Most of the code is entangled with `st.session_state`, so tests would either be shallow or
demand a refactor that is its own project.
**Alternatives rejected:** Adding pytest back as part of the next feature branch — that
buries a project-shaping decision inside an unrelated diff. Worth doing, worth doing
openly, and worth doing after the library layer takes explicit arguments (see `CLAUDE.md`
rule 5).

## 007 — Dependencies must land in both pyproject.toml and requirements.txt
**Date:** 2026-08-26 · **Status:** active
**Decision:** Any dependency change edits `pyproject.toml` and then regenerates
`requirements.txt` via `uv pip compile pyproject.toml -o requirements.txt`, in the same commit.
**Why:** Streamlit Community Cloud installs from `requirements.txt`; local dev resolves
from `pyproject.toml`/`uv.lock`. Touching only one produces a change that works on Jose's
machine and 500s in production.
**Alternatives rejected:** Dropping `requirements.txt` and having the cloud read
`pyproject.toml` — not reliably supported on the current Community Cloud runtime, and not
worth risking the deployed app to find out.

## 008 — Rules live in CLAUDE.md; features come before test infrastructure
**Date:** 2026-08-26 · **Status:** active
**Decision:** Confirmed by Jose. The working rules stay in `CLAUDE.md` so they auto-load
into every session rather than needing to be pointed at. And feature development takes
priority over building test infrastructure: Claude does not propose or add a test suite
until Jose asks. Reaffirms 006 rather than superseding it.
**Why:** Auto-loading means the rules apply by default instead of on remembering. On
testing, the app's value right now is in the features scientists are waiting on; the manual
UI pass from 002 is the accepted verification in the meantime.
**Alternatives rejected:** A `rules.md` Claude has to be handed each session — same content,
worse odds of being read.

## 009 — Two workflows converge on one CollectionPlan
**Date:** 2026-08-26 · **Status:** active
**Decision:** The app gets two entry workflows — *legacy* (manual annotations, one QuPath
class = one sample = one well, with the optional explode into per-shape wells) and *cells*
(segmentation shapes, class → replicates → budgeted selection). Both produce the same
object: a GeoDataFrame of shapes carrying `class_name`, `replicate`, `group_key` and `well`,
plus calibration points, µm/px and a parameter/provenance record. QC, smoothing, path
ordering and XML export live downstream of that object and are shared. See `ROADMAP.md`.
**Why:** `group_key` — the unit that maps to exactly one well — unifies all three
behaviours: legacy is `class_name`, explode is `shape_id`, new workflow is
`class_name + replicate`. One well-assignment and export path, three user experiences,
no duplicated coordinate handling (the part where a bug means cutting the wrong tissue).
**Alternatives rejected:** Two parallel apps or pages each with their own export — would
double the coordinate/QC logic, and the two copies would drift.

## 010 — Export exposes smoothing tolerance and cut-path optimization
**Date:** 2026-08-26 · **Status:** active
**Decision:** The two user-facing export parameters are the smoothing/simplification
tolerance and the cut-path optimization mode (`none`/`greedy`/`hilbert`). Tolerance is
specified in **µm**, not pixels. Both ship with a recommended default and an on-screen
explanation of the reasoning.
**Why:** Tolerance in pixels — the current hard-coded `simplify(1)` — silently changes
meaning with objective magnification; µm is the unit the instrument works in. The
recommendation is anchored to the cutting laser's positioning precision: below it,
simplification cannot change which tissue is cut, it only stops the stage tracing vertices
that do not matter. Path optimization cuts stage travel and focus drift, which is
negligible for 20 annotations and significant for 2000 cells. py-lmd already provides
`tsp_greedy_solve` / `tsp_hilbert_solve`, but only wires them into the mask-based
`SegmentationLoader`, not the `Collection` path this app uses — so we order shapes
ourselves using its solvers.
**Alternatives rejected:** Exposing shape dilation instead — deferred, see 013 and
`ROADMAP.md` open question 3. Exposing a minimum shape area — a per-class stats
concern (Phase 2), not an export parameter.

## 011 — The user supplies µm per pixel; the app cross-checks it
**Date:** 2026-08-26 · **Status:** active
**Decision:** µm/px is a required user input before any area figure is displayed. Where
QuPath `measurements` are present, the app computes the implied scale and **warns** on a
mismatch, without overwriting the entered value.
**Why:** Jose's call: an explicit input is unambiguous and auditable, and the scale is a
property of the acquisition that the user is responsible for knowing. Auto-filling a
derived number invites accepting it unread. The cross-check is nearly free and catches the
expensive typo — a 10× error turns an area budget into the wrong experiment.
**Alternatives rejected:** Auto-derive with override (recommended, not chosen). Working in
px² — area budgets in pixels² are not a quantity anyone can reason about experimentally.
**Note for implementers:** the derivation works because QuPath writes `Cell: Area` in µm²
while GeoJSON coordinates stay in pixels; `sqrt(Cell:Area / polygon_area_px)` gave
0.3467 µm/px with 0.2% spread across all 121 cells of `Single_cells.geojson`.

## 012 — Cell selection defaults to maximum spatial spread
**Date:** 2026-08-26 · **Status:** active
**Decision:** The default selection mode maximises spatial dispersion of the chosen cells
within a class, implemented as greedy farthest-point sampling. Random selection is the
alternative. Spread is the default regardless of whether adjacent cells are allowed, and
interacts with that constraint rather than being replaced by it.
**Why:** A replicate drawn from one corner of the tissue measures that corner, not the
class. Spreading averages over local biological gradients, staining artefacts and niche
effects, which is what a scientist means when they ask for N cells of a type. The
constraint case composes naturally — a farthest-point sampler already avoids neighbours.
**Alternatives rejected:** Ranking by a QuPath measurement as the primary mode — deferred,
supported by the data and worth building later; `selection.py` must not foreclose it
(`ROADMAP.md` open question 4). Random as the default — reproducible but noisier for the
same number of cells.

## 013 — Adjacency is judged on pre-dilation geometry
**Date:** 2026-08-26 · **Status:** active
**Decision:** When the user disallows adjacent cells, the constraint is that no two
selected shapes touch or overlap, evaluated on the original QuPath geometry **before** any
smoothing or dilation the export path applies. No minimum-gap parameter.
**Why:** The raw segmentation is the ground truth about which cells are neighbours; a
constraint measured on shapes the app has already modified would depend on export settings
chosen later. No extra input to explain, and QuPath's expansion-based segmentation produces
exactly-touching cells, which this catches.
**Alternatives rejected:** A user-set minimum gap in µm — more control, another parameter
to explain, and no evidence yet that users want it. Consequence to keep in view: if a
dilation step is ever added, dilated neighbours could overlap even with this constraint on,
and the app would have to say so on screen.

## 014 — Phase 0 is gated on byte-identical XML
**Date:** 2026-08-26 · **Status:** active
**Decision:** Before the Phase 0 refactor, capture XML output from current `master` for
both demo GeoJSONs as golden files, and require the restructured code to reproduce them
byte-for-byte on the same inputs.
**Why:** Phase 0 moves the coordinate and export code with no intended behaviour change,
and there is no test suite (006/008). Byte equality is the strongest available assertion
that a pure refactor stayed pure, and it costs one afternoon rather than a test framework.
It is also the only check that would catch a silent change to the Y-flip, calibration
ordering or simplification.
**Alternatives rejected:** Relying on the manual UI pass alone — a human clicking through
the app cannot see that a coordinate shifted by a pixel.

## 015 — Replicates are spread and interleaved
**Date:** 2026-08-26 · **Status:** active
**Decision:** Confirmed by Jose. Replicates of a class each span the class's full spatial
extent and interleave with one another. The class is **not** partitioned into one spatial
block per replicate.
**Why:** It makes replicates statistical repeats of the same population rather than samples
of different regions, which is what a replicate is normally taken to mean. Regional
comparison remains expressible by the user as separate classes in QuPath.
**Alternatives rejected:** Spatial partitioning — appropriate if the question is regional
variation, but the wrong default and it silently confounds replicate with location.

## 016 — Spatial binning, not farthest-point, is the default selection algorithm
**Date:** 2026-08-26 · **Status:** active · **supersedes the algorithm named in 012**
**Decision:** The spread default is implemented as k-means spatial binning of cell
centroids, taking the cell nearest each bin centre; for *r* replicates, replicate *i* takes
the *i*-th nearest cell in each bin. 012's intent (spread by default) stands; its named
implementation (greedy farthest-point sampling) is replaced.
**Why:** Prototyped both on the 121 cells of `Single_cells.geojson`, selecting 20.
Farthest-point gave the best separation (min pairwise gap 101 px vs 26 px random) but
**biased selection to the tissue rim** — mean distance from the tissue edge 67 px against a
population mean of 78 px — because maximising separation means racing to the extremes. That
is a representativeness bug in a feature whose entire purpose is representativeness.
Binning gave min gap 33 px at depth 72 px: most of the separation, near-population-average
depth, visibly even coverage. It also produces 015's interleaved replicates by construction
(verified: 3 replicates × 12 bins → 12/12/12, zero overlap) instead of needing a second
mechanism.
**Alternatives rejected:** Farthest-point (edge bias, above). Random as default (unbiased
depth, but clumps — min gap 26 px, with visibly touching pairs among the 20 selected).
**Consequence:** binning does not by itself guarantee non-adjacency, so 013's constraint is
load-bearing and applies on top: if a bin's nearest candidate touches an already-selected
shape, take that bin's next-nearest.

## 017 — Live selection preview
**Date:** 2026-08-26 · **Status:** active
**Decision:** Jose's idea, adopted. The cell workflow draws the shapes as parameters are
set — unselected in grey, selected coloured by replicate, calibration triangle overlaid —
using a static matplotlib/geopandas render redrawn on each change. One plotting function
serves the Phase 2 class view, the Phase 4 selection preview and the export QC image,
replacing the separate `py-lmd` plot. Above a shape-count threshold it falls back to
plotting centroids instead of polygons. No interactive plotting library.
**Why:** It is the difference between choosing selection parameters and guessing at them,
and it is the most direct expression of the transparency goal in 003 — clumping, edge bias
or a starved replicate become visible rather than inferred. Jose asked not to do it if it
got complicated; measured, it does not: a two-layer render is ~0.35 s at 10k shapes, 1.8 s
at 50k, 7.6 s at 200k, and the centroid fallback is 0.14 s at 200k. Streamlit already
reruns on every widget change, so redraw-on-change needs no extra machinery.
**Alternatives rejected:** Interactive pan/zoom via plotly or pydeck — genuinely nicer, but
a new dependency plus browser memory risk on Community Cloud, for a benefit the static
render mostly already delivers. Revisit if users ask to zoom.

## 018 — Smoothing tolerance in µm, warned against shape size
**Date:** 2026-08-26 · **Status:** active · refines 010
**Decision:** Smoothing tolerance is specified in µm with a default of 0.5 µm, displayed as
a percentage of the median shape diameter, and warns when it exceeds roughly 2% of that.
No instrument-precision figure is required.
**Why:** 010 anchored the default to the cutting laser's precision, which needs a number
nobody had to hand. Anchoring to shape size instead is self-calibrating and better
targeted: the same 0.5 µm is negligible on a 200 µm mini-bulk annotation and material on a
10 µm cell, and the warning fires exactly in the case that matters. The underlying problem
010 identified is unchanged — `simplify(1)` is one *pixel*, so its physical effect scales
with magnification (0.35 µm at 0.347 µm/px, ~1.7 µm on a 4× overview) and the number
therefore means nothing physical on its own.
**Alternatives rejected:** Blocking on the LMD7 spec figure — would stall Phase 5 on a
detail the shape-relative warning handles better anyway.

## 019 — Smoothing default stays simplify(1) in pixels; explain and let users decide
**Date:** 2026-08-26 · **Status:** active · **supersedes 018, and the µm part of 010**
**Decision:** Jose's call. The simplification tolerance keeps its current value and unit —
shapely Douglas-Peucker, **1 pixel** — and is exposed with a plain explanation of what it
does. No µm conversion, no shape-relative warning threshold. The user decides.
**Why:** The default is battle-tested across 60+ users and changing it would alter output
for everyone for a theoretical gain. Explaining a parameter is the transparency this app
owes its users (003); tutoring them with a derived threshold is not. 018 was overthinking a
default that already works.
**Alternatives rejected:** 018's µm-with-warning scheme, and 010's µm framing — both
withdrawn. The observation behind them is still true (a pixel tolerance means different
physical distances at different magnifications) but it belongs in the on-screen
explanation, not in the units or in a warning.

## 020 — Multi-slide-into-one-plate deferred
**Date:** 2026-08-26 · **Status:** active
**Decision:** Jose is deferring the multiple-slides-into-one-plate question to think about.
Not designed for in the current phases; `ROADMAP.md` open question 5 keeps the context.
**Why:** It affects how well assignment is scoped (per file vs across files) and so is
better answered before Phase 3 hardens well assignment than retrofitted after — but it does
not block Phases 0–2.

## 021 — Phase 0 delivered: library split, CollectionPlan, CRS fix
**Date:** 2026-08-26 · **Status:** active
**Decision:** `core.py` and `utils.py` are deleted and replaced by `model.py`,
`geojson.py`, `plate.py`, `qc.py`, `export.py` and `extras.py`. Library functions take
explicit arguments and return report objects or raise domain exceptions; only
`streamlit_app.py` touches `st.*` and `st.session_state`. The legacy workflow runs through
`CollectionPlan`. Verified byte-identical XML and CSV across four cases per 014.
**Why:** `ROADMAP.md` Phase 0. The seam has to exist before a second workflow can hang off
it, and the `st.session_state` reads inside library code made anything untestable outside
Streamlit — the golden harness could only be written because the new functions are pure.
**Behaviour changes shipped alongside**, each a bug the refactor put in reach:
CRS mislabelling cleared; wells validated against the chosen plate rather than always 384;
plate-aware CSV filename; QC image and `classes.json` no longer written to the working
directory; unplaced surplus classes named rather than merely counted; plate layout sorted so
it is stable across reruns; `SawParseError` raised instead of silently returning `{}`; and
the MultiPolygon branch fixed, which would have raised `KeyError` for any user who had one.
`provenance.json` is now in the download bundle (009).
**Alternatives rejected:** Keeping `core.py`/`utils.py` and adding modules alongside — the
duplication would have to be unpicked later, and the point of Phase 0 is that Phase 1
inherits one clear structure. Indentation normalises to 4-space as a side effect of the
files being new, so the codebase is now internally consistent (`CLAUDE.md` rule 9).

## 022 — The golden harness lives in the repo and gating on it is mandatory
**Date:** 2026-08-26 · **Status:** active
**Decision:** The Phase 0 throwaway harness becomes `tools/golden_harness.py` with
reference output committed in `tools/golden/` (8 files, ~220 KB). `CLAUDE.md` rule 6 now
requires `check` to pass before committing any change that touches geometry, calibration,
well assignment or export. Re-blessing with `capture` is allowed only when output is meant
to change, must be stated in the commit message, and the files are never hand-edited.
**Why:** It is the only check that can see the failure mode that matters here — a
coordinate shifted by a pixel, an inverted Y flip, shapes added in a different order. All
of those leave the app looking perfectly healthy and cut the wrong tissue. Phase 4's
selection engine will change which shapes are chosen, so having a fixed reference for
*how* a chosen shape is rendered becomes more valuable, not less. Committing the reference
also means it traces to a specific version rather than to a scratchpad that gets cleaned.
**Verified both directions:** the committed goldens are byte-identical to output captured
from the pre-Phase-0 code, and replacing `ORIENTATION_TRANSFORM` with the identity matrix
makes all four XML comparisons differ and `check` exit 1. A gate that cannot go red is
worthless, so this was tested explicitly.
**Alternatives rejected:** Leaving it in the scratchpad — it would be gone next session and
the discipline would not survive. Reintroducing pytest to host it — reverses 006/008; the
harness is a script, and it can be moved under pytest later if a suite ever arrives.
**Known gaps, recorded rather than papered over:** no LineString case (no demo file has
one), nothing covering the UI or the QC/warning behaviour, and it proves "unchanged" rather
than "correct" — a pre-existing coordinate bug is faithfully preserved.

## 023 — Phase 1 delivered: router, shared steps, and the image-scale input
**Date:** 2026-08-26 · **Status:** active
**Decision:** `streamlit_app.py` becomes session init plus a router. Steps 1–3 (upload,
workflow choice, calibration) are shared; the router then dispatches to `ui_legacy.render`
or `ui_cells.render`. The `ui_*` modules are the UI layer and may own `st.session_state`;
the library modules stay pure. `CLAUDE.md` rule 5 is rewritten around that boundary,
replacing its stale references to `core.py` and `utils.py`.
**Why:** `ROADMAP.md` Phase 1. The legacy workflow keeps its order and wording so existing
users are not disoriented, while the second workflow gets somewhere to live.
**Verified:** golden harness clean — all 8 artefacts identical, so the legacy path is
untouched and now frozen. Router detection exercised against both demo files with stubbed
widgets: `Single_cells.geojson` (121 cells vs 7 annotations) defaults to cells,
`TD_01_verysmall_mIF.geojson` to legacy, no file to legacy with no hint.

## 024 — Step numbers are parameters, not literals
**Date:** 2026-08-26 · **Status:** active
**Decision:** The `ui_shared` step functions take a `step` label used in their heading,
rather than hard-coding "Step 2". The workflows pass their own numbers.
**Why:** The two workflows reach the shared steps at different points, so a literal is
wrong for one of them. The first attempt used "Step 1.5" and "Step 1.6" to avoid
renumbering, which read worse than simply renumbering: the flow is now 1–6 in both
workflows. Renumbering does shift what "Step 2" means for returning users, which is the
cost accepted here.
**Alternatives rejected:** Hard-coding numbers per workflow by duplicating the headings —
two places to keep in sync for no benefit.

## 025 — Image scale is asked for in the cell workflow, not globally
**Date:** 2026-08-26 · **Status:** active
**Decision:** `pixel_size_step` lives in `ui_shared` (both workflows may use it) but is
only called by the cell workflow. The legacy workflow does not ask for µm/px.
**Why:** 011 requires the value before any area figure is shown; the legacy workflow shows
no area figures, so requiring it there would be a new obstacle with no benefit for the
existing users. The roadmap listed it under "shared steps", which this satisfies as shared
*code* invoked where area actually matters.
**Alternatives rejected:** Asking globally — adds a required field to a workflow that does
not need it. Putting it only in `ui_cells` — Phase 3's area budgets and any future legacy
area reporting would want it back in the shared module.

## 026 — A file with no calibration points is readable, not rejected
**Date:** 2026-08-26 · **Status:** active · supersedes the `name`-column gate added in 021
**Decision:** `read_and_qc` no longer requires a `name` column. Its absence means the file
has no named point annotations, which is reported as "no calibration points" at the
calibration step with instructions for adding them in QuPath. Only a genuinely unusable
file — no features, or no `classification` column at all — still raises `GeojsonError`.
**Why:** Jose hit this with a real QuPath 0.7.0 export of 14145 segmented cells. QuPath
omits a property entirely when no object in the export carries it, so a file without
calibration points has no `name` column, and the app rejected it with "Export as a
FeatureCollection with named calibration points" — which the user *had* done. The message
named the wrong cause and blocked a readable file, against 003. Missing calibration points
is a real problem, but the fix is in QuPath and the app should say so precisely.
**Alternatives rejected:** Keeping the hard stop with a better message — the file loads
fine, and the user can still inspect classes and plate layouts while going back to QuPath
for the points.

## 027 — Multi-class QuPath objects become one combined class
**Date:** 2026-08-26 · **Status:** active
**Decision:** A QuPath object classified with several classes exports as
`{"names": ["Tumor", "Immune cells"]}` — plural. Those names are joined with `": "` into a
single class, mirroring how QuPath displays derived classes. The count and the resulting
names are warned about on screen, saying explicitly that such objects are *neither* of
their parent classes here.
**Why:** Found in the same export: 1130 of 8537 classified cells were multi-class, and the
app read them as `None`. That polluted the class list and then crashed the plate layout,
because `sorted()` cannot compare `None` with a string. Joining is the least surprising
repair — the class reads the same as it did in QuPath — and a double-positive cell is a
genuine biological category someone may well want in its own well. Silently folding them
into one parent class would misassign tissue.
**Alternatives rejected:** Dropping them with a warning — throws away real data the user
classified deliberately. Assigning them to the first parent class — silently wrong, and the
kind of wrong that only shows up in the mass spec. Asking the user per file — a dialog for
something QuPath already has a display convention for.
**Consequence:** anything with a classification but no usable name at all is now dropped
with its own count, so `classification_name` is never `None` downstream.
