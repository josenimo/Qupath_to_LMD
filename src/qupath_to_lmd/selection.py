"""Choosing which shapes fill each replicate.

Two things matter and they pull against each other. Each replicate should sample the whole
class, not one corner of it — otherwise a replicate measures a region rather than a
population. And no two collected shapes should sit on top of each other, whichever replicate
they belong to, because neighbouring cells share a cut boundary.

The approach: bin space into one bin per shape *in the whole class budget* (not per
replicate), take one shape per bin so every collected shape is roughly a bin apart, then deal
those across replicates in a spatially shuffled order so each replicate still spans the
tissue. Adjacency is a strong preference rather than a rule, because in dense tissue avoiding
it entirely is sometimes impossible — see `decisions.md` 042.
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

WITH_NEIGHBOUR = "neighbour_also_collected"


class SelectionMode(str, Enum):
    """How shapes are chosen from within a class."""

    SPREAD = "spread"
    RANDOM = "random"


@dataclass(frozen=True)
class SelectionParams:
    """Everything that determines a selection, recorded so it can be reproduced."""

    mode: SelectionMode = SelectionMode.SPREAD
    avoid_adjacent: bool = True
    seed: int = 0


@dataclass
class SelectionResult:
    """What the selection achieved, per class and replicate."""

    replicate_of: pandas.Series = field(default_factory=lambda: pandas.Series(dtype="Int64"))
    achieved: pandas.DataFrame = field(default_factory=pandas.DataFrame)
    n_with_collected_neighbour: int = 0

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
    unusable inside a Streamlit rerun, while this is ~0.03 s at any size (`decisions.md` 040).

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


def adjacency(gdf: geopandas.GeoDataFrame) -> dict:
    """Which shapes touch or overlap which, on the original QuPath geometry.

    Evaluated before any smoothing or dilation (`decisions.md` 013), so the verdict does not
    depend on export settings chosen later. Always computed, even when the user allows
    adjacent shapes, because the count of collected shapes with a collected neighbour is
    reported either way.
    """
    geometries = gdf.geometry.to_numpy()
    pairs = shapely.STRtree(geometries).query(geometries, predicate="intersects")
    positions = gdf.index.to_numpy()

    neighbours: dict = {}
    for left, right in zip(*pairs, strict=True):
        if left != right:
            neighbours.setdefault(positions[left], set()).add(positions[right])

    logger.info(f"Adjacency: {len(neighbours)} of {len(gdf)} shapes touch at least one other")
    return neighbours


def _candidate_stream(
    gdf: geopandas.GeoDataFrame,
    candidates: numpy.ndarray,
    total_wanted: int,
    params: SelectionParams,
    generator: numpy.random.Generator,
) -> list:
    """Order a class's shapes so that taking a prefix gives a well-spread set.

    One bin per shape in the *whole* class budget, so consecutive picks are about a bin
    apart. Bin order is shuffled within each pass, so dealing the stream round-robin across
    replicates does not put replicate 1 and replicate 2 in the same neighbourhood.
    """
    if params.mode is SelectionMode.RANDOM:
        shuffled = candidates.copy()
        generator.shuffle(shuffled)
        return list(shuffled)

    subset = gdf.loc[candidates]
    centroids = subset.geometry.centroid
    xy = numpy.c_[centroids.x.to_numpy(), centroids.y.to_numpy()]
    labels, centres = grid_bins(xy, total_wanted)

    members: list[list] = []
    for bin_number in range(len(centres)):
        in_bin = numpy.flatnonzero(labels == bin_number)
        if in_bin.size == 0:
            continue
        distances = numpy.linalg.norm(xy[in_bin] - centres[bin_number], axis=1)
        members.append(list(candidates[in_bin[numpy.argsort(distances)]]))

    stream: list = []
    for depth in range(max((len(m) for m in members), default=0)):
        order = generator.permutation(len(members))
        stream.extend(members[b][depth] for b in order if depth < len(members[b]))
    return stream


def _total_wanted(item: ClassBudget, budget_mode: BudgetMode, areas: pandas.Series, candidates) -> int:
    """How many shapes the whole class budget implies, used to size the grid."""
    if budget_mode is BudgetMode.CELLS:
        return max(1, int(item.replicates * item.per_replicate))
    median_area = float(areas[candidates].median()) or 1.0
    return max(1, int(numpy.ceil(item.replicates * item.per_replicate / median_area)))


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
        params: mode, adjacency preference and seed.
        pixel_size_um: needed for an area budget, and for reporting achieved area.

    Raises:
        ValueError: an area budget without a pixel size.
    """
    params = params or SelectionParams()
    if budget_mode is BudgetMode.AREA and not pixel_size_um:
        raise ValueError("An area budget needs the image scale (µm per pixel).")

    logger.info(
        f"Selecting: mode={params.mode.value}, avoid adjacent={params.avoid_adjacent}, "
        f"seed={params.seed}, budget in {budget_mode.unit}"
    )

    areas = gdf.geometry.area * (pixel_size_um or 1.0) ** 2
    neighbours = adjacency(gdf)
    generator = numpy.random.default_rng(params.seed)

    replicate_of = pandas.Series(pandas.NA, index=gdf.index, dtype="Int64")
    taken: set = set()
    rows = []

    for item in budgets:
        candidates = gdf.index[gdf[CLASS_NAME] == item.class_name].to_numpy()
        if candidates.size == 0 or item.per_replicate <= 0:
            rows.extend(_empty_rows(item, budget_mode, pixel_size_um))
            continue

        total = _total_wanted(item, budget_mode, areas, candidates)
        stream = _candidate_stream(gdf, candidates, total, params, generator)
        picked = _deal(stream, item, budget_mode, areas, taken, neighbours, params.avoid_adjacent)

        for replicate, members in picked.items():
            for position in members:
                replicate_of.at[position] = replicate
            achieved = len(members) if budget_mode is BudgetMode.CELLS else float(areas[members].sum())
            rows.append(
                {
                    CLASS_NAME: item.class_name,
                    "replicate": replicate,
                    "shapes": len(members),
                    "area_um2": float(areas[members].sum()) if pixel_size_um else numpy.nan,
                    "requested": item.per_replicate,
                    "achieved": achieved,
                }
            )

    achieved_table = pandas.DataFrame(rows)
    achieved_table = _count_collected_neighbours(achieved_table, replicate_of, gdf[CLASS_NAME], neighbours)
    total_conflicts = int(achieved_table[WITH_NEIGHBOUR].sum()) if not achieved_table.empty else 0

    result = SelectionResult(
        replicate_of=replicate_of,
        achieved=achieved_table,
        n_with_collected_neighbour=total_conflicts,
    )
    logger.success(
        f"Selected {result.n_selected} shapes across {len(achieved_table)} replicates; "
        f"{total_conflicts} of them touch another collected shape"
    )
    return result


def _empty_rows(item: ClassBudget, budget_mode: BudgetMode, pixel_size_um) -> list[dict]:
    """Rows for a class that could supply nothing, so it still appears in the report."""
    return [
        {
            CLASS_NAME: item.class_name,
            "replicate": replicate,
            "shapes": 0,
            "area_um2": 0.0 if pixel_size_um else numpy.nan,
            "requested": item.per_replicate,
            "achieved": 0,
        }
        for replicate in range(1, item.replicates + 1)
    ]


def _deal(stream, item, budget_mode, areas, taken, neighbours, avoid_adjacent) -> dict:
    """Hand shapes to replicates in turn, preferring shapes with no collected neighbour.

    Adjacency is a preference, not a rule: dense tissue can make it impossible to fill a
    budget without touching, and silently under-delivering would be worse than touching
    (`decisions.md` 042). Conflicting candidates are held back and only used once the
    non-conflicting ones run out.
    """
    picked: dict[int, list] = {replicate: [] for replicate in range(1, item.replicates + 1)}
    filled = dict.fromkeys(picked, 0.0)
    deferred: list = []

    def needs(replicate) -> bool:
        return filled[replicate] < item.per_replicate - 1e-9

    def give(replicate, candidate) -> None:
        picked[replicate].append(candidate)
        taken.add(candidate)
        filled[replicate] += 1 if budget_mode is BudgetMode.CELLS else float(areas[candidate])

    def next_needy(order):
        return next((replicate for replicate in order if needs(replicate)), None)

    turn = 0
    for pool, first_pass in ((stream, True), (deferred, False)):
        for candidate in pool:
            if candidate in taken:
                continue
            if not any(needs(replicate) for replicate in picked):
                break
            if first_pass and avoid_adjacent and any(n in taken for n in neighbours.get(candidate, ())):
                deferred.append(candidate)
                continue
            order = list(picked)[turn:] + list(picked)[:turn]
            replicate = next_needy(order)
            if replicate is None:
                break
            give(replicate, candidate)
            turn = (turn + 1) % len(picked)

    return picked


def _count_collected_neighbours(
    table: pandas.DataFrame,
    replicate_of: pandas.Series,
    class_of: pandas.Series,
    neighbours: dict,
) -> pandas.DataFrame:
    """Per class and replicate, how many shapes touch another shape also being collected.

    Counted across the whole collection, not within a replicate: the laser cuts a shared
    boundary regardless of which well either cell goes to.
    """
    if table.empty:
        table[WITH_NEIGHBOUR] = []
        return table

    selected = set(replicate_of.dropna().index)
    conflicting = {
        position for position in selected if any(n in selected for n in neighbours.get(position, ()))
    }

    counts = []
    for _, row in table.iterrows():
        members = replicate_of.index[
            (replicate_of == row["replicate"]) & (class_of == row[CLASS_NAME])
        ]
        counts.append(sum(1 for position in members if position in conflicting))

    table[WITH_NEIGHBOUR] = counts
    logger.debug(f"{len(conflicting)} collected shapes touch another collected shape")
    return table
