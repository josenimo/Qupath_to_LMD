"""The selection engine — which shapes fill each replicate.

The scientific core. A failure here means the wrong tissue gets collected, so the tests state
the property being protected rather than just the expected number.
"""

import numpy
import pytest
import shapely

from qupath_to_lmd import budget, geojson, selection
from qupath_to_lmd.model import CLASS_NAME
from tests.conftest import CELLS_PIXEL_SIZE


def _select(gdf, per_replicate=20, replicates=3, classes=None, **params):
    classes = classes or [max(set(gdf[CLASS_NAME]), key=lambda c: (gdf[CLASS_NAME] == c).sum())]
    budgets = [budget.ClassBudget(name, replicates, per_replicate) for name in classes]
    return selection.select(
        gdf, budgets, budget.BudgetMode.CELLS,
        selection.SelectionParams(**params), CELLS_PIXEL_SIZE,
    )


def _centroids(gdf, index):
    subset = gdf.loc[index]
    return numpy.c_[subset.geometry.centroid.x, subset.geometry.centroid.y]


def test_a_count_budget_is_met_exactly(cells_gdf):
    result = _select(cells_gdf, per_replicate=20, replicates=3, seed=1)
    assert (result.achieved["shapes"] == 20).all(), (
        f"Each replicate was asked for 20 shapes; got {result.achieved['shapes'].tolist()}."
    )
    assert result.shortfalls.empty


def test_no_shape_is_collected_twice(cells_gdf):
    """A shape in two wells would be cut twice, which is physically impossible."""
    result = _select(cells_gdf, per_replicate=20, replicates=3, seed=1)
    selected = result.replicate_of.dropna()
    assert len(selected) == len(set(selected.index)), "A shape was assigned to more than one replicate."
    assert len(selected) == result.n_selected


def test_spread_separates_shapes_better_than_random(cells_gdf):
    """A replicate drawn from one corner measures that corner, not the class.

    Compared against random rather than an absolute distance, because the right number depends
    on the tissue.
    """
    spread = _select(cells_gdf, per_replicate=20, replicates=2, seed=1, mode=selection.SelectionMode.SPREAD)
    random = _select(cells_gdf, per_replicate=20, replicates=2, seed=1, mode=selection.SelectionMode.RANDOM)

    def median_nearest(result):
        points = _centroids(cells_gdf, result.replicate_of.dropna().index)
        distances = numpy.linalg.norm(points[:, None] - points[None], axis=2)
        numpy.fill_diagonal(distances, numpy.inf)
        return float(numpy.median(distances.min(axis=1)))

    spread_distance, random_distance = median_nearest(spread), median_nearest(random)
    assert spread_distance > random_distance, (
        f"Spread selection put shapes {spread_distance:.0f}px apart against random's "
        f"{random_distance:.0f}px. Spread should separate them more, or it is not spreading."
    )


def test_replicates_are_interleaved_not_partitioned(cells_gdf):
    """Each replicate should span the whole class, so replicates are statistical repeats rather
    than samples of different regions.

    Checked against shuffling the replicate labels of the same shapes: if the real spread of
    replicate centroids is no worse than that null, the replicates are interleaved. Averaged
    over seeds, because one draw against its own tail fails by chance.
    """
    rng = numpy.random.default_rng(0)
    real, null = [], []
    for seed in range(5):
        result = _select(cells_gdf, per_replicate=15, replicates=3, seed=seed)
        index = result.replicate_of.dropna().index
        labels = result.replicate_of.reindex(index).to_numpy()
        points = _centroids(cells_gdf, index)
        extent = float(numpy.ptp(points, axis=0).max())

        def spread(assignment, points=points):
            centres = [points[assignment == r].mean(axis=0) for r in sorted(set(assignment))]
            return max(numpy.linalg.norm(a - b) for a in centres for b in centres)

        real.append(spread(labels) / extent)
        null.extend(spread(rng.permutation(labels)) / extent for _ in range(10))

    assert numpy.median(real) <= numpy.percentile(null, 90), (
        f"Replicate centroids differ by {numpy.median(real):.1%} of the class extent against a "
        f"shuffled-label median of {numpy.median(null):.1%}. That suggests replicates are being "
        "partitioned by region rather than interleaved."
    )


def test_the_same_seed_gives_the_same_selection(cells_gdf):
    """A collection has to be reproducible to be reportable in a methods section."""
    first = _select(cells_gdf, seed=7)
    second = _select(cells_gdf, seed=7)
    assert first.replicate_of.equals(second.replicate_of), "The same seed produced a different selection."


def test_different_seeds_give_different_selections(cells_gdf):
    """If the seed did nothing, reporting it would be meaningless."""
    a = _select(cells_gdf, seed=7, mode=selection.SelectionMode.RANDOM)
    b = _select(cells_gdf, seed=8, mode=selection.SelectionMode.RANDOM)
    assert not a.replicate_of.equals(b.replicate_of), "Two different seeds produced identical selections."


def test_neighbours_are_judged_by_distance_not_strict_intersection(near_touching_chain):
    """Real QuPath segmentation separates adjacent cells by a sub-pixel gap.

    On an 8537-cell export the median gap to the nearest neighbour was 0.57 px and only 4% of
    cells actually intersected — so a strict `intersects` test found 350 pairs where a 1 px
    tolerance found 26 336. Cells adjacent in every sense that matters for cutting were
    invisible, which made both the constraint and its report meaningless. This fixture has the
    same 0.5 px gaps.
    """
    strict = selection.adjacency(near_touching_chain, 0.0)
    tolerant = selection.adjacency(near_touching_chain, 1.0)

    assert not strict, (
        f"These squares are separated by a 0.5px gap, so strict intersection should find no "
        f"neighbours at all; it found some for {len(strict)} shapes."
    )
    assert len(tolerant) == len(near_touching_chain), (
        f"Every square has a neighbour within 1px, but the tolerant test found them for only "
        f"{len(tolerant)} of {len(near_touching_chain)}."
    )
    assert selection.DEFAULT_NEIGHBOUR_DISTANCE_PX > 0, (
        "The default neighbour distance is zero, which reverts to strict intersection and would "
        "report almost no neighbours on a real segmentation."
    )


def test_the_adjacency_preference_respects_the_sub_pixel_gap(near_touching_chain):
    """Twelve squares each within 1px of the next: at the default distance the largest
    non-adjacent set is six, so asking for more must relax and report."""
    def run(per_replicate):
        return selection.select(
            near_touching_chain, [budget.ClassBudget("chain", 1, per_replicate)],
            budget.BudgetMode.CELLS, selection.SelectionParams(avoid_adjacent=True, seed=0),
        )

    comfortable = run(6)
    assert comfortable.n_with_collected_neighbour == 0, (
        f"Six of twelve squares can be chosen without any pair within 1px, but "
        f"{comfortable.n_with_collected_neighbour} conflicts were reported."
    )
    forced = run(12)
    assert forced.n_selected == 12
    assert forced.n_with_collected_neighbour > 0, (
        "Taking all twelve makes neighbours unavoidable, so conflicts must be reported."
    )


def test_a_budget_that_fits_the_non_touching_set_takes_no_neighbours(touching_chain):
    """Twenty squares in a chain: the largest set with no two touching is exactly ten.

    Asking for ten must find them.
    """
    result = selection.select(
        touching_chain, [budget.ClassBudget("chain", 2, 5)], budget.BudgetMode.CELLS,
        selection.SelectionParams(avoid_adjacent=True, seed=0),
    )
    assert result.n_selected == 10
    assert result.n_with_collected_neighbour == 0, (
        f"Ten non-touching squares exist in this chain, but {result.n_with_collected_neighbour} "
        "of the selected ones touch another selected one. The preference is not being applied."
    )


def test_an_impossible_budget_is_still_filled_and_the_conflicts_reported(touching_chain):
    """In dense tissue a large budget cannot be met without neighbours. Under-delivering
    silently would be worse than touching, so the preference relaxes — and says so."""
    result = selection.select(
        touching_chain, [budget.ClassBudget("chain", 1, 12)], budget.BudgetMode.CELLS,
        selection.SelectionParams(avoid_adjacent=True, seed=0),
    )
    assert result.n_selected == 12, (
        f"Asked for 12 of 20 squares and got {result.n_selected}. The adjacency preference must "
        "relax rather than under-deliver."
    )
    assert result.n_with_collected_neighbour > 0, (
        "Twelve squares cannot be chosen from this chain without touching, so conflicts must be "
        "reported. Reporting zero here means the count is not working."
    )
    assert selection.WITH_NEIGHBOUR in result.achieved.columns


def test_the_adjacency_count_spans_replicates(touching_chain):
    """The laser cuts a shared boundary regardless of which well either cell goes to, so the
    constraint cannot be per-replicate."""
    result = selection.select(
        touching_chain, [budget.ClassBudget("chain", 2, 5)], budget.BudgetMode.CELLS,
        selection.SelectionParams(avoid_adjacent=True, seed=0),
    )
    chosen = touching_chain.loc[result.replicate_of.dropna().index]
    geometries = chosen.geometry.to_numpy()
    pairs = shapely.STRtree(geometries).query(geometries, predicate="intersects")
    touching = int((pairs[0] != pairs[1]).sum())
    assert touching == 0, (
        f"{touching} pairs of selected squares touch each other across replicates. Two replicates "
        "must not take neighbouring shapes."
    )


def test_an_area_budget_reaches_its_target_without_wild_overshoot(cells_gdf):
    """Filling by area walks outward until the target is met, so it lands just above it."""
    target = 2000.0
    biggest = max(set(cells_gdf[CLASS_NAME]), key=lambda c: (cells_gdf[CLASS_NAME] == c).sum())
    result = selection.select(
        cells_gdf, [budget.ClassBudget(biggest, 2, target)], budget.BudgetMode.AREA,
        selection.SelectionParams(seed=1), CELLS_PIXEL_SIZE,
    )
    assert (result.achieved["area_um2"] >= target).all(), (
        f"Area budgets should reach the target; achieved {result.achieved['area_um2'].tolist()} "
        f"against {target}."
    )
    assert (result.achieved["area_um2"] < target * 1.2).all(), (
        f"Overshoot exceeded 20%: {result.achieved['area_um2'].tolist()}. The fill should stop "
        "as soon as the target is met."
    )


def test_an_area_budget_without_a_scale_is_rejected(cells_gdf):
    with pytest.raises(ValueError, match="needs the image scale"):
        selection.select(
            cells_gdf, [budget.ClassBudget("x", 1, 100.0)], budget.BudgetMode.AREA,
            selection.SelectionParams(), None,
        )


def test_a_class_with_no_shapes_still_appears_in_the_report(cells_gdf):
    """Otherwise a typo in a class name silently collects nothing with no explanation."""
    result = selection.select(
        cells_gdf, [budget.ClassBudget("does-not-exist", 2, 5)], budget.BudgetMode.CELLS,
        selection.SelectionParams(seed=1), CELLS_PIXEL_SIZE,
    )
    assert len(result.achieved) == 2, (
        f"Expected both replicates reported as empty, got {len(result.achieved)} rows."
    )
    assert (result.achieved["shapes"] == 0).all()
    assert not result.shortfalls.empty, "An empty class should be reported as a shortfall."


def test_grid_bins_never_lose_a_point(cells_gdf):
    """Every shape must land in exactly one bin, or shapes become unselectable."""
    points = _centroids(cells_gdf, cells_gdf.index)
    for target in (1, 5, 50, len(points), len(points) * 2):
        labels, centres = selection.grid_bins(points, target)
        assert len(labels) == len(points), f"Binning {len(points)} points into ~{target} bins returned {len(labels)} labels."
        assert labels.min() >= 0 and labels.max() < len(centres), (
            f"Bin labels fall outside the {len(centres)} centres returned."
        )


def test_exploded_classes_can_be_selected_from(cells_gdf):
    """After exploding, every shape is its own class — the single-cell path."""
    exploded = geojson.explode_classes(cells_gdf, ["single_cells_demo"])
    names = sorted(n for n in set(exploded[CLASS_NAME]) if n.startswith("single_cells_demo_"))[:3]
    result = selection.select(
        exploded, [budget.ClassBudget(n, 1, 1) for n in names], budget.BudgetMode.CELLS,
        selection.SelectionParams(seed=1), CELLS_PIXEL_SIZE,
    )
    assert result.n_selected == 3, f"Expected one shape from each of three exploded classes, got {result.n_selected}."
