"""Canonical data model that both workflows produce and the export path consumes.

See ROADMAP.md. The important idea is `group_key`: the unit that maps to exactly one
well. The legacy workflow sets it to the class name, exploded classes set it per shape,
and the cell workflow will set it to class + replicate. Well assignment, plate QC and
export therefore need only one rule.
"""

from dataclasses import dataclass, field
from typing import Any

import geopandas
import numpy

# Columns the export path relies on. Everything else in the frame is passthrough
# from QuPath and is carried along for the re-importable GeoJSON.
SHAPE_ID = "shape_id"
CLASS_NAME = "classification_name"
REPLICATE = "replicate"
GROUP_KEY = "group_key"
WELL = "well"

CANONICAL_COLUMNS = (SHAPE_ID, CLASS_NAME, REPLICATE, GROUP_KEY, WELL, "geometry")


@dataclass
class CollectionPlan:
    """A fully-decided collection: which shape goes into which well, and how.

    `shapes` holds every candidate shape. Rows with no `well` are not cut — they are kept
    so the app can tell the user what was left out instead of silently dropping it.
    """

    shapes: geopandas.GeoDataFrame
    calibration_names: list[str]
    calibration_array: numpy.ndarray
    workflow: str
    source_file: str | None = None
    session_id: str | None = None
    pixel_size_um: float | None = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def selected(self) -> geopandas.GeoDataFrame:
        """Shapes that will be cut, in the order they were loaded."""
        return self.shapes[self.shapes[WELL].notna()]

    @property
    def skipped(self) -> geopandas.GeoDataFrame:
        """Shapes with no well assigned; reported to the user, never cut."""
        return self.shapes[self.shapes[WELL].isna()]

    @property
    def wells_used(self) -> list[str]:
        """Wells that will receive tissue, sorted."""
        return sorted(set(self.selected[WELL]))

    def provenance(self) -> dict[str, Any]:
        """Everything that determined the output, for the download bundle."""
        return {
            "workflow": self.workflow,
            "source_file": self.source_file,
            "session_id": self.session_id,
            "pixel_size_um": self.pixel_size_um,
            "calibration_points": {
                name: [float(x), float(y)]
                for name, (x, y) in zip(self.calibration_names, self.calibration_array, strict=True)
            },
            "parameters": self.params,
            "shapes_total": int(len(self.shapes)),
            "shapes_selected": int(len(self.selected)),
            "shapes_skipped": int(len(self.skipped)),
            "groups": int(self.selected[GROUP_KEY].nunique()),
            "wells_used": self.wells_used,
        }


def plan_from_class_wells(
    gdf: geopandas.GeoDataFrame,
    samples_and_wells: dict[str, str],
    calibration_names: list[str],
    calibration_array: numpy.ndarray,
    *,
    source_file: str | None = None,
    session_id: str | None = None,
    params: dict[str, Any] | None = None,
) -> CollectionPlan:
    """Build a plan for the legacy workflow: one class is one sample is one well.

    Exploded classes need no special handling here — explosion already rewrote
    `classification_name` per shape, so each exploded shape becomes its own group.
    """
    shapes = gdf.copy()
    shapes[SHAPE_ID] = shapes["id"] if "id" in shapes.columns else shapes.index.astype(str)
    shapes[REPLICATE] = None
    shapes[GROUP_KEY] = shapes[CLASS_NAME]
    shapes[WELL] = shapes[GROUP_KEY].map(samples_and_wells)

    return CollectionPlan(
        shapes=shapes,
        calibration_names=list(calibration_names),
        calibration_array=calibration_array,
        workflow="legacy",
        source_file=source_file,
        session_id=session_id,
        params=params or {},
    )
