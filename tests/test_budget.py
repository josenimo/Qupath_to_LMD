"""Replicates, budgets, and whether a class can supply them."""

import pytest

from qupath_to_lmd import budget, stats
from tests.conftest import CELLS_PIXEL_SIZE


def test_group_keys_come_from_budgets_alone():
    """The plate layout is shown before the selection runs, which is only possible because the
    group names depend on the budgets and not on which shapes get chosen."""
    budgets = [budget.ClassBudget("Tumor", 3, 100), budget.ClassBudget("Immune", 2, 100)]
    assert budget.group_keys(budgets) == ["Tumor_r1", "Tumor_r2", "Tumor_r3", "Immune_r1", "Immune_r2"], (
        f"Group keys came out as {budget.group_keys(budgets)}. The plate assignment and the "
        "selection both key off these, so the format matters."
    )
    assert budget.total_groups(budgets) == 5, "One well is needed per replicate per class."


@pytest.mark.parametrize(
    ("mode", "column", "unit"),
    [(budget.BudgetMode.CELLS, "shapes", "shapes"), (budget.BudgetMode.AREA, "area_total_um2", "µm²")],
)
def test_each_mode_points_at_its_own_supply_column(mode, column, unit):
    assert mode.stats_column == column
    assert mode.unit == unit


def test_a_feasible_budget_reports_no_shortfall(cells_gdf):
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    budgets = [budget.ClassBudget(name, 1, 1) for name in table.index]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)
    assert (check[budget.SHORTFALL] == 0).all(), (
        f"One shape per class from a file with {int(table['shapes'].sum())} shapes was reported "
        f"as short: \n{check}"
    )


def test_an_infeasible_budget_reports_the_shortfall_and_what_can_be_filled(cells_gdf):
    """Asking for more than a class holds is a decision to make knowingly. The actionable
    number is how many whole replicates the class can actually fill."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    biggest = table.index[0]
    available = int(table.at[biggest, "shapes"])
    budgets = [budget.ClassBudget(biggest, 4, available)]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)

    row = check.loc[biggest]
    assert row[budget.REQUIRED] == 4 * available
    assert row[budget.SHORTFALL] == 3 * available, (
        f"Asking for 4x the whole class should be short by 3x it; reported {row[budget.SHORTFALL]}."
    )
    assert row[budget.ACHIEVABLE] == 1, (
        f"The class holds exactly one full replicate's worth, so 1 should be fillable; "
        f"reported {row[budget.ACHIEVABLE]}."
    )


def test_an_area_budget_without_a_scale_fails_loudly(cells_gdf):
    """Area budgets need a scale. Failing in the library rather than silently skipping the
    check means the UI cannot show a feasibility table built on nothing."""
    counts_only = stats.class_statistics(cells_gdf, pixel_size_um=None)
    budgets = [budget.ClassBudget(name, 1, 100.0) for name in counts_only.index]
    with pytest.raises(KeyError, match="Area budgets need the image scale"):
        budget.feasibility(counts_only, budgets, budget.BudgetMode.AREA)


def test_a_zero_amount_yields_no_fillable_replicates(cells_gdf):
    """A zero budget collects nothing, and dividing by it must not raise."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    budgets = [budget.ClassBudget(table.index[0], 2, 0)]
    check = budget.feasibility(table, budgets, budget.BudgetMode.CELLS)
    assert check.iloc[0][budget.ACHIEVABLE] == 0


def test_display_renames_and_rounds_without_touching_the_replicate_count(cells_gdf):
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    budgets = [budget.ClassBudget(name, 2, 3.14159) for name in table.index]
    display = budget.for_display(budget.feasibility(table, budgets, budget.BudgetMode.CELLS))
    assert budget.DISPLAY_COLUMNS[budget.REPLICATES] in display.columns
    assert display[budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]].iloc[0] == pytest.approx(3.14)
    assert display[budget.DISPLAY_COLUMNS[budget.REPLICATES]].iloc[0] == 2, (
        "Replicate counts must stay exact integers; they are not measurements."
    )
