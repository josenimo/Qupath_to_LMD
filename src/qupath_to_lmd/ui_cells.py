"""The cell-segmentation workflow: pick classes, replicates, and how much to collect.

Phase 1 puts the scaffolding in place — workflow routing, and the image scale that every
area figure depends on. The parts that make it useful arrive in later phases; see
ROADMAP.md.
"""

import streamlit as st

from qupath_to_lmd import ui_shared
from qupath_to_lmd.model import CLASS_NAME


def render(uploaded_file) -> None:
    """The cell workflow as far as Phase 1 takes it."""
    pixel_size = ui_shared.pixel_size_step(step="4")
    st.divider()

    if st.session_state.gdf is None:
        st.info("Upload a GeoJSON to continue.")
        return

    n_classes = st.session_state.gdf[CLASS_NAME].nunique()
    n_shapes = len(st.session_state.gdf)
    st.markdown("## Step 5: Choose classes, replicates and amounts")
    st.write(f"This file has {n_shapes} shapes across {n_classes} classes.")

    if pixel_size is None:
        st.info("Enter the image scale above before continuing — every area figure depends on it.")
        return

    st.info(
        "**Not built yet.** This workflow is being added in stages (see `ROADMAP.md`):\n\n"
        "- **Phase 2** — per-class statistics: cell counts, total and per-cell area in µm², "
        "density, so you can see what is available before committing\n"
        "- **Phase 3** — replicates per class, and a budget per replicate as either a cell "
        "count or a target area, with a feasibility check\n"
        "- **Phase 4** — the selection itself: spatially spread by default, optional "
        "no-touching-cells constraint, and a live preview of what will be cut\n"
        "- **Phase 5** — smoothing and cut-path options at export\n\n"
        "In the meantime, the annotations workflow above can already collect these shapes "
        "one class per well, including splitting a class into one well per cell."
    )
