"""The cell-segmentation workflow: pick classes, replicates, and how much to collect.

Phases 0–2 are in place: routing, image scale, and per-class statistics with a class
overview. Replicates, budgets and the selection engine arrive in Phases 3–5; see ROADMAP.md.
"""

import streamlit as st
from loguru import logger

from qupath_to_lmd import plot, stats, ui_shared


def class_selection_step(pixel_size_um: float, step: str = "5") -> list[str]:
    """Show what each class holds, then let the user choose which ones to collect."""
    gdf = st.session_state.gdf

    st.markdown(f"## Step {step}: Choose which classes to collect")
    st.markdown(
        "Areas below are computed from the shapes themselves and the image scale you "
        "entered, so they are real areas of tissue. Use them to judge what is worth "
        "collecting before deciding on amounts."
    )

    table = stats.class_statistics(gdf, pixel_size_um=pixel_size_um)
    st.dataframe(stats.for_display(table), width="stretch")

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
    st.write(
        f"**{int(kept['shapes'].sum()):,} shapes** across {len(selected)} classes, "
        f"totalling **{kept['area_total_um2'].sum():,.0f} µm²** of tissue."
    )
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


def render(uploaded_file) -> None:
    """The cell workflow as far as Phase 2 takes it."""
    pixel_size = ui_shared.pixel_size_step(step="4")
    st.divider()

    if st.session_state.gdf is None:
        st.info("Upload a GeoJSON to continue.")
        return
    if pixel_size is None:
        st.info("Enter the image scale above to continue — every area figure depends on it.")
        return

    selected = class_selection_step(pixel_size, step="5")
    if selected:
        overview_step(selected)
    st.divider()

    st.markdown("## Step 6: Replicates and amounts")
    st.info(
        "**Not built yet.** Coming next (see `ROADMAP.md`):\n\n"
        "- **Phase 3** — replicates per class, and a budget per replicate as either a cell "
        "count or a target area, with a feasibility check against the figures above\n"
        "- **Phase 4** — the selection itself: spatially spread by default, optional "
        "no-touching-cells constraint, and a preview of exactly what will be cut\n"
        "- **Phase 5** — smoothing and cut-path options at export\n\n"
        "Until then, the annotations workflow can collect these shapes one class per well, "
        "including splitting a class into one well per cell."
    )
