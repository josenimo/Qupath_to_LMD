"""The cell-segmentation workflow: pick classes, replicates, and how much to collect.

Phases 0–3 are in place: routing, image scale, per-class statistics, and replicates with
budgets. The selection engine itself is Phase 4; see ROADMAP.md.
"""

import hashlib

import pandas
import streamlit as st
from loguru import logger

from qupath_to_lmd import budget, export, plot, selection, stats, ui_shared
from qupath_to_lmd.model import CLASS_NAME, plan_from_selection


def class_selection_step(pixel_size_um: float | None, step: str = "5") -> list[str]:
    """Show what each class holds, then let the user choose which ones to collect."""
    gdf = st.session_state.gdf

    st.markdown(f"## Step {step}: Choose which classes to collect")
    if pixel_size_um:
        st.markdown(
            "Areas below are computed from the shapes themselves and the image scale you "
            "entered, so they are real areas of tissue. Use them to judge what is worth "
            "collecting before deciding on amounts."
        )
    else:
        st.markdown(
            "Without an image scale only shape counts can be shown, which is all you need if "
            "you intend to collect a number of cells. Enter a scale above if you would rather "
            "work in areas."
        )

    table = stats.class_statistics(gdf, pixel_size_um=pixel_size_um)
    display = stats.for_display(table)
    # Columns stay numeric so the table remains sortable; the format only trims the display.
    st.dataframe(
        display,
        width="stretch",
        column_config={
            name: st.column_config.NumberColumn(name, format=f"%.{stats.DECIMALS}f")
            for name in display.columns
            if name != stats.DISPLAY_COLUMNS["shapes"]
        },
    )

    all_classes = table.index.tolist()
    selected = st.multiselect(
        "Classes to collect",
        options=all_classes,
        default=st.session_state.selected_classes or all_classes,
        help="Everything after this step works only on the classes you keep here.",
    )

    if selected != st.session_state.selected_classes:
        st.session_state.selected_classes = selected
        logger.info(f"Classes selected: {selected}")

    if not selected:
        st.warning("No classes selected, so there is nothing to collect yet.")
        return []

    kept = table.loc[selected]
    summary = f"**{int(kept['shapes'].sum()):,} shapes** across {len(selected)} classes"
    if "area_total_um2" in kept.columns:
        summary += f", totalling **{kept['area_total_um2'].sum():,.{stats.DECIMALS}f} µm²** of tissue"
    st.write(summary + ".")
    return selected


def overview_step(selected: list[str]) -> None:
    """Draw the shapes, colouring the chosen classes and greying out the rest."""
    gdf = st.session_state.gdf
    with st.spinner("Drawing shapes..."):
        figure = plot.plot_shapes(
            gdf,
            included=selected,
            calibration_array=st.session_state.calib_array,
            title=f"{len(gdf)} shapes — coloured classes are the ones you kept",
        )
    st.pyplot(figure, width="content")
    if len(gdf) > plot.POLYGON_LIMIT:
        st.caption(
            f"Over {plot.POLYGON_LIMIT:,} shapes, so each one is drawn as a dot rather than "
            "its outline. The outlines are still what gets cut."
        )
    st.caption(
        "Dashed triangle and crosses are your calibration points. Shapes far outside the "
        "triangle are the ones at risk of distortion."
    )


def _budget_mode(pixel_size_um: float | None) -> budget.BudgetMode:
    """Let the user choose what a budget counts. Area needs a scale; cells never do."""
    labels = {
        budget.BudgetMode.CELLS: "Number of cells per replicate",
        budget.BudgetMode.AREA: "Area of tissue per replicate (µm²)",
    }
    options = [budget.BudgetMode.CELLS]
    if pixel_size_um:
        options.append(budget.BudgetMode.AREA)
    else:
        st.caption(
            "Budgeting by area needs the image scale from step 4. Collecting by number of "
            "cells works without it."
        )

    return st.radio(
        "Budget by",
        options=options,
        format_func=lambda mode: labels[mode],
        horizontal=True,
        key="budget_mode_choice",
    )


def budgets_step(selected: list[str], pixel_size_um: float | None, step: str = "6") -> list[budget.ClassBudget]:
    """Replicates and per-replicate amount for each class, with a feasibility check."""
    st.markdown(f"## Step {step}: Replicates and amounts")
    st.markdown(
        "Each replicate of each class is collected into its own well. Set how many "
        "replicates you want and how much goes into each one."
    )

    mode = _budget_mode(pixel_size_um)
    table = stats.class_statistics(st.session_state.gdf, pixel_size_um=pixel_size_um)
    supply = table.loc[selected, mode.stats_column]

    # Default to the whole class in a single replicate — the same thing the annotations
    # workflow would do — so the starting point is neutral rather than an invented number.
    editable = pandas.DataFrame(
        {
            budget.DISPLAY_COLUMNS[budget.REPLICATES]: 1,
            budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]: supply.round(stats.DECIMALS),
        },
        index=supply.index,
    )
    # A key tied to the selection and mode, so changing either gives a fresh editor rather
    # than leaving rows from the previous one behind.
    signature = hashlib.md5(("|".join(sorted(selected)) + mode.value).encode()).hexdigest()[:8]
    edited = st.data_editor(
        editable,
        width="stretch",
        key=f"budget_editor_{signature}",
        column_config={
            budget.DISPLAY_COLUMNS[budget.REPLICATES]: st.column_config.NumberColumn(
                budget.DISPLAY_COLUMNS[budget.REPLICATES], min_value=1, step=1, format="%d"
            ),
            budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]: st.column_config.NumberColumn(
                f"Per replicate ({mode.unit})", min_value=0.0, format=f"%.{stats.DECIMALS}f"
            ),
        },
    )

    budgets = [
        budget.ClassBudget(
            class_name=str(class_name),
            replicates=int(row[budget.DISPLAY_COLUMNS[budget.REPLICATES]] or 1),
            per_replicate=float(row[budget.DISPLAY_COLUMNS[budget.PER_REPLICATE]] or 0),
        )
        for class_name, row in edited.iterrows()
    ]

    _report_feasibility(table, budgets, mode)
    st.session_state.budget_mode = mode.value
    st.session_state.budgets = [vars(item) for item in budgets]
    return budgets


def _report_feasibility(table: pandas.DataFrame, budgets: list[budget.ClassBudget], mode: budget.BudgetMode) -> None:
    """Show what each class is asked for against what it holds, and warn on shortfalls."""
    check = budget.feasibility(table, budgets, mode)
    st.dataframe(budget.for_display(check), width="stretch")

    short = check[check[budget.SHORTFALL] > 0]
    if not short.empty:
        lines = "\n".join(
            f"- **{name}**: asked for {row[budget.REQUIRED]:,.{stats.DECIMALS}f} {mode.unit}, "
            f"has {row[budget.AVAILABLE]:,.{stats.DECIMALS}f} — enough for "
            f"{int(row[budget.ACHIEVABLE])} full replicate(s)"
            for name, row in short.iterrows()
        )
        st.warning(
            f"{len(short)} class(es) cannot supply what you asked for:\n\n{lines}\n\n"
            "You can continue — those replicates will be filled as far as the class allows — "
            "or reduce the amount or the number of replicates."
        )
    else:
        st.success("Every class can supply its budget.")

    zero = [item.class_name for item in budgets if item.per_replicate <= 0]
    if zero:
        st.warning(f"Nothing will be collected for: {', '.join(zero)} — the amount is zero.")


def capacity_step(budgets: list[budget.ClassBudget], step: str = "7") -> dict:
    """Plate settings, and whether the plan fits on the plate."""
    st.markdown(f"## Step {step}: Plate")
    settings = ui_shared.plate_settings_step(step=step)

    needed = budget.total_groups(budgets)
    usable = len(settings["wells"])
    st.write(f"This plan needs **{needed} wells**, one per replicate. This plate offers **{usable}**.")
    if needed > usable:
        st.warning(
            f"{needed - usable} more wells are needed than the plate offers. Reduce the "
            "replicates, lower the margin or spacing, or use a 384 well plate."
        )
    return settings


def _selection_params(step: str) -> selection.SelectionParams:
    """Mode, the adjacency constraint, and the seed that makes a selection reproducible."""
    mode_column, adjacency_column, seed_column = st.columns([3, 3, 2])

    with mode_column:
        mode = st.radio(
            "How to choose shapes within a class",
            options=list(selection.SelectionMode),
            format_func=lambda m: {
                selection.SelectionMode.SPREAD: "Spread out across the tissue (recommended)",
                selection.SelectionMode.RANDOM: "Random",
            }[m],
            key=f"selection_mode_{step}",
            help=(
                "Spread lays a grid over each class and takes the shape nearest each grid "
                "square, so a replicate samples the whole class rather than one corner of it. "
                "Random draws without regard to position, which is unbiased but clumps."
            ),
        )

    with adjacency_column:
        allow_adjacent = st.checkbox(
            "Allow touching shapes to be collected",
            value=True,
            key=f"allow_adjacent_{step}",
            help=(
                "When unticked, no two collected shapes may touch or overlap, judged on the "
                "original QuPath outlines. Neighbouring cells share a boundary, so cutting "
                "both risks collecting parts of each into the wrong well."
            ),
        )

    with seed_column:
        seed = st.number_input(
            "Seed", min_value=0, max_value=10_000, value=0, step=1, key=f"seed_{step}",
            help="Same seed, same selection. Recorded in provenance.json so you can report it.",
        )

    return selection.SelectionParams(mode=mode, allow_adjacent=allow_adjacent, seed=int(seed))


def selection_step(budgets, settings: dict, pixel_size_um: float | None, step: str = "8") -> None:
    """Choose the shapes, preview them, and offer the export."""
    st.markdown(f"## Step {step}: Select shapes and export")
    params = _selection_params(step)

    mode = budget.BudgetMode(st.session_state.budget_mode or budget.BudgetMode.CELLS.value)
    try:
        with st.spinner("Choosing shapes..."):
            result = selection.select(
                st.session_state.gdf, budgets, mode, params, pixel_size_um=pixel_size_um
            )
    except ValueError as error:
        st.error(str(error))
        return

    if result.n_selected == 0:
        st.warning("Nothing was selected. Check the amounts in step 6.")
        return

    _report_selection(result, mode)
    _preview_selection(result)
    _export_selection(result, settings, params, pixel_size_um)


def _report_selection(result: selection.SelectionResult, mode: budget.BudgetMode) -> None:
    """Achieved against requested, per replicate."""
    st.write(f"**{result.n_selected:,} shapes** selected across {len(result.achieved)} replicates.")
    st.dataframe(result.achieved.round(stats.DECIMALS), width="stretch")

    short = result.shortfalls
    if not short.empty:
        lines = "\n".join(
            f"- **{row[CLASS_NAME]} replicate {int(row['replicate'])}**: got "
            f"{row['achieved']:,.{stats.DECIMALS}f} of {row['requested']:,.{stats.DECIMALS}f} {mode.unit}"
            for _, row in short.iterrows()
        )
        st.warning(
            f"{len(short)} replicate(s) could not be filled completely:\n\n{lines}\n\n"
            "They will still be collected, just with less material than you asked for."
        )
    if result.n_blocked_by_adjacency:
        st.caption(
            f"{result.n_blocked_by_adjacency} candidate shapes were skipped because they touch "
            "a shape already being collected."
        )


def _preview_selection(result: selection.SelectionResult) -> None:
    """Draw what will be cut, coloured by replicate."""
    labels = result.replicate_of.map(lambda value: f"replicate {int(value)}" if pandas.notna(value) else None)
    with st.spinner("Drawing the selection..."):
        figure = plot.plot_shapes(
            st.session_state.gdf,
            labels=labels,
            calibration_array=st.session_state.calib_array,
            title="What will be cut, coloured by replicate",
        )
    st.pyplot(figure, width="content")
    st.caption(
        "Classes are merged here so you can judge whether the replicates are spread and "
        "comparable. Replicates drawn from the same grid square sit next to each other by "
        "design — untick *allow touching shapes* above if that is a problem for cutting."
    )


def _export_selection(result, settings: dict, params: selection.SelectionParams, pixel_size_um) -> None:
    """Assign wells, build the plan, and hand off to the shared export step."""
    plan, samples_and_wells = plan_from_selection(
        gdf=st.session_state.gdf,
        replicate_of=result.replicate_of,
        wells=settings["wells"],
        calibration_names=st.session_state.calibs,
        calibration_array=st.session_state.calib_array,
        source_file=st.session_state.file_name,
        session_id=st.session_state.session_id,
        pixel_size_um=pixel_size_um,
        params={
            "simplify_tolerance_px": export.DEFAULT_SIMPLIFY_TOLERANCE,
            "plate": settings["plate_type"],
            "margins": settings["margins"],
            "step_row": settings["step_row"],
            "step_col": settings["step_col"],
            "selection_mode": params.mode.value,
            "allow_adjacent": params.allow_adjacent,
            "seed": params.seed,
            "budget_mode": st.session_state.budget_mode,
            "budgets": st.session_state.budgets,
        },
    )

    unplaced = sorted(set(plan.shapes.loc[plan.shapes["group_key"].notna(), "group_key"]) - set(samples_and_wells))
    if unplaced:
        st.error(
            f"{len(unplaced)} group(s) have no well on this plate and will not be cut: "
            f"{', '.join(unplaced[:8])}. Reduce replicates or use a larger plate."
        )

    st.session_state.saw = samples_and_wells
    with st.expander(f"Well assignment ({len(samples_and_wells)} wells)", expanded=False):
        st.write(samples_and_wells)

    ui_shared.export_step(settings, lambda _settings: plan, step="9")


def render(uploaded_file) -> None:
    """The cell workflow as far as Phase 3 takes it."""
    pixel_size = ui_shared.pixel_size_step(step="4")
    st.divider()

    if st.session_state.gdf is None:
        st.info("Upload a GeoJSON to continue.")
        return

    selected = class_selection_step(pixel_size, step="5")
    if not selected:
        return
    overview_step(selected)
    st.divider()

    budgets = budgets_step(selected, pixel_size, step="6")
    st.divider()

    settings = capacity_step(budgets, step="7")
    st.divider()

    selection_step(budgets, settings, pixel_size, step="8")
