import json
import sys
import tempfile
import uuid
from pathlib import Path

import streamlit as st
from loguru import logger

from qupath_to_lmd import export, extras, geojson, plate, qc
from qupath_to_lmd.model import CLASS_NAME, plan_from_class_wells

####################
## Page settings ###
####################
st.set_page_config(layout="wide")

DEFAULTS = {
    "session_id": None,          # set below, needs a fresh uuid
    "view_mode": "default",
    "gdf": None,
    "geojson_report": None,
    "calibration_points": None,
    "calibs": None,
    "calib_array": None,
    "saw": None,
    "use_plate_wells": True,
    "file_name": None,
    "plate_df": None,
    "plate_gen_params": None,
    "show_saw_uploader": False,
    "zip_buffer": None,
    "bundle_name": None,
    "collection_image": None,
    "log_file_path": None,
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


@st.cache_data(show_spinner="Reading and checking your GeoJSON...")
def read_geojson(source):
    """Cached wrapper so a rerun does not re-parse the same upload."""
    return geojson.read_and_qc(source)


def reset_file_state():
    """Forget the uploaded file and everything derived from it."""
    for key in ("gdf", "geojson_report", "calibration_points", "calibs", "calib_array", "file_name"):
        st.session_state[key] = None


####################
### Introduction ###
####################
st.markdown("""
            # Convert a GeoJSON polygons for Laser Microdissection
            ## Part of the [openDVP](https://github.com/CosciaLab/openDVP) framework
            ### For help, post issue on [Github](https://github.com/CosciaLab/Qupath_to_LMD) with .geojson file and session id
            """)
st.write(f" Session id: {st.session_state.session_id}")
st.divider()

############################
## Step 1: Geojson upload ##
############################

st.markdown("""
            ## Step 1: Upload and check .geojson file from Qupath
            Upload your .geojson file from qupath, order of calibration points is important
            """)

uploaded_file = st.file_uploader(label="Choose a file", type="geojson", accept_multiple_files=False)

if uploaded_file:
    if st.session_state.file_name != uploaded_file.name or st.session_state.gdf is None:
        logger.info(f"New file detected: {uploaded_file.name}")
        try:
            gdf, calibration_points, report = read_geojson(uploaded_file)
        except geojson.GeojsonError as error:
            st.error(str(error))
            logger.error(str(error))
            st.stop()
        st.session_state.file_name = uploaded_file.name
        st.session_state.gdf = gdf
        st.session_state.calibration_points = calibration_points
        st.session_state.geojson_report = report

    report = st.session_state.geojson_report
    counts = ", ".join(f"{count} {name}s" for name, count in report.geometry_counts.items())
    st.write(f"Geometries in file: {counts}")

    if report.n_unclassified_dropped:
        st.warning(
            f"{report.n_unclassified_dropped} objects have no QuPath classification. "
            "These are unclassified objects and cannot be assigned to a well, so they are ignored."
        )
    if report.n_multipolygons_dropped:
        st.warning(
            f"{report.n_multipolygons_dropped} MultiPolygon objects found. These are not supported — "
            "please split them into single polygons in QuPath. Processing continues without them."
        )
        st.table(report.multipolygons)

    st.success(f"File check complete, {report.n_shapes_kept} shapes available.")

    if st.session_state.calibration_points:
        calib_options = list(st.session_state.calibration_points)

        c1 = st.selectbox("Select calibration point 1", calib_options, index=0 if len(calib_options) > 0 else None)
        c2 = st.selectbox("Select calibration point 2", calib_options, index=1 if len(calib_options) > 1 else None)
        c3 = st.selectbox("Select calibration point 3", calib_options, index=2 if len(calib_options) > 2 else None)

        st.session_state.calibs = [c1, c2, c3]
        logger.info(f"Calibration points chosen: {st.session_state.calibs}")

        if all(st.session_state.calibs):
            triangle = qc.triangle_qc(
                st.session_state.gdf,
                st.session_state.calibration_points,
                st.session_state.calibs,
            )
            st.session_state.calib_array = triangle.calibration_array
            st.write(f"{triangle.fraction_inside * 100:.2f}% of shapes are inside the calibration triangle")
            if triangle.is_concerning:
                st.warning(
                    "Less than 25% of your shapes fall inside the calibration triangle. Shapes far "
                    "outside it get distorted by the coordinate transform, so you may cut the wrong "
                    "tissue. Consider calibration points closer to your annotations."
                )
    else:
        st.warning("No calibration points found in the GeoJSON file.")

else:
    if st.session_state.file_name is not None:
        reset_file_state()

st.divider()

##########################################################
## Step 1.1 (Optional): Split a class into many classes ##
##########################################################

if st.session_state.gdf is not None:
    st.markdown("## Step 1.1 (Optional): Split a class into many classes")
    st.markdown(
        "For one or more classes below. For every shape belonging to a selected class, "
        "a unique, numbered ID will be created (e.g., 'T-Cell' -> 'T-Cell_001', 'T-Cell_002'). "
        "This is useful for single-cell collection."
    )

    all_classes = st.session_state.gdf[CLASS_NAME].unique().tolist()
    classes_to_make_unique = st.multiselect("Select classes to make unique:", options=all_classes)

    if st.button("Generate Unique Names"):
        logger.info("Generate Unique Names button clicked")
        if not classes_to_make_unique:
            st.warning("Please select at least one class to make unique.")
        else:
            st.session_state.gdf = geojson.explode_classes(st.session_state.gdf, classes_to_make_unique)
            st.session_state.saw = None
            st.session_state.plate_df = None
            st.info("Chosen classes were split up, check below.")

st.divider()

########################################
## Step 2: Choose collection settings ##
########################################

st.markdown("""
            ## Step 2: Decide which plate to collect into, either 384 or 96 well plate.
            Decide how many wells to make unavailable as a margin (for 384wp we suggest a margin of 2).
            Decide how many wells to leave blank in between, for easier pipetting.
            """)

st.write("You can increase plate size by dragging bottom right corner")

plate_col, margin_col, step_row_col, step_col_col = st.columns(4)
with plate_col:
    plate_string = st.selectbox("Select a plate type", ("384 well plate", "96 well plate"))
with margin_col:
    margin_int = st.number_input("Margin (integer)", min_value=0, max_value=10, value=1)
with step_row_col:
    step_row_int = st.number_input("Space between rows", min_value=1, max_value=10, value=1)
with step_col_col:
    step_col_int = st.number_input("Space between columns", min_value=1, max_value=10, value=1)

plate_type = plate_string.split(" ")[0]
wells = plate.acceptable_wells(
    plate=plate_type, margins=margin_int, step_row=step_row_int, step_col=step_col_int
)

#####################################
## Step 2.1: User inputs for plate ##
#####################################

col1, col2, col3 = st.columns(3)
with col1:
    if st.button("Show plate format with default wells"):
        st.session_state.view_mode = "default"
        logger.info("Show plate format with default wells -- ButtonPress")
with col2:
    if st.button("Show plate format with samples from geojson"):
        logger.info("Show plate format with samples from geojson -- ButtonPress")
        if uploaded_file is None:
            st.warning("Please upload a file first.")
        else:
            st.session_state.view_mode = "samples"
with col3:
    randomize_toggle = st.toggle("Randomize samples", value=False)

plate_gen_params = {
    "plate_type": plate_type,
    "margins": margin_int,
    "step_row": step_row_int,
    "step_col": step_col_int,
    "randomize": randomize_toggle,
}
params_have_changed = st.session_state.plate_gen_params != plate_gen_params

##############################
## Step 2.2 Plot dataframe ##
##############################

if st.session_state.view_mode == "default":
    layout = plate.default_layout(plate=plate_type)
    st.dataframe(layout.style.map(plate.highlight(set(wells))), width="stretch")

elif st.session_state.view_mode == "samples":
    if uploaded_file is None:
        st.warning("File no longer available. Please upload a file or switch to the default view.")
    elif st.session_state.gdf is not None:
        if params_have_changed or st.session_state.plate_df is None:
            st.session_state.plate_gen_params = plate_gen_params
            layout, unplaced = plate.sample_layout(
                classes=st.session_state.gdf[CLASS_NAME].unique().tolist(),
                plate=plate_type,
                wells=wells,
                randomize=randomize_toggle,
            )
            st.session_state.plate_df = layout
            if unplaced:
                st.warning(
                    f"{len(unplaced)} classes do not fit in the {len(wells)} usable wells and are "
                    f"not placed: {', '.join(unplaced[:10])}{' ...' if len(unplaced) > 10 else ''}. "
                    "Reduce the margin or spacing, use a 384 well plate, or collect in two rounds."
                )
        if st.session_state.plate_df is not None:
            classes = set(st.session_state.gdf[CLASS_NAME])
            st.dataframe(st.session_state.plate_df.style.map(plate.highlight(classes)), width="stretch")

if st.button("Confirm and use this plate layout"):
    logger.info("Confirm and use this plate layout -- ButtonPress")
    if st.session_state.view_mode == "samples" and st.session_state.plate_df is not None:
        st.session_state.saw = plate.layout_to_saw(st.session_state.plate_df)
        st.session_state.use_plate_wells = True
        report = qc.validate_saw(
            st.session_state.saw,
            st.session_state.gdf[CLASS_NAME].unique().tolist(),
            plate=plate_type,
        )
        if report.missing_classes:
            st.warning(
                f"{len(report.missing_classes)} classes in your file have no well and will not be "
                f"collected: {', '.join(sorted(report.missing_classes)[:10])}"
            )
        if report.invalid_wells:
            st.error(f"These wells do not exist on a {plate_type} well plate: {sorted(report.invalid_wells)}")
        else:
            st.success("Samples and wells layout confirmed, you are ready for Step 3!")
    else:
        st.warning("Please generate and view a plate layout with samples from your GeoJSON first.")

if st.session_state.saw is not None and st.session_state.use_plate_wells:
    st.download_button(
        label="Download samples and wells setup",
        data=json.dumps(st.session_state.saw, indent=4),
        file_name="samples_and_wells.json",
        mime="application/json",
    )
    with st.expander("View Samples and Wells Dictionary", expanded=False):
        st.write(st.session_state.saw)

#################################################
### Step 2.3 : Upload Custom Samples and Wells ##
#################################################

if st.button("Upload custom samples and wells dictionary, will override"):
    logger.info("Upload custom samples and wells dictionary -- ButtonPress")
    st.session_state.show_saw_uploader = True

if st.session_state.show_saw_uploader:
    uploaded_saw = st.file_uploader(
        label="Choose a custom samples-and-wells file (.txt or .json)",
        type=["txt", "json"],
        accept_multiple_files=False,
        key="saw_uploader",
    )
    if uploaded_saw is not None:
        try:
            candidate = plate.parse_saw_file(uploaded_saw)
        except plate.SawParseError as error:
            st.error(f"Could not read that samples-and-wells file: {error}")
            logger.error(f"Samples-and-wells parse failed: {error}")
        else:
            if st.session_state.gdf is None:
                st.warning("Upload a GeoJSON first, so the scheme can be checked against your classes.")
            else:
                report = qc.validate_saw(
                    candidate,
                    st.session_state.gdf[CLASS_NAME].unique().tolist(),
                    plate=plate_type,
                )
                if report.missing_classes:
                    st.warning(
                        f"Classes in your file with no well, they will not be collected: "
                        f"{', '.join(sorted(report.missing_classes)[:10])}"
                    )
                if report.duplicate_wells:
                    st.warning(f"Wells receiving more than one class: {report.duplicate_wells}")
                if report.invalid_wells:
                    st.error(
                        f"These wells do not exist on a {plate_type} well plate: "
                        f"{sorted(report.invalid_wells)}. Fix the file or change the plate type."
                    )
                else:
                    st.session_state.saw = candidate
                    st.session_state.use_plate_wells = False
                    st.session_state.show_saw_uploader = False
                    st.success(f"Custom samples and wells loaded and checked: {len(candidate)} classes.")

###############################
### Step 3: Process contours ##
###############################

st.markdown("""
            ## Step 3: Process to create .xml file for LMD
            Here we create the .xml file from your geojson.
            Please download the QC image, and plate scheme for future reference.
            """)

if st.button("Process files"):
    logger.info("Process files button clicked")
    if st.session_state.gdf is None:
        st.warning("Please upload a GeoJSON file first.")
    elif st.session_state.saw is None:
        st.warning("Please confirm a plate layout or upload a samples-and-wells scheme first.")
    elif st.session_state.calib_array is None:
        st.warning("Please select three calibration points first.")
    else:
        plan = plan_from_class_wells(
            gdf=st.session_state.gdf,
            samples_and_wells=st.session_state.saw,
            calibration_names=st.session_state.calibs,
            calibration_array=st.session_state.calib_array,
            source_file=st.session_state.file_name,
            session_id=st.session_state.session_id,
            params={
                "simplify_tolerance_px": export.DEFAULT_SIMPLIFY_TOLERANCE,
                "plate": plate_type,
                "margins": margin_int,
                "step_row": step_row_int,
                "step_col": step_col_int,
                "randomize": randomize_toggle,
                "samples_and_wells_source": "plate builder" if st.session_state.use_plate_wells else "uploaded",
            },
        )

        skipped = plan.skipped
        if not skipped.empty:
            st.warning(
                f"{len(skipped)} of {len(plan.shapes)} shapes have no well and will not be cut. "
                f"Their classes: {', '.join(sorted(set(skipped[CLASS_NAME]))[:10])}"
            )

        try:
            result = export.build_collection(plan, samples_and_wells=st.session_state.saw, plate=plate_type)
        except ValueError as error:
            st.error(str(error))
            logger.error(str(error))
            st.stop()

        st.session_state.zip_buffer = export.build_bundle(
            plan=plan,
            result=result,
            samples_and_wells=st.session_state.saw,
            plate=plate_type,
            log_path=st.session_state.log_file_path,
        )
        st.session_state.bundle_name = f"{Path(st.session_state.file_name).stem}_collection.zip"
        st.session_state.collection_image = result.image_path

        st.write(
            f"Collection: {result.n_shapes} shapes, {result.n_vertices} vertices, "
            f"{len(plan.wells_used)} wells used."
        )
        st.image(result.image_path, caption="Your Contours", width="content")
        st.success("All files have been processed and are ready for download.")
        logger.success("All files processed and zipped successfully")

if st.session_state.zip_buffer:
    st.download_button(
        label="Download files",
        data=st.session_state.zip_buffer.getvalue(),
        file_name=st.session_state.bundle_name or "collection.zip",
        mime="application/zip",
    )

st.divider()
st.divider()
st.divider()
st.divider()

#######################
####### EXTRAS ########
#######################

st.markdown("""
            # Extras to make your life easier :D
             - Create Qupath classes
            """)
st.divider()


#################################
## EXTRA 1: Classes for QuPath ##
#################################
st.markdown("""
            ## Extra #1 : Create QuPath classes from categoricals
            Creating many QuPath classes can be tedious, and is very error prone, especially for large projects.
            This tool takes in two lists of categoricals, and a number for replicates, and create a class for every permutation.

            Afterwards you must:
            1. Create a new QuPath project
            2. Close QuPath window
            3. Delete `<QuPath project>/classifiers/annotations/classes.json`
            4. Replace with newly created file
            5. Rename it as `classes.json`
            6. Reopen QuPath with project, and you should see classes
            """)

input1 = st.text_area("Enter first categorical (comma-separated)", placeholder="example: celltype_A, celltype_B")
input2 = st.text_area("Enter second categorical (comma-separated)", placeholder="example: control, drug_treated")
input3 = st.number_input("Enter number of replicates", min_value=1, step=1, value=2)
list1 = [i.strip() for i in input1.split(",") if i.strip()]
list2 = [i.strip() for i in input2.split(",") if i.strip()]

if st.button("Create class names for QuPath"):
    if not list1 or not list2:
        st.warning("Please enter at least one value in each categorical.")
    else:
        names = extras.generate_combinations(list1, list2, int(input3))
        st.write(f"{len(names)} class names created.")
        st.download_button(
            "Download classes.json for QuPath",
            data=json.dumps(extras.build_classes_json(names), indent=2),
            file_name="classes.json",
            mime="application/json",
        )

st.image(image="./assets/sample_names_example.png", caption="Example of class names for QuPath")
st.divider()
