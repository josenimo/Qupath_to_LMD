import sys
import tempfile
import uuid

import streamlit as st
from loguru import logger

from qupath_to_lmd import ui_cut, ui_plates, ui_samples, ui_shared, ui_slides, ui_summary

####################
## Page settings ###
####################
st.set_page_config(layout="wide")

DEFAULTS = {
    "session_id": None,          # set below, needs a fresh uuid
    "log_file_path": None,
    # Stage 1, Slides
    "slides": None,
    "upload_key": None,
    "calibration": {},
    "pixel_size_by_slide": {},
    # Stage 2, Samples
    "workflow": "legacy",
    "selected_classes": None,
    "budget_mode": None,
    "budgets": None,
    "minimum_area_um2": None,
    "region_params": None,
    "packing_params": None,
    "region_budgets": None,
    "slide_strategy": None,
    "slide_order": None,
    # Stage 3, Plates
    "n_plates": None,
    "plate_distribution": None,
    # Stage 4, Cut
    "cut_order": None,
    "zip_buffer": None,
    "bundle_name": None,
    "bundle_signature": None,
    "collection_image": None,
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value
if st.session_state.session_id is None:
    st.session_state.session_id = str(uuid.uuid4())

# Configure logging. The log file ships inside the download bundle, so it is part of the
# support story: a user can send it with a Github issue.
if st.session_state.log_file_path is None:
    st.session_state.log_file_path = tempfile.NamedTemporaryFile(delete=False, suffix=".log").name

logger.remove()
logger.add(st.session_state.log_file_path, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}")
logger.add(sys.stdout, colorize=True, format="<green>{time:HH:mm:ss.SS}</green> | <level>{level}</level> | {message}", level="DEBUG")

####################
### Introduction ###
####################
st.markdown("# Turn QuPath shapes into a Laser Microdissection cutting file")
st.markdown(
    "[openDVP framework](https://github.com/CosciaLab/openDVP) · "
    "[Report an issue](https://github.com/CosciaLab/Qupath_to_LMD/issues) · "
    "[Glossary](https://github.com/CosciaLab/Qupath_to_LMD/blob/master/GLOSSARY.md)"
)
st.caption(
    f"Session id `{st.session_state.session_id}` — include it, and your .geojson, when "
    "reporting an issue."
)
st.divider()

#################################################
### Four stages: slides, samples, plates, cut ###
#################################################
# Each stage hands the next a plain object — slides, a SampleSet, a PlateLayout — so a stage
# can change without the others knowing (`decisions.md` 078).

summary = ui_summary.Summary.start()
# Filled before the stages, so a hard stop in a stage never takes the extras away with it.
with summary.extras:
    ui_shared.extras_step()

context = ui_slides.render()
st.divider()
if context is not None:
    summary.slides(context)
    sample_set = ui_samples.render(context)
    st.divider()
    if sample_set is not None:
        summary.samples(sample_set, ui_samples.methods()[st.session_state.workflow].LABEL)
        layout = ui_plates.render(sample_set)
        st.divider()
        if layout is not None:
            summary.plates(layout, len(sample_set.samples))
            summary.cut(ui_cut.render(context, sample_set, layout))
            st.divider()
