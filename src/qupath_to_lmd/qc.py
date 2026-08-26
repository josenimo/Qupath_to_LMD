"""Quality checks. These report; they do not decide. The UI layer chooses what to do."""

from dataclasses import dataclass, field

import geopandas
import numpy
import shapely
from loguru import logger

from qupath_to_lmd.plate import acceptable_wells


@dataclass
class TriangleReport:
    """How much of the tissue sits inside the calibration triangle."""

    calibration_array: numpy.ndarray
    n_shapes: int = 0
    n_intersecting: int = 0

    @property
    def fraction_inside(self) -> float:
        """Share of cuttable shapes intersecting the calibration triangle."""
        return self.n_intersecting / self.n_shapes if self.n_shapes else 0.0

    @property
    def is_concerning(self) -> bool:
        """Shapes far outside the triangle get warped by the coordinate transform."""
        return self.n_shapes > 0 and self.fraction_inside < 0.25


@dataclass
class SawReport:
    """Mismatches between a samples-and-wells scheme, the shapes, and the plate."""

    missing_classes: set[str] = field(default_factory=set)
    invalid_wells: set[str] = field(default_factory=set)
    duplicate_wells: dict[str, list[str]] = field(default_factory=dict)
    plate: str = "384"

    @property
    def is_usable(self) -> bool:
        """Wells that do not exist would send tissue nowhere; everything else is a warning."""
        return not self.invalid_wells


def triangle_qc(
    gdf: geopandas.GeoDataFrame,
    calibration_points: dict[str, list[float]],
    selected_names: list[str],
) -> TriangleReport:
    """Check how many shapes fall inside the triangle formed by the chosen calibration points.

    Raises:
        KeyError: a selected name is not in the calibration-point pool.
    """
    logger.info(f"Triangle QC for calibration points {selected_names}")
    missing = [name for name in selected_names if name not in calibration_points]
    if missing:
        raise KeyError(f"Calibration point(s) not found in the file: {missing}")

    calibration_array = numpy.array([calibration_points[name] for name in selected_names])
    triangle = shapely.Polygon(calibration_array)

    cuttable = gdf[gdf.geometry.geom_type.isin(["Polygon", "LineString"])]
    report = TriangleReport(
        calibration_array=calibration_array,
        n_shapes=len(cuttable),
        n_intersecting=int(cuttable.geometry.intersects(triangle).sum()),
    )

    logger.info(f"{report.fraction_inside * 100:.2f}% of shapes intersect the calibration triangle")
    if report.is_concerning:
        logger.warning("Under 25% of shapes are inside the calibration triangle; shapes may be warped")

    return report


def validate_saw(
    samples_and_wells: dict[str, str],
    shape_classes: list[str],
    plate: str = "384",
) -> SawReport:
    """Check a samples-and-wells scheme against the shapes and the chosen plate."""
    logger.info(f"Validating samples-and-wells against a {plate} well plate")
    if not isinstance(samples_and_wells, dict):
        raise TypeError("samples and wells must be a dictionary")

    report = SawReport(plate=plate)
    # Full plate, no margin: margins are a collection-quality preference, not a hard limit,
    # so a user deliberately using an edge well should not be blocked.
    report.invalid_wells = set(samples_and_wells.values()) - set(acceptable_wells(plate=plate, margins=0))
    report.missing_classes = set(shape_classes) - set(samples_and_wells)

    by_well: dict[str, list[str]] = {}
    for class_name, well in samples_and_wells.items():
        by_well.setdefault(well, []).append(class_name)
    report.duplicate_wells = {well: names for well, names in by_well.items() if len(names) > 1}

    if report.invalid_wells:
        logger.error(f"Wells that do not exist on a {plate} well plate: {report.invalid_wells}")
    if report.missing_classes:
        logger.warning(f"Classes present in the shapes but absent from samples and wells: {report.missing_classes}")
    if report.duplicate_wells:
        logger.warning(f"Wells receiving more than one class: {report.duplicate_wells}")

    return report
