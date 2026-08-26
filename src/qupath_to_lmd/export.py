"""Turning a CollectionPlan into the files a scientist downloads."""

import io
import json
import os
import tempfile
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy
from lmd.lib import Collection
from loguru import logger

from qupath_to_lmd.geojson import extract_coordinates, sanitize_for_qupath
from qupath_to_lmd.model import WELL, CollectionPlan
from qupath_to_lmd.plate import placement_dataframe

# QuPath image coordinates grow downward; the LMD stage does not. This flip is why the
# collection lands the right way up, and it must not change without checking a real cut.
ORIENTATION_TRANSFORM = numpy.array([[1, 0], [0, -1]])

DEFAULT_SIMPLIFY_TOLERANCE = 1.0


@dataclass
class CollectionResult:
    """The artefacts of one export."""

    xml: str
    csv: str
    image_path: str
    n_shapes: int
    n_vertices: int


def build_collection(
    plan: CollectionPlan,
    samples_and_wells: dict[str, str],
    simplify_tolerance: float = DEFAULT_SIMPLIFY_TOLERANCE,
    plate: str = "384",
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
    """
    selected = plan.selected
    if selected.empty:
        raise ValueError("No shapes have been assigned to a well, so there is nothing to cut.")

    logger.info(f"Building collection from {len(selected)} shapes, simplify tolerance {simplify_tolerance}px")

    collection = Collection(calibration_points=plan.calibration_array)
    collection.orientation_transform = ORIENTATION_TRANSFORM

    coordinates = selected.geometry.simplify(simplify_tolerance).apply(extract_coordinates)
    for index in selected.index:
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
