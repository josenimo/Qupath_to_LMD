"""Reading, QC and rewriting of QuPath GeoJSON.

Pure functions: they return findings instead of drawing them, so the UI layer decides
how to present them and so they can be exercised outside Streamlit.
"""

import ast
import json
from dataclasses import dataclass, field

import geopandas
import pandas
from loguru import logger

from qupath_to_lmd.model import CLASS_NAME


class GeojsonError(Exception):
    """The file cannot be used at all — no geometry, or no way to find calibration points."""


@dataclass
class GeojsonReport:
    """What QC found. Everything here is shown to the user; nothing is dropped silently."""

    geometry_counts: dict[str, int] = field(default_factory=dict)
    calibration_point_names: list[str] = field(default_factory=list)
    n_unclassified_dropped: int = 0
    multipolygons: pandas.DataFrame | None = None
    n_shapes_kept: int = 0

    @property
    def n_multipolygons_dropped(self) -> int:
        """How many MultiPolygons were removed because py-lmd cannot cut them."""
        return 0 if self.multipolygons is None else len(self.multipolygons)


def read_and_qc(source) -> tuple[geopandas.GeoDataFrame, dict[str, list[float]], GeojsonReport]:
    """Read a QuPath GeoJSON, split off calibration points, and QC the rest.

    Args:
        source: path or file-like object of a QuPath-exported FeatureCollection.

    Returns:
        The cuttable shapes, the calibration-point pool as `{name: [x, y]}`, and a report.

    Raises:
        GeojsonError: the file is empty, or has no `name` column to find points with.
    """
    logger.info("Reading GeoJSON")
    gdf = geopandas.read_file(source)

    # QuPath coordinates are image pixels, but GeoJSON's default CRS is EPSG:4326, so
    # geopandas tags them as longitude/latitude. Left alone, every .area and .distance
    # call is computed against a geographic projection and is wrong.
    gdf = gdf.set_crs(None, allow_override=True)

    if gdf.empty:
        raise GeojsonError("The uploaded GeoJSON file has no features in it.")
    if "name" not in gdf.columns:
        raise GeojsonError(
            "No 'name' column found, so calibration points cannot be identified. "
            "Export from QuPath as a FeatureCollection with named calibration points."
        )

    report = GeojsonReport(geometry_counts=gdf.geometry.geom_type.value_counts().to_dict())
    logger.info(f"Geometries: {report.geometry_counts}")

    # Named Points are the calibration-point pool; all Points then leave the frame.
    points = gdf[gdf.geometry.geom_type == "Point"]
    calibration_points = {
        row["name"]: [row.geometry.x, row.geometry.y] for _, row in points.iterrows() if row["name"]
    }
    report.calibration_point_names = list(calibration_points)
    logger.info(f"Found {len(calibration_points)} calibration points: {report.calibration_point_names}")
    gdf = gdf[gdf.geometry.geom_type != "Point"]

    # Unclassified QuPath objects cannot be assigned to a well.
    if "classification" not in gdf.columns:
        raise GeojsonError("No 'classification' column found — none of the objects are classified in QuPath.")
    n_unclassified = int(gdf["classification"].isna().sum())
    if n_unclassified:
        report.n_unclassified_dropped = n_unclassified
        logger.debug(f"Dropping {n_unclassified} unclassified objects")
        gdf = gdf[gdf["classification"].notna()]

    gdf = gdf.copy()
    gdf[CLASS_NAME] = gdf["classification"].apply(_classification_name)

    # py-lmd cuts a single closed path per shape, so a MultiPolygon has no meaning here.
    is_multi = gdf.geometry.geom_type == "MultiPolygon"
    if is_multi.any():
        columns = [c for c in ("name", CLASS_NAME) if c in gdf.columns]
        report.multipolygons = pandas.DataFrame(gdf.loc[is_multi, columns])
        logger.debug(f"Dropping {int(is_multi.sum())} MultiPolygons")
        gdf = gdf[~is_multi]

    report.n_shapes_kept = len(gdf)
    logger.success(f"GeoJSON QC complete, {report.n_shapes_kept} shapes kept")
    return gdf, calibration_points, report


def _classification_name(value) -> str | None:
    """QuPath writes `classification` as a JSON string; geopandas sometimes gives a dict."""
    if isinstance(value, str):
        return ast.literal_eval(value).get("name")
    return value.get("name") if isinstance(value, dict) else None


def explode_classes(gdf: geopandas.GeoDataFrame, classes: list[str]) -> geopandas.GeoDataFrame:
    """Give every shape of the named classes its own numbered class, for single-cell collection.

    `T-Cell` becomes `T-Cell_001`, `T-Cell_002`, ... Re-running is safe: the original name
    is remembered, so matching is always done against it rather than against the last result.
    """
    logger.info(f"Exploding classes into per-shape names: {classes}")
    gdf = gdf.copy()

    if "original_classification_name" not in gdf.columns:
        gdf["original_classification_name"] = gdf[CLASS_NAME]

    for class_name in classes:
        matching = gdf.index[gdf["original_classification_name"] == class_name]
        for i, idx in enumerate(matching, start=1):
            gdf.loc[idx, CLASS_NAME] = f"{class_name}_{str(i).zfill(3)}"

    gdf = rewrite_classification(gdf)
    logger.success(f"Exploded {len(classes)} class(es)")
    return gdf


def rewrite_classification(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Push `classification_name` back into the nested `classification` value.

    Needed so the exported GeoJSON re-opens in QuPath with the new class names.
    """
    gdf = gdf.copy()

    def rewrite(row):
        value = row["classification"]
        as_dict = ast.literal_eval(value) if isinstance(value, str) else dict(value)
        as_dict["name"] = row[CLASS_NAME]
        return str(as_dict)

    gdf["classification"] = gdf.apply(rewrite, axis=1)
    return gdf


def extract_coordinates(geometry) -> list[list[float]]:
    """Outline of a shape as a list of `[x, y]`, ready for py-lmd."""
    if geometry.geom_type == "Polygon":
        return [list(coord) for coord in geometry.exterior.coords]
    if geometry.geom_type == "LineString":
        return [list(coord) for coord in geometry.coords]
    raise GeojsonError(
        f"Geometry type {geometry.geom_type} is not supported. "
        "Convert it to a Polygon or LineString in QuPath."
    )


def sanitize_for_qupath(gdf: geopandas.GeoDataFrame) -> geopandas.GeoDataFrame:
    """Reduce a frame to what QuPath will re-import without complaining.

    QuPath chokes on NaN-bearing properties, so columns holding any are dropped and only
    the fields it needs are kept.
    """
    logger.info("Sanitizing GeoDataFrame for QuPath re-import")
    gdf = gdf.dropna(axis="columns")

    required = ["id", "objectType", "classification", "geometry"]
    missing = [column for column in required if column not in gdf.columns]
    if missing:
        raise GeojsonError(f"Cannot write a QuPath-compatible GeoJSON, missing columns: {missing}")

    return gdf[required]


def measurements_frame(gdf: geopandas.GeoDataFrame) -> pandas.DataFrame:
    """Explode QuPath's `measurements` JSON into a DataFrame, one column per measurement.

    Only cells and detections carry measurements; annotation-only files give an empty frame.
    The index matches `gdf`, so the result can be joined straight back on.
    """
    if "measurements" not in gdf.columns:
        return pandas.DataFrame(index=gdf.index)

    parsed = {}
    for index, raw in gdf["measurements"].items():
        if isinstance(raw, str) and raw.strip():
            try:
                parsed[index] = json.loads(raw)
            except json.JSONDecodeError:
                logger.debug(f"Could not parse measurements for {index}")
        elif isinstance(raw, dict):
            parsed[index] = raw

    frame = pandas.DataFrame.from_dict(parsed, orient="index")
    logger.info(f"Parsed measurements for {len(frame)} of {len(gdf)} objects, {frame.shape[1]} fields")
    return frame.reindex(gdf.index)


def area_measurement_column(measurements: pandas.DataFrame) -> str | None:
    """Find the column holding QuPath's object area, which it reports in µm².

    `Cell: Area` is what cell segmentation writes; fall back to any other area field so
    detections and custom pipelines still work.
    """
    if measurements.empty:
        return None
    if "Cell: Area" in measurements.columns:
        return "Cell: Area"
    candidates = [c for c in measurements.columns if c.strip().endswith("Area")]
    return candidates[0] if candidates else None
