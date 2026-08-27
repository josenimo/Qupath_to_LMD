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
    triangle_area: float = 0.0

    @property
    def fraction_inside(self) -> float:
        """Share of cuttable shapes intersecting the calibration triangle."""
        return self.n_intersecting / self.n_shapes if self.n_shapes else 0.0

    @property
    def is_concerning(self) -> bool:
        """Shapes far outside the triangle get warped by the coordinate transform."""
        return self.n_shapes > 0 and self.fraction_inside < 0.25

    @property
    def is_degenerate(self) -> bool:
        """The three points do not form a triangle, so no coordinate transform exists.

        Happens when the same point is picked more than once, or when all three are
        collinear. py-lmd does not complain — it writes a perfectly well-formed XML whose
        coordinates are meaningless — so this has to be caught here.
        """
        return self.triangle_area <= 0


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
        triangle_area=float(triangle.area),
    )

    if report.is_degenerate:
        logger.error(f"Calibration points {selected_names} do not form a triangle")
        return report

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


@dataclass
class PixelSizeReport:
    """Cross-check of the user's µm/px against what QuPath's own measurements imply."""

    entered_um_per_px: float
    n_objects_checked: int = 0
    implied_um_per_px: float | None = None
    relative_spread: float | None = None

    @property
    def ratio(self) -> float | None:
        """Entered value divided by the implied one. 1.0 means agreement."""
        if not self.implied_um_per_px:
            return None
        return self.entered_um_per_px / self.implied_um_per_px

    @property
    def is_concerning(self) -> bool:
        """More than 5% apart. A 10x typo turns an area budget into a different experiment."""
        return self.ratio is not None and abs(self.ratio - 1) > 0.05


def pixel_size_qc(gdf: geopandas.GeoDataFrame, entered_um_per_px: float) -> PixelSizeReport:
    """Compare an entered µm/px against the scale implied by QuPath's area measurements.

    QuPath writes object areas in µm² while GeoJSON coordinates stay in image pixels, so
    `sqrt(area_µm² / area_px²)` recovers the scale. Reports only — the entered value is
    never overwritten (`decisions.md` 011).
    """
    from qupath_to_lmd.geojson import area_measurements

    report = PixelSizeReport(entered_um_per_px=entered_um_per_px)

    # Only the area field, not all ~100 measurements: see `decisions.md` 050.
    area_um2 = area_measurements(gdf)
    if area_um2.notna().sum() == 0:
        logger.info("No area measurements in this file, cannot cross-check pixel size")
        return report

    area_px2 = gdf.geometry.area
    usable = area_um2.notna() & (area_um2 > 0) & (area_px2 > 0)
    if not usable.any():
        return report

    implied = numpy.sqrt(area_um2[usable] / area_px2[usable])
    report.n_objects_checked = int(usable.sum())
    report.implied_um_per_px = float(implied.median())
    report.relative_spread = float(implied.std() / implied.median()) if len(implied) > 1 else 0.0

    logger.info(
        f"Pixel size: entered {entered_um_per_px} µm/px, "
        f"QuPath's own areas imply {report.implied_um_per_px:.4f} over {report.n_objects_checked} objects"
    )
    if report.is_concerning:
        logger.warning(f"Entered pixel size is {report.ratio:.2f}x the implied value")

    return report
