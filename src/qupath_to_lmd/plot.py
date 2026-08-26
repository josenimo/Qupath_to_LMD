"""Drawing shapes for the class overview, the selection preview and the export QC image.

One function serves all three (`decisions.md` 017).

Uses `matplotlib.figure.Figure` directly rather than `pyplot`, because pyplot keeps every
figure in a global registry and Streamlit reruns would leak them.
"""

import geopandas
import numpy
import pandas
from loguru import logger
from matplotlib.collections import PolyCollection
from matplotlib.figure import Figure
from matplotlib.lines import Line2D

from qupath_to_lmd.model import CLASS_NAME

# Okabe-Ito, which stays distinguishable for the common forms of colour blindness.
PALETTE = ["#E69F00", "#56B4E9", "#009E73", "#0072B2", "#D55E00", "#CC79A7", "#F0E442"]
MUTED = "#dcdcdc"
MUTED_EDGE = "#b4b4b4"

# Above this many shapes, draw one dot per shape instead of its outline. Polygon rendering
# is ~2s at 50k shapes and ~8s at 200k; centroids are 0.14s at 200k.
POLYGON_LIMIT = 20_000


def class_colors(classes: list[str]) -> dict[str, str]:
    """Stable colour per class: sorted, so a class keeps its colour across redraws."""
    return {name: PALETTE[i % len(PALETTE)] for i, name in enumerate(sorted(classes))}


def plot_shapes(
    gdf: geopandas.GeoDataFrame,
    labels: pandas.Series | None = None,
    included: list[str] | None = None,
    calibration_array: numpy.ndarray | None = None,
    title: str | None = None,
    figsize: tuple[float, float] = (10.0, 7.5),
) -> Figure:
    """Draw shapes coloured by a label, with everything else grey.

    One function for the class overview, the selection preview and the export QC image
    (`decisions.md` 017): the caller decides what the label means.

    Args:
        gdf: the shapes.
        labels: label per shape index. Defaults to `classification_name`. Shapes whose label
            is NA are always drawn grey — that is how unselected shapes appear.
        included: labels to colour. Anything else is drawn grey, so a user can see what they
            are leaving out rather than only what they are taking.
        calibration_array: 3x2 array; drawn as a dashed triangle if given.
        title: optional heading.
        figsize: inches.
    """
    figure = Figure(figsize=figsize, layout="constrained")
    axes = figure.add_subplot()

    if labels is None:
        labels = gdf[CLASS_NAME]
    labels = labels.reindex(gdf.index)

    classes = sorted(labels.dropna().unique())
    included = classes if included is None else included
    colors = class_colors(classes)
    as_dots = len(gdf) > POLYGON_LIMIT
    logger.info(f"Plotting {len(gdf)} shapes as {'centroids' if as_dots else 'polygons'}")

    unlabelled = gdf[labels.isna()]
    if not unlabelled.empty:
        _draw(axes, unlabelled, MUTED, as_dots, False)

    # Excluded first, so included labels are drawn over them.
    for class_name in sorted(classes, key=lambda name: name in included):
        subset = gdf[labels == class_name]
        if subset.empty:
            continue
        is_in = class_name in included
        _draw(axes, subset, colors[class_name] if is_in else MUTED, as_dots, is_in)

    if calibration_array is not None and len(calibration_array) == 3:
        triangle = numpy.vstack([calibration_array, calibration_array[:1]])
        axes.plot(triangle[:, 0], triangle[:, 1], "--", color="#444444", linewidth=1, zorder=1)
        axes.scatter(
            calibration_array[:, 0], calibration_array[:, 1],
            marker="+", s=90, color="#444444", zorder=4,
        )

    handles = [
        Line2D([], [], marker="o", linestyle="", markersize=7,
               markerfacecolor=colors[name] if name in included else MUTED,
               markeredgecolor="none",
               label=f"{name} ({int((labels == name).sum())})"
                     + ("" if name in included else " — excluded"))
        for name in classes
    ]
    if not unlabelled.empty:
        handles.append(
            Line2D([], [], marker="o", linestyle="", markersize=7, markerfacecolor=MUTED,
                   markeredgecolor="none", label=f"not selected ({len(unlabelled)})")
        )
    if handles:
        # Placed outside the axes: a legend inside covers tissue, and tissue is the point.
        # "outside ..." locations need constrained layout, which the figure above uses.
        figure.legend(handles=handles, fontsize=8, loc="outside right upper", frameon=False)

    # QuPath image coordinates grow downward, so inverting y makes this look like the view
    # the user annotated in.
    axes.invert_yaxis()
    axes.set_aspect("equal")
    axes.axis("off")
    if title:
        axes.set_title(title, fontsize=10)

    return figure


def _draw(axes, subset: geopandas.GeoDataFrame, color: str, as_dots: bool, emphasised: bool) -> None:
    """Draw one class, either as outlines or as centroid dots."""
    if as_dots:
        centroids = subset.geometry.centroid
        axes.scatter(centroids.x, centroids.y, s=2 if emphasised else 1, c=color,
                     linewidths=0, zorder=3 if emphasised else 2)
        return

    polygons = [
        numpy.asarray(geometry.exterior.coords)
        for geometry in subset.geometry
        if geometry.geom_type == "Polygon"
    ]
    if not polygons:
        return
    axes.add_collection(
        PolyCollection(
            polygons,
            facecolors=color,
            edgecolors=color if emphasised else MUTED_EDGE,
            linewidths=0.3,
            zorder=3 if emphasised else 2,
        )
    )
    axes.autoscale_view()
