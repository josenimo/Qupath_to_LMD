"""Turning a CollectionPlan into the files a scientist downloads."""

import contextlib
import io
import json
import os
import tempfile
import warnings
import zipfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import geopandas
import numpy
from lmd.lib import Collection, tsp_hilbert_solve
from loguru import logger

from qupath_to_lmd.geojson import extract_coordinates, sanitize_for_qupath
from qupath_to_lmd.model import WELL, CollectionPlan
from qupath_to_lmd.plate import placement_dataframe

# QuPath image coordinates grow downward; the LMD stage does not. This flip is why the
# collection lands the right way up, and it must not change without checking a real cut.
ORIENTATION_TRANSFORM = numpy.array([[1, 0], [0, -1]])

DEFAULT_SIMPLIFY_TOLERANCE = 1.0

# Order of the Hilbert curve used to shorten the cut path. py-lmd suggests at least 4 for a
# 1x1 mm area and 7 for a whole slide; 7 gave the shortest paths at every size measured.
HILBERT_ORDER = 7

class PathOrder(str, Enum):
    """The order shapes are written to the XML, which is the order the LMD cuts them.

    Stage movement between shapes is a leading cause of cutting misalignment, so the default
    is the option that minimises it rather than the one that preserves historical output
    (`decisions.md` 047, 052, 053).
    """

    HILBERT = "hilbert"
    GROUPED = "grouped"
    NONE = "none"


DEFAULT_PATH_ORDER = PathOrder.HILBERT


def order_for_cutting(
    selected: geopandas.GeoDataFrame, mode: PathOrder, hilbert_p: int = HILBERT_ORDER
) -> numpy.ndarray:
    """Positional order in which to write shapes.

    `NONE` keeps the order the shapes were loaded in. `GROUPED` puts all shapes for a well
    together, so the collector moves once per well instead of once per shape. `HILBERT` does
    that and also shortens the cut path within each well, using py-lmd's own solver.

    Wells are visited in plate order (row then column), so the collector sweeps rather than
    jumping about.
    """
    if mode is PathOrder.NONE:
        return numpy.arange(len(selected))

    wells = selected[WELL].to_numpy()
    order: list[int] = []
    for well in sorted(set(wells), key=lambda name: (name[0], int(name[1:]))):
        positions = numpy.flatnonzero(wells == well)
        if mode is not PathOrder.GROUPED and len(positions) > 2:
            subset = selected.iloc[positions]
            centroids = subset.geometry.centroid
            points = numpy.c_[centroids.x.to_numpy(), centroids.y.to_numpy()]
            positions = positions[_solve_within_well(points, mode, hilbert_p)]
        order.extend(int(position) for position in positions)

    return numpy.array(order)


def _solve_within_well(points: numpy.ndarray, mode: PathOrder, hilbert_p: int) -> numpy.ndarray:
    """Shortest-ish visiting order for one well's shapes, using py-lmd's Hilbert solver.

    py-lmd also ships a greedy nearest-neighbour solver. It is not offered: it needs
    `umap-learn`, which costs ~354 MB of JIT on first use and is reinstalled on every cold
    boot, in exchange for about 8% shorter travel (`decisions.md` 053).

    The solver prints progress to stdout, which is quietened here — the useful numbers are
    logged by the caller instead.
    """
    with contextlib.redirect_stdout(io.StringIO()), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        solved = tsp_hilbert_solve(points, p=hilbert_p)

    order = numpy.asarray(solved).ravel()
    if sorted(order.tolist()) != list(range(len(points))):
        # Never seen, but a solver returning anything other than a permutation would silently
        # drop or duplicate shapes, so fall back rather than cut the wrong thing.
        logger.error(f"{mode.value} solver returned {len(order)} indices for {len(points)} shapes; not reordering")
        return numpy.arange(len(points))
    return order


def path_stats(selected: geopandas.GeoDataFrame, order: numpy.ndarray) -> tuple[float, int]:
    """Total distance the stage travels between shapes, and how often the collector moves."""
    if len(order) < 2:
        return 0.0, 0
    ordered = selected.iloc[order]
    centroids = ordered.geometry.centroid
    points = numpy.c_[centroids.x.to_numpy(), centroids.y.to_numpy()]
    length = float(numpy.linalg.norm(numpy.diff(points, axis=0), axis=1).sum())
    wells = ordered[WELL].to_numpy()
    return length, int((wells[:-1] != wells[1:]).sum())


@dataclass
class CollectionResult:
    """The artefacts of one export, and what the cut path looks like."""

    xml: str
    csv: str
    image_path: str
    n_shapes: int
    n_vertices: int
    path_length_px: float = 0.0
    collector_moves: int = 0
    baseline_path_length_px: float = 0.0
    baseline_collector_moves: int = 0


def build_collection(
    plan: CollectionPlan,
    samples_and_wells: dict[str, str],
    simplify_tolerance: float = DEFAULT_SIMPLIFY_TOLERANCE,
    plate: str = "384",
    path_order: PathOrder = DEFAULT_PATH_ORDER,
) -> CollectionResult:
    """Build the LMD collection from a plan and render it to XML, CSV and a QC image.

    Args:
        plan: the decided collection. Only shapes with a well are cut.
        samples_and_wells: the confirmed plate scheme, used for the placement CSV. It is
            the layout the user signed off on, so it is recorded as such even where a
            class turned out to have no shapes.
        simplify_tolerance: Douglas-Peucker tolerance in image pixels. The outline may move
            by up to this much; larger values mean fewer vertices and faster cutting.
        plate: plate type, for the placement CSV.
        path_order: the order shapes are written in, which is the order the LMD cuts them.
            Defaults to `HILBERT`, which minimises stage movement without greedy's
            memory cost.
    """
    selected = plan.selected
    if selected.empty:
        raise ValueError("No shapes have been assigned to a well, so there is nothing to cut.")

    logger.info(f"Building collection from {len(selected)} shapes, simplify tolerance {simplify_tolerance}px")

    collection = Collection(calibration_points=plan.calibration_array)
    collection.orientation_transform = ORIENTATION_TRANSFORM

    order = order_for_cutting(selected, path_order)
    baseline_length, baseline_moves = path_stats(selected, numpy.arange(len(selected)))
    length, moves = path_stats(selected, order)
    logger.info(
        f"Cut path {path_order.value}: {length:,.0f}px and {moves} collector moves "
        f"(unordered: {baseline_length:,.0f}px, {baseline_moves} moves)"
    )

    coordinates = selected.geometry.simplify(simplify_tolerance).apply(extract_coordinates)
    for position in order:
        index = selected.index[position]
        collection.new_shape(coordinates[index], well=selected.at[index, WELL])

    n_vertices = int(sum(len(coords) for coords in coordinates))
    logger.info(f"Added {len(selected)} shapes, {n_vertices} vertices")

    image_path = str(Path(tempfile.mkdtemp(prefix="qupath_to_lmd_")) / "collection.png")
    collection.plot(save_name=image_path)

    xml = _collection_to_xml(collection)
    csv = placement_dataframe(samples_and_wells, plate=plate).to_csv(index=True)

    logger.success("Collection built")
    return CollectionResult(
        xml=xml,
        csv=csv,
        image_path=image_path,
        n_shapes=len(selected),
        n_vertices=n_vertices,
        path_length_px=length,
        collector_moves=moves,
        baseline_path_length_px=baseline_length,
        baseline_collector_moves=baseline_moves,
    )


def _collection_to_xml(collection: Collection) -> str:
    """py-lmd only writes to a path, so round-trip through a temporary file."""
    handle, path = tempfile.mkstemp(suffix=".xml", text=True)
    try:
        collection.save(path)
        with os.fdopen(handle, "r") as file:
            return file.read()
    finally:
        os.remove(path)


def build_bundle(
    plan: CollectionPlan,
    result: CollectionResult,
    samples_and_wells: dict[str, str],
    plate: str = "384",
    log_path: str | None = None,
) -> io.BytesIO:
    """Zip up everything needed to run, check and reproduce the collection."""
    stem = Path(plan.source_file).stem if plan.source_file else "collection"
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "a", zipfile.ZIP_DEFLATED, False) as archive:
        archive.writestr(f"{stem}.xml", result.xml)
        archive.writestr(f"{stem}_{plate}_wellplate.csv", result.csv)
        archive.writestr("samples_and_wells.json", json.dumps(samples_and_wells, indent=4))
        archive.writestr("provenance.json", json.dumps(plan.provenance(), indent=4))

        with tempfile.NamedTemporaryFile(suffix=".geojson") as temporary:
            with warnings.catch_warnings():
                # Deliberate: QuPath pixel coordinates have no CRS, and QuPath does not want one.
                warnings.filterwarnings("ignore", message=".*'crs' was not provided.*")
                sanitize_for_qupath(plan.shapes).to_file(temporary.name, driver="GeoJSON")
            archive.write(temporary.name, f"{stem}_processed.geojson")

        archive.write(result.image_path, "collection.png")

        if log_path and Path(log_path).exists():
            archive.write(log_path, f"log_{plan.session_id}.log")

    logger.success("Download bundle assembled")
    return buffer
