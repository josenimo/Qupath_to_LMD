"""The legacy workflow: one QuPath class is one sample is one well.

Frozen as of Phase 1 — later phases must not change what this produces. The golden
harness (`tools/golden_harness.py`) is what enforces that.
"""

import streamlit as st
from loguru import logger

from qupath_to_lmd import geojson, ui_shared
from qupath_to_lmd.model import CLASS_NAME, plan_from_class_wells


def explode_step(step: str = "4") -> None:
    """Optional: give every shape of a class its own class, for single-cell collection."""
    if st.session_state.gdf is None:
        return

    st.markdown(f"## Step {step} (optional): Split a class into many classes")
    st.markdown(
        "For one or more classes below. For every shape belonging to a selected class, "
        "a unique, numbered ID will be created (e.g., 'T-Cell' -> 'T-Cell_001', 'T-Cell_002'). "
        "This is useful for single-cell collection."
    )

    all_classes = st.session_state.gdf[CLASS_NAME].unique().tolist()
    chosen = st.multiselect("Select classes to make unique:", options=all_classes)

    if st.button("Generate Unique Names"):
        logger.info("Generate Unique Names button clicked")
        if not chosen:
            st.warning("Please select at least one class to make unique.")
        else:
            st.session_state.gdf = geojson.explode_classes(st.session_state.gdf, chosen)
            st.session_state.saw = None
            st.session_state.plate_df = None
            st.info("Chosen classes were split up, check below.")


def _build_plan(settings: dict):
    """One class, one well. Exploded classes already have per-shape names, so they just work."""
    return plan_from_class_wells(
        gdf=st.session_state.gdf,
        samples_and_wells=st.session_state.saw,
        calibration_names=st.session_state.calibs,
        calibration_array=st.session_state.calib_array,
        source_file=st.session_state.file_name,
        session_id=st.session_state.session_id,
        params={
            "plate": settings["plate_type"],
            "margins": settings["margins"],
            "step_row": settings["step_row"],
            "step_col": settings["step_col"],
            "samples_and_wells_source": "plate builder" if st.session_state.use_plate_wells else "uploaded",
        },
    )


def render(uploaded_file) -> None:
    """The whole legacy workflow, below the shared upload and calibration steps."""
    explode_step(step="4")
    st.divider()

    settings = ui_shared.plate_settings_step(step="5")
    ui_shared.plate_layout_step(settings, uploaded_file)
    st.divider()

    ui_shared.export_step(settings, _build_plan, step="6")
