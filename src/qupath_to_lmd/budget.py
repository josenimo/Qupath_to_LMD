"""Replicates and per-replicate budgets, and whether a class can actually supply them.

Feasibility is reported, never enforced: a user may knowingly ask for more than a class
holds and accept a partially-filled replicate (`decisions.md` 003).
"""

from dataclasses import dataclass
from enum import Enum

import pandas
from loguru import logger

# Column names of the feasibility table, so the UI and the tests agree on them.
REPLICATES = "replicates"
PER_REPLICATE = "per_replicate"
REQUIRED = "required"
AVAILABLE = "available"
SHORTFALL = "shortfall"
ACHIEVABLE = "achievable_replicates"


class BudgetMode(str, Enum):
    """What a budget counts."""

    CELLS = "cells"
    AREA = "area"

    @property
    def unit(self) -> str:
        """How the amount is spelled in the interface."""
        return "shapes" if self is BudgetMode.CELLS else "µm²"

    @property
    def stats_column(self) -> str:
        """Which `stats.class_statistics` column holds the supply for this mode."""
        return "shapes" if self is BudgetMode.CELLS else "area_total_um2"


@dataclass(frozen=True)
class ClassBudget:
    """How much of one class to collect, and across how many replicates."""

    class_name: str
    replicates: int
    per_replicate: float

    @property
    def required(self) -> float:
        """Total demanded across every replicate of this class."""
        return self.replicates * self.per_replicate


def feasibility(
    stats: pandas.DataFrame,
    budgets: list[ClassBudget],
    mode: BudgetMode,
) -> pandas.DataFrame:
    """Compare what each class is asked for against what it holds.

    Args:
        stats: output of `stats.class_statistics`, indexed by class name.
        budgets: one entry per class the user is collecting.
        mode: whether budgets count shapes or µm².

    Returns:
        A frame indexed by class with the request, the supply, any shortfall, and how many
        whole replicates the class can actually fill.

    Raises:
        KeyError: `stats` lacks the column this mode needs — for area budgets that means no
            pixel size was given.
    """
    if mode.stats_column not in stats.columns:
        raise KeyError(
            f"Cannot check an {mode.value} budget: '{mode.stats_column}' is missing. "
            "Area budgets need the image scale."
        )

    rows = {}
    for item in budgets:
        available = float(stats.at[item.class_name, mode.stats_column])
        required = item.required
        rows[item.class_name] = {
            REPLICATES: item.replicates,
            PER_REPLICATE: item.per_replicate,
            REQUIRED: required,
            AVAILABLE: available,
            SHORTFALL: max(0.0, required - available),
            ACHIEVABLE: int(available // item.per_replicate) if item.per_replicate > 0 else 0,
        }

    table = pandas.DataFrame.from_dict(rows, orient="index")
    table.index.name = stats.index.name
    short = table[table[SHORTFALL] > 0]
    if not short.empty:
        logger.warning(f"{len(short)} classes cannot supply their budget: {list(short.index)}")

    return table


def group_keys(budgets: list[ClassBudget]) -> list[str]:
    """The `class_r<replicate>` key of every group the plan will produce.

    Depends only on the budgets, not on which shapes end up selected, so the plate layout can
    be shown before the selection runs.
    """
    return [
        f"{item.class_name}_r{replicate}"
        for item in budgets
        for replicate in range(1, item.replicates + 1)
    ]


def total_groups(budgets: list[ClassBudget]) -> int:
    """One well per replicate per class, so this is how many wells the plan needs."""
    return sum(item.replicates for item in budgets)


DISPLAY_COLUMNS = {
    REPLICATES: "Replicates",
    PER_REPLICATE: "Per replicate",
    REQUIRED: "Total requested",
    AVAILABLE: "Available",
    SHORTFALL: "Short by",
    ACHIEVABLE: "Replicates fillable",
}


def for_display(table: pandas.DataFrame, decimals: int = 2) -> pandas.DataFrame:
    """Rename and round the feasibility table for showing to a user."""
    columns = [c for c in DISPLAY_COLUMNS if c in table.columns]
    display = table[columns].copy()
    for column in (PER_REPLICATE, REQUIRED, AVAILABLE, SHORTFALL):
        if column in display.columns:
            display[column] = display[column].round(decimals)
    return display.rename(columns=DISPLAY_COLUMNS)
