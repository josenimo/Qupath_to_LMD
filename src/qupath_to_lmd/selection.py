"""Choosing which shapes fill each replicate.

The default is spatial spread: a replicate drawn from one corner of the tissue measures that
corner, not the class. Spread is implemented by binning space on a regular grid and taking
the shape nearest each bin's centre, which also gives interleaved replicates for free — see
`decisions.md` 016 and 040.
"""

from dataclasses import dataclass, field
from enum import Enum

import geopandas
import numpy
import pandas
import shapely
from loguru import logger

from qupath_to_lmd.budget import BudgetMode, ClassBudget
from qupath_to_lmd.model import CLASS_NAME

GRID_SEARCH_STEPS = 40


class SelectionMode(str, Enum):
    """How shapes are chosen from within a class."""

    SPREAD = "spread"
    RANDOM = "random"


@dataclass(frozen=True)
class SelectionParams:
    """Everything that determines a selection, recorded so it can be reproduced."""

    mode: SelectionMode = SelectionMode.SPREAD
    allow_adjacent: bool = True
    seed: int = 0


@dataclass
class SelectionResult:
    """What the selection actually achieved, per class and replicate."""

    replicate_of: pandas.Series = field(default_factory=lambda: pandas.Series(dtype="Int64"))
    achieved: pandas.DataFrame = field(default_factory=pandas.DataFrame)
    n_blocked_by_adjacency: int = 0

    @property
    def n_selected(self) -> int:
        """How many shapes were chosen in total."""
        return int(self.replicate_of.notna().sum())

    @property
    def shortfalls(self) -> pandas.DataFrame:
        """Replicates that could not be filled to the requested amount."""
        if self.achieved.empty:
            return self.achieved
        return self.achieved[self.achieved["achieved"] < self.achieved["requested"] - 1e-9]


def grid_bins(xy: numpy.ndarray, target_bins: int) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Bin points onto a regular grid whose occupied-cell count lands close to `target_bins`.

    A regular grid rather than k-means: k-means costs 14 s at 2000 clusters and 62 s at 4000,
    which is unusable inside a rerun, while this is ~0.03 s at any size and separates the
    chosen shapes at least as well (`decisions.md` 040).

    Returns:
        The bin index of every point, and the centre coordinate of every bin.
    """
    target_bins = max(1, min(target_bins, len(xy)))
    if target_bins == 1:
        return numpy.zeros(len(xy), dtype=int), xy.mean(axis=0, keepdims=True)

    low, high = 1e-9, float(numpy.ptp(xy, axis=0).max()) or 1.0
    best = None
    for _ in range(GRID_SEARCH_STEPS):
        size = (low + high) / 2
        keys = numpy.floor(xy / size).astype(numpy.int64)
        unique, inverse = numpy.unique(keys, axis=0, return_inverse=True)
        count = len(unique)
        if best is None or abs(count - target_bins) < abs(best[2] - target_bins):
            best = (unique, inverse, count, size)
        if count == target_bins:
            break
        low, high = (size, high) if count > target_bins else (low, size)

    unique, inverse, count, size = best
    logger.debug(f"Grid binning {len(xy)} points into {count} bins (wanted {target_bins})")
    return inverse.ravel(), (unique + 0.5) * size


def _adjacency(gdf: geopandas.GeoDataFrame) -> dict[int, set[int]]:
    """Which shapes touch or overlap which, on the original QuPath geometry.

    Evaluated before any smoothing or dilation (`decisions.md` 013), so the verdict does not
    depend on export settings chosen later.
    """
    geometries = gdf.geometry.to_numpy()
    pairs = shapely.STRtree(geometries).query(geometries, predicate="intersects")
    positions = gdf.index.to_numpy()

    neighbours: dict[int, set[int]] = {}
    for left, right in zip(*pairs, strict=True):
        if left == right:
            continue
        neighbours.setdefault(positions[left], set()).add(positions[right])

    logger.info(f"Adjacency graph: {len(neighbours)} shapes have at least one neighbour")
    return neighbours


def _ordered_bins(xy: numpy.ndarray, labels: numpy.ndarray, centres: numpy.ndarray, index: numpy.ndarray) -> list[list]:
    """Members of each bin, nearest to the bin centre first."""
    bins = []
    for bin_number in range(len(centres)):
        members = numpy.flatnonzero(labels == bin_number)
        if members.size == 0:
            continue
        distances = numpy.linalg.norm(xy[members] - centres[bin_number], axis=1)
        bins.append(list(index[members[numpy.argsort(distances)]]))
    return bins


def select(
    gdf: geopandas.GeoDataFrame,
    budgets: list[ClassBudget],
    budget_mode: BudgetMode,
    params: SelectionParams | None = None,
    pixel_size_um: float | None = None,
) -> SelectionResult:
    """Fill every replicate of every class, and report what was achieved.

    Args:
        gdf: QC'd shapes carrying `classification_name`.
        budgets: one per class, giving replicates and the amount per replicate.
        budget_mode: whether the amount counts shapes or µm².
        params: mode, adjacency constraint and seed.
        pixel_size_um: needed for an area budget, and for reporting achieved area.

    Raises:
        ValueError: an area budget without a pixel size.
    """
    params = params or SelectionParams()
    if budget_mode is BudgetMode.AREA and not pixel_size_um:
        raise ValueError("An area budget needs the image scale (µm per pixel).")

    logger.info(
        f"Selecting: mode={params.mode.value}, adjacent allowed={params.allow_adjacent}, "
        f"seed={params.seed}, budget in {budget_mode.unit}"
    )

    um2_per_px2 = (pixel_size_um or 1.0) ** 2
    areas = gdf.geometry.area * um2_per_px2
    neighbours = {} if params.allow_adjacent else _adjacency(gdf)

    replicate_of = pandas.Series(pandas.NA, index=gdf.index, dtype="Int64")
    taken: set = set()
    blocked = 0
    rows = []
    generator = numpy.random.default_rng(params.seed)

    for item in budgets:
        candidates = gdf.index[gdf[CLASS_NAME] == item.class_name].to_numpy()
        if candidates.size == 0:
            logger.warning(f"No shapes for class {item.class_name}")
            continue

        bins, blocked_here = _bins_for(gdf, candidates, item, budget_mode, areas, params, generator)
        blocked += blocked_here
        pointers = [0] * len(bins)

        for replicate in range(1, item.replicates + 1):
            picked, blocked_here = _fill_replicate(
                bins, pointers, taken, neighbours, item, budget_mode, areas
            )
            blocked += blocked_here
            for position in picked:
                replicate_of.at[position] = replicate
                taken.add(position)

            achieved = len(picked) if budget_mode is BudgetMode.CELLS else float(areas[picked].sum())
            rows.append(
                {
                    CLASS_NAME: item.class_name,
                    "replicate": replicate,
                    "shapes": len(picked),
                    "area_um2": float(areas[picked].sum()) if pixel_size_um else numpy.nan,
                    "requested": item.per_replicate,
                    "achieved": achieved,
                }
            )

    result = SelectionResult(
        replicate_of=replicate_of,
        achieved=pandas.DataFrame(rows),
        n_blocked_by_adjacency=blocked,
    )
    logger.success(
        f"Selected {result.n_selected} shapes across {len(result.achieved)} replicates"
        + (f", {blocked} candidates skipped as adjacent" if blocked else "")
    )
    return result


def _bins_for(gdf, candidates, item, budget_mode, areas, params, generator) -> tuple[list[list], int]:
    """Order the class's candidates into groups to draw from, one draw per group per pass."""
    if params.mode is SelectionMode.RANDOM:
        shuffled = candidates.copy()
        generator.shuffle(shuffled)
        # One bin holding everything: a pass takes them in shuffled order.
        return [list(shuffled)], 0

    # Aim for one bin per shape wanted, so a single pass spreads across the whole class.
    if budget_mode is BudgetMode.CELLS:
        wanted = int(item.per_replicate)
    else:
        median_area = float(areas[candidates].median()) or 1.0
        wanted = max(1, int(numpy.ceil(item.per_replicate / median_area)))

    subset = gdf.loc[candidates]
    centroids = subset.geometry.centroid
    xy = numpy.c_[centroids.x.to_numpy(), centroids.y.to_numpy()]
    labels, centres = grid_bins(xy, wanted)
    return _ordered_bins(xy, labels, centres, candidates), 0


def _fill_replicate(bins, pointers, taken, neighbours, item, budget_mode, areas) -> tuple[list, int]:
    """Take shapes round-robin across bins until the budget is met or the class runs dry."""
    picked: list = []
    picked_area = 0.0
    blocked = 0

    def satisfied() -> bool:
        if budget_mode is BudgetMode.CELLS:
            return len(picked) >= item.per_replicate
        return picked_area >= item.per_replicate

    if item.per_replicate <= 0:
        return picked, blocked

    progressed = True
    while progressed and not satisfied():
        progressed = False
        for bin_number, members in enumerate(bins):
            if satisfied():
                break
            while pointers[bin_number] < len(members):
                candidate = members[pointers[bin_number]]
                pointers[bin_number] += 1
                if candidate in taken:
                    continue
                if neighbours and any(neighbour in taken for neighbour in neighbours.get(candidate, ())):
                    blocked += 1
                    continue
                picked.append(candidate)
                taken.add(candidate)
                picked_area += float(areas[candidate])
                progressed = True
                break

    # The caller owns `taken`; entries added here are re-added there, which is harmless.
    return picked, blocked
