"""Per-class descriptive statistics, so a user can see what is available before committing.

Areas are derived from the shape geometry and the user's µm/px, never from QuPath
`measurements` — a typical QuPath export has none (`decisions.md` 029).
"""

import geopandas
import numpy
import pandas
from loguru import logger
from shapely.geometry import MultiPoint

from qupath_to_lmd.model import CLASS_NAME

UM2_PER_MM2 = 1_000_000


def class_statistics(
    gdf: geopandas.GeoDataFrame,
    pixel_size_um: float,
    area_floor_um2: float = 0.0,
) -> pandas.DataFrame:
    """Summarise every class in the frame, one row per class.

    Args:
        gdf: QC'd shapes carrying `classification_name`.
        pixel_size_um: micrometres per pixel; areas are meaningless without it.
        area_floor_um2: optional threshold. Shapes below it are counted per class so the
            user can see how much of a class is too small to be worth collecting. 0
            disables the count.

    Returns:
        A numeric frame indexed by class name. Formatting is the caller's business.
    """
    if pixel_size_um <= 0:
        raise ValueError("pixel_size_um must be positive to compute areas")
    if gdf.empty:
        return pandas.DataFrame()

    logger.info(f"Computing statistics for {gdf[CLASS_NAME].nunique()} classes at {pixel_size_um} µm/px")

    um2_per_px2 = pixel_size_um**2
    frame = pandas.DataFrame(
        {
            CLASS_NAME: gdf[CLASS_NAME].to_numpy(),
            "area_um2": gdf.geometry.area.to_numpy() * um2_per_px2,
        }
    )

    grouped = frame.groupby(CLASS_NAME)["area_um2"]
    stats = pandas.DataFrame(
        {
            "shapes": grouped.size(),
            "area_total_um2": grouped.sum(),
            "area_median_um2": grouped.median(),
            "area_q1_um2": grouped.quantile(0.25),
            "area_q3_um2": grouped.quantile(0.75),
            "area_min_um2": grouped.min(),
            "area_max_um2": grouped.max(),
        }
    )
    stats["area_total_mm2"] = stats["area_total_um2"] / UM2_PER_MM2

    if area_floor_um2 > 0:
        below = frame[frame["area_um2"] < area_floor_um2].groupby(CLASS_NAME).size()
        stats["shapes_below_floor"] = below.reindex(stats.index).fillna(0).astype(int)

    extent = _extent_mm2(gdf, um2_per_px2)
    stats["extent_mm2"] = extent
    with numpy.errstate(divide="ignore", invalid="ignore"):
        stats["shapes_per_mm2"] = stats["shapes"] / extent.replace(0, numpy.nan)

    return stats.sort_values("shapes", ascending=False)


def _extent_mm2(gdf: geopandas.GeoDataFrame, um2_per_px2: float) -> pandas.Series:
    """Area of the convex hull of each class's shape centroids, in mm².

    Centroids rather than full geometries because the hull of 8000 polygons is slow and no
    more informative. Fewer than three centroids, or collinear ones, give a zero-area hull;
    those become 0 here and the density that divides by them becomes NaN rather than
    infinity.
    """
    centroids = gdf.geometry.centroid
    points = pandas.DataFrame({CLASS_NAME: gdf[CLASS_NAME].to_numpy(), "x": centroids.x, "y": centroids.y})

    areas = {}
    for class_name, group in points.groupby(CLASS_NAME):
        hull = MultiPoint(list(zip(group["x"], group["y"], strict=True))).convex_hull
        areas[class_name] = hull.area * um2_per_px2 / UM2_PER_MM2

    return pandas.Series(areas, name="extent_mm2")


DISPLAY_COLUMNS = {
    "shapes": "Shapes",
    "area_total_mm2": "Total area (mm²)",
    "area_median_um2": "Median area (µm²)",
    "area_q1_um2": "Q1 (µm²)",
    "area_q3_um2": "Q3 (µm²)",
    "area_min_um2": "Min (µm²)",
    "area_max_um2": "Max (µm²)",
    "extent_mm2": "Spread (mm²)",
    "shapes_per_mm2": "Density (/mm²)",
    "shapes_below_floor": "Below floor",
}


def for_display(stats: pandas.DataFrame) -> pandas.DataFrame:
    """Rename and order the columns for showing to a user."""
    columns = [c for c in DISPLAY_COLUMNS if c in stats.columns]
    return stats[columns].rename(columns=DISPLAY_COLUMNS)
