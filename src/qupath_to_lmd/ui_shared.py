"""Steps used by both workflows.

This is the UI layer, so these functions may read and write `st.session_state` — that is
what they are for. The library modules (`geojson`, `plate`, `qc`, `export`, `model`) stay
pure and take explicit arguments.
"""

import json
from pathlib import Path

import streamlit as st
from loguru import logger

from qupath_to_lmd import export, extras, geojson, plate, qc
from qupath_to_lmd.model import CLASS_NAME

WORKFLOWS = {
    "legacy": "Annotations — one class is one sample is one well",
    "cells": "Cell segmentation — pick classes, replicates and how much to collect",
}


@st.cache_data(show_spinner="Reading and checking your GeoJSON...")
def _read_geojson(source):
    """Cached wrapper so a rerun does not re-parse the same upload."""
    return geojson.read_and_qc(source)


def reset_file_state():
    """Forget the uploaded file and everything derived from it."""
    for key in ("gdf", "geojson_report", "calibration_points", "calibs", "calib_array", "file_name"):
        st.session_state[key] = None


def upload_step(step: str = "1"):
    """Upload a GeoJSON and show what QC found. Returns the uploaded file or None."""
    st.markdown(f"""
                ## Step {step}: Upload and check .geojson file from Qupath
                Upload your .geojson file from qupath, order of calibration points is important
                """)

    uploaded_file = st.file_uploader(label="Choose a file", type="geojson", accept_multiple_files=False)

    if not uploaded_file:
        if st.session_state.file_name is not None:
            reset_file_state()
        return None

    if st.session_state.file_name != uploaded_file.name or st.session_state.gdf is None:
        logger.info(f"New file detected: {uploaded_file.name}")
        try:
            gdf, calibration_points, report = _read_geojson(uploaded_file)
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
    if report.n_unnamed_classification_dropped:
        st.warning(
            f"{report.n_unnamed_classification_dropped} objects have a QuPath classification "
            "that carries no usable class name, so they cannot be assigned to a well and are "
            "ignored. If you expected these, check how they are classified in QuPath."
        )
    if report.multiclass_counts:
        total = sum(report.multiclass_counts.values())
        listed = ", ".join(f"{name} ({count})" for name, count in list(report.multiclass_counts.items())[:6])
        st.warning(
            f"{total} objects carry more than one QuPath class. They are treated as their own "
            f"combined classes, named the way QuPath displays them: {listed}. "
            "They are neither of their parent classes here — assign them a well of their own, "
            "or reclassify in QuPath if that is not what you want."
        )
    if report.n_multipolygons_dropped:
        st.warning(
            f"{report.n_multipolygons_dropped} MultiPolygon objects found. These are not supported — "
            "please split them into single polygons in QuPath. Processing continues without them."
        )
        st.table(report.multipolygons)

    st.success(f"File check complete, {report.n_shapes_kept} shapes available.")
    return uploaded_file


def workflow_step(step: str = "2") -> str:
    """Choose a workflow. Suggests one from `objectType` once a file is loaded."""
    st.markdown(f"## Step {step}: Which kind of collection is this?")

    suggestion = None
    gdf = st.session_state.gdf
    if gdf is not None and "objectType" in gdf.columns:
        counts = gdf["objectType"].value_counts().to_dict()
        n_cells = counts.get("cell", 0) + counts.get("detection", 0)
        n_annotations = counts.get("annotation", 0)
        described = ", ".join(f"{count} {kind}s" for kind, count in counts.items())
        suggestion = "cells" if n_cells > n_annotations else "legacy"
        st.write(
            f"This file contains {described}. Based on that, the "
            f"**{'cell segmentation' if suggestion == 'cells' else 'annotations'}** workflow is "
            "probably what you want — but you decide."
        )

    options = list(WORKFLOWS)
    default_index = options.index(suggestion) if suggestion else 0
    chosen = st.radio(
        "Workflow",
        options=options,
        index=default_index,
        format_func=lambda key: WORKFLOWS[key],
        key="workflow_choice",
    )

    if st.session_state.workflow != chosen:
        logger.info(f"Workflow set to {chosen}")
        st.session_state.workflow = chosen
    return chosen


def calibration_step(step: str = "3"):
    """Pick three calibration points and report triangle coverage."""
    if st.session_state.gdf is None:
        return

    st.markdown(f"## Step {step}: Calibration points")

    if not st.session_state.calibration_points:
        st.warning(
            "**No calibration points in this file, so no collection can be made from it.** "
            "The LMD needs three reference points to map image coordinates onto the stage.\n\n"
            "In QuPath: select the point tool, click three spots on the slide (ideally close "
            "to the tissue you want to cut), give each point annotation a name in the "
            "annotation list, then export again. The export must include the point "
            "annotations as well as your cells or regions — if you exported a selection, the "
            "points were probably left out."
        )
        return

    options = list(st.session_state.calibration_points)
    c1 = st.selectbox("Select calibration point 1", options, index=0 if len(options) > 0 else None)
    c2 = st.selectbox("Select calibration point 2", options, index=1 if len(options) > 1 else None)
    c3 = st.selectbox("Select calibration point 3", options, index=2 if len(options) > 2 else None)

    st.session_state.calibs = [c1, c2, c3]
    logger.info(f"Calibration points chosen: {st.session_state.calibs}")

    if not all(st.session_state.calibs):
        return

    triangle = qc.triangle_qc(
        st.session_state.gdf, st.session_state.calibration_points, st.session_state.calibs
    )
    st.session_state.calib_array = triangle.calibration_array
    st.write(f"{triangle.fraction_inside * 100:.2f}% of shapes are inside the calibration triangle")
    if triangle.is_concerning:
        st.warning(
            "Less than 25% of your shapes fall inside the calibration triangle. Shapes far "
            "outside it get distorted by the coordinate transform, so you may cut the wrong "
            "tissue. Consider calibration points closer to your annotations."
        )


def pixel_size_step(step: str = "4") -> float | None:
    """Ask for µm per pixel and cross-check it against QuPath's own area measurements.

    Required before any area figure is shown (`decisions.md` 011). The entered value is
    never overwritten by the implied one — a mismatch is reported and the user decides.
    """
    if st.session_state.gdf is None:
        return None

    st.markdown(f"## Step {step}: Image scale")
    st.markdown(
        "How many micrometres does one pixel of your image cover? QuPath shows this in "
        "*Image → Image properties → Pixel width*. Every area below is computed from it, so "
        "a wrong value gives you a correct-looking collection of the wrong amount of tissue."
    )

    entered = st.number_input(
        "Micrometres per pixel (µm/px)",
        min_value=0.0,
        max_value=100.0,
        value=float(st.session_state.pixel_size_um or 0.0),
        step=0.01,
        format="%.4f",
        key="pixel_size_input",
    )

    if not entered:
        st.info("Enter the pixel size to continue.")
        return None

    report = qc.pixel_size_qc(st.session_state.gdf, entered)
    if report.implied_um_per_px is None:
        st.warning(
            "This file has no QuPath area measurements, so the value cannot be cross-checked. "
            "Please double-check it against QuPath yourself."
        )
    elif report.is_concerning:
        st.warning(
            f"Your value is **{report.ratio:.2f}×** what this file implies. QuPath's own area "
            f"measurements across {report.n_objects_checked} objects imply "
            f"**{report.implied_um_per_px:.4f} µm/px**. One of the two is wrong — most often "
            "this is a pixel size read from the wrong image or a factor-of-ten slip. "
            "You can continue if you are sure."
        )
    else:
        st.success(
            f"Cross-checked against {report.n_objects_checked} objects: this file implies "
            f"{report.implied_um_per_px:.4f} µm/px (spread {report.relative_spread * 100:.2f}%)."
        )

    st.session_state.pixel_size_um = entered
    return entered


def plate_settings_step(step: str = "5") -> dict:
    """Plate type, margin and spacing. Returns the settings and the usable wells."""
    st.markdown(f"""
                ## Step {step}: Decide which plate to collect into, either 384 or 96 well plate.
                Decide how many wells to make unavailable as a margin (for 384wp we suggest a margin of 2).
                Decide how many wells to leave blank in between, for easier pipetting.
                """)
    st.write("You can increase plate size by dragging bottom right corner")

    plate_col, margin_col, step_row_col, step_col_col = st.columns(4)
    with plate_col:
        plate_string = st.selectbox("Select a plate type", ("384 well plate", "96 well plate"))
    with margin_col:
        margin = st.number_input("Margin (integer)", min_value=0, max_value=10, value=1)
    with step_row_col:
        step_row = st.number_input("Space between rows", min_value=1, max_value=10, value=1)
    with step_col_col:
        step_col = st.number_input("Space between columns", min_value=1, max_value=10, value=1)

    plate_type = plate_string.split(" ")[0]
    return {
        "plate_type": plate_type,
        "margins": margin,
        "step_row": step_row,
        "step_col": step_col,
        "wells": plate.acceptable_wells(
            plate=plate_type, margins=margin, step_row=step_row, step_col=step_col
        ),
    }


def plate_layout_step(settings: dict, uploaded_file) -> None:
    """Plate views, layout confirmation, and the custom samples-and-wells override."""
    plate_type, wells = settings["plate_type"], settings["wells"]

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
        randomize = st.toggle("Randomize samples", value=False)

    params = {k: settings[k] for k in ("plate_type", "margins", "step_row", "step_col")}
    params["randomize"] = randomize
    params_changed = st.session_state.plate_gen_params != params

    if st.session_state.view_mode == "default":
        layout = plate.default_layout(plate=plate_type)
        st.dataframe(layout.style.map(plate.highlight(set(wells))), width="stretch")

    elif st.session_state.view_mode == "samples":
        if uploaded_file is None:
            st.warning("File no longer available. Please upload a file or switch to the default view.")
        elif st.session_state.gdf is not None:
            if params_changed or st.session_state.plate_df is None:
                st.session_state.plate_gen_params = params
                layout, unplaced = plate.sample_layout(
                    classes=st.session_state.gdf[CLASS_NAME].unique().tolist(),
                    plate=plate_type,
                    wells=wells,
                    randomize=randomize,
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
            _report_saw(qc.validate_saw(
                st.session_state.saw,
                st.session_state.gdf[CLASS_NAME].unique().tolist(),
                plate=plate_type,
            ), plate_type, success="Samples and wells layout confirmed, you are ready to process!")
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

    _custom_saw_step(plate_type)


def _report_saw(report, plate_type: str, success: str) -> None:
    """Show what samples-and-wells validation found."""
    if report.missing_classes:
        st.warning(
            f"{len(report.missing_classes)} classes in your file have no well and will not be "
            f"collected: {', '.join(sorted(report.missing_classes)[:10])}"
        )
    if report.duplicate_wells:
        st.warning(f"Wells receiving more than one class: {report.duplicate_wells}")
    if report.invalid_wells:
        st.error(
            f"These wells do not exist on a {plate_type} well plate: {sorted(report.invalid_wells)}. "
            "Fix the scheme or change the plate type."
        )
    else:
        st.success(success)


def _custom_saw_step(plate_type: str) -> None:
    """Upload a samples-and-wells dictionary, overriding the generated layout."""
    if st.button("Upload custom samples and wells dictionary, will override"):
        logger.info("Upload custom samples and wells dictionary -- ButtonPress")
        st.session_state.show_saw_uploader = True

    if not st.session_state.show_saw_uploader:
        return

    uploaded_saw = st.file_uploader(
        label="Choose a custom samples-and-wells file (.txt or .json)",
        type=["txt", "json"],
        accept_multiple_files=False,
        key="saw_uploader",
    )
    if uploaded_saw is None:
        return

    try:
        candidate = plate.parse_saw_file(uploaded_saw)
    except plate.SawParseError as error:
        st.error(f"Could not read that samples-and-wells file: {error}")
        logger.error(f"Samples-and-wells parse failed: {error}")
        return

    if st.session_state.gdf is None:
        st.warning("Upload a GeoJSON first, so the scheme can be checked against your classes.")
        return

    report = qc.validate_saw(
        candidate, st.session_state.gdf[CLASS_NAME].unique().tolist(), plate=plate_type
    )
    if report.invalid_wells:
        _report_saw(report, plate_type, success="")
        return

    st.session_state.saw = candidate
    st.session_state.use_plate_wells = False
    st.session_state.show_saw_uploader = False
    _report_saw(report, plate_type, success=f"Custom samples and wells loaded and checked: {len(candidate)} classes.")


def export_step(settings: dict, build_plan, step: str = "6") -> None:
    """Process the collection and offer the download bundle.

    Args:
        settings: plate settings from `plate_settings_step`.
        build_plan: callable returning a `CollectionPlan`, supplied by the workflow.
        step: number shown in the heading, since each workflow reaches this step at a
            different point.
    """
    st.markdown(f"""
                ## Step {step}: Process to create .xml file for LMD
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
            _process(settings, build_plan)

    if st.session_state.zip_buffer:
        st.download_button(
            label="Download files",
            data=st.session_state.zip_buffer.getvalue(),
            file_name=st.session_state.bundle_name or "collection.zip",
            mime="application/zip",
        )


def _process(settings: dict, build_plan) -> None:
    """Build the plan, render it, and stash the bundle for download."""
    plate_type = settings["plate_type"]
    plan = build_plan(settings)

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


def extras_step() -> None:
    """Extra #1: generate QuPath classes from two categoricals."""
    st.markdown("""
                # Extras to make your life easier :D
                 - Create Qupath classes
                """)
    st.divider()
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
    replicates = st.number_input("Enter number of replicates", min_value=1, step=1, value=2)
    list1 = [i.strip() for i in input1.split(",") if i.strip()]
    list2 = [i.strip() for i in input2.split(",") if i.strip()]

    if st.button("Create class names for QuPath"):
        if not list1 or not list2:
            st.warning("Please enter at least one value in each categorical.")
        else:
            names = extras.generate_combinations(list1, list2, int(replicates))
            st.write(f"{len(names)} class names created.")
            st.download_button(
                "Download classes.json for QuPath",
                data=json.dumps(extras.build_classes_json(names), indent=2),
                file_name="classes.json",
                mime="application/json",
            )

    st.image(image="./assets/sample_names_example.png", caption="Example of class names for QuPath")
