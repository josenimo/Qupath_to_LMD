# Glossary

One word per thing. The app, its messages and its code use these terms consistently; where
QuPath or py-lmd use a different word for the same thing, that is noted.

Written because the same object was being called a *shape*, a *polygon*, an *object* and a
*contour* in different parts of the app — sometimes within a few lines of each other.

---

## The thing being cut

**shape** — one outline that the laser will cut. This is the app's word for it, from upload
through to the XML, and it is what py-lmd calls them too (`new_shape`, `Collection.shapes`).
Counts in the interface — "8537 shapes available", "100 shapes per replicate" — always mean
this.

**object** — QuPath's word, used **only** when talking about the input file. QuPath's interface
says annotation objects, cell objects and detection objects, and its GeoJSON carries an
`objectType` field. So: *QuPath exports objects; the app reads them as shapes.* Messages about
what happened during reading say "objects" because at that point they are still QuPath's;
messages about what will be cut say "shapes".

Some objects never become shapes: unclassified ones, ones whose classification carries no usable
name, and MultiPolygons.

**polygon** — the **geometry type**, alongside `MultiPolygon` and `LineString`. Used only in that
sense. A shape usually has Polygon geometry, but "polygon" is not a synonym for "shape".

**contour** — not used. It appeared in an image caption and the README; both now say *shape*.

---

## Grouping and collection

**class** — a QuPath classification name, which in practice is a biological category:
`Tumor`, `Immune cells`. The app never invents one. An object classified with several classes
becomes a single combined class joined with `--` and sorted, e.g. `Immune cells--Tumor`.

**group** — the unit that maps to exactly one **well**, held in the `group_key` column. This is
the seam the two workflows share:

| workflow | a group is |
| --- | --- |
| annotations | one class |
| annotations, with a class exploded | one shape |
| cell segmentation | one class and replicate, e.g. `Tumor_r2` |

**replicate** — one repeat of a class, collected into its own well. Replicates of a class are
drawn from across the whole tissue and interleaved with each other, so they are statistical
repeats rather than samples of different regions.

**well** — a position on the plate, written row-then-column with no padding: `C3`, `B12`. A 384
plate is rows A–P by columns 1–24; a 96 plate is A–H by 1–12.

**margin** — how many wells to leave unused around the edge of the plate. It exists because the
LMD7 collects unreliably into the outermost wells of a 384 plate.

**collection** — everything that will be cut in one run: the shapes, their groups, their wells,
and the parameters that produced them. Written out as an `.xml` the LMD software executes.

**plan** (`CollectionPlan`) — the object holding a decided collection. Both workflows produce
one, and everything downstream — QC, smoothing, cut order, export — reads only from it.

---

## Geometry and scale

**calibration point** — one of three named point annotations that let the LMD map image
coordinates onto its stage. Three are required, they must not be collinear, and they should sit
close to the tissue being cut: a wide triangle distorts small shapes.

**pixel size** (µm/px) — how many micrometres one image pixel covers. QuPath coordinates are in
pixels while its area measurements are in µm², so the app can often derive this from the file
itself. It is only needed to express amounts as areas.

**smoothing tolerance** — how far a shape's outline may move when redundant points are removed,
in pixels. Higher values mean fewer points and a faster cut, at the cost of following the
annotation less exactly.

**cutting order** — the order shapes are written to the XML, which is the order the LMD cuts
them. Stage movement between shapes is a leading cause of cutting misalignment, so the default
groups each well together and shortens the path within it.

**neighbour** — two shapes closer than a set distance, judged on the original QuPath geometry.
Not the same as *intersecting*: QuPath's cell segmentation leaves a sub-pixel gap between
adjacent cells, so a strict intersection test finds almost none of the real neighbours.

---

## Where the terms come from

| this app | QuPath | py-lmd |
| --- | --- | --- |
| shape | object (annotation / cell / detection) | shape |
| class | classification | — |
| well | — | well / cap |
| collection | — | Collection |
| calibration point | point annotation | calibration point |
