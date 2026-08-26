"""Steps used by both workflows.

This is the UI layer, so these functions may read and write `st.session_state` — that is
what they are for. The library modules (`geojson`, `plate`, `qc`, `export`, `model`) stay
pure and take explicit arguments.
"""

import json
from pathlib import Path

import streamlit as st
from loguru import logger

from qupath_to_lmd import export, extras, geojson, plate, qc, stats
from qupath_to_lmd.model import CLASS_NAME

# The rendered number input carries `step` as an HTML attribute and browsers snap entries
# to that grid, so the step has to be as fine as the format can display or typed values get
# rounded. Four decimals matches what QuPath reports for pixel width.
PIXEL_SIZE_DECIMALS = 4
PIXEL_SIZE_STEP = 10**-PIXEL_SIZE_DECIMALS
PIXEL_SIZE_FORMAT = f"%.{PIXEL_SIZE_DECIMALS}f"
PIXEL_SIZE_MIN = PIXEL_SIZE_STEP
PIXEL_SIZE_MAX = 100.0

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
            f"{total} objects carry more than one QuPath class. Each combination becomes its "
            f"own class, with the class names joined by `--`: {listed}. "
            "These objects are not counted under any of their individual classes — give each "
            "combination its own well, or reclassify in QuPath if that is not what you want."
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

    available = st.session_state.calibration_points or {}
    if len(available) < 3:
        found = (
            "This file has no calibration points."
            if not available
            else f"This file has only {len(available)} calibration point(s): {', '.join(available)}."
        )
        st.error(
            f"**{found} Three are required and there is no way to continue without them.**\n\n"
            "The LMD needs three reference points to map image coordinates onto the stage. "
            "Without them any cutting file would be meaningless, so processing stops here.\n\n"
            "In QuPath: select the point tool, click three spots on the slide — ideally close "
            "to the tissue you want to cut — give each point annotation a name in the "
            "annotation list, then export again. The export must include the point annotations "
            "as well as your cells or regions; if you exported a selection, the points were "
            "probably left out."
        )
        logger.error(f"Stopping: {len(available)} calibration points found, 3 required")
        st.stop()

    options = list(available)
    c1 = st.selectbox("Select calibration point 1", options, index=0)
    c2 = st.selectbox("Select calibration point 2", options, index=1)
    c3 = st.selectbox("Select calibration point 3", options, index=2)

    st.session_state.calibs = [c1, c2, c3]
    logger.info(f"Calibration points chosen: {st.session_state.calibs}")

    triangle = qc.triangle_qc(
        st.session_state.gdf, st.session_state.calibration_points, st.session_state.calibs
    )

    if triangle.is_degenerate:
        repeated = len(set(st.session_state.calibs)) < 3
        st.error(
            "**These three calibration points do not form a triangle, so no collection can "
            "be made from them.**\n\n"
            + (
                "The same point is selected more than once. Pick three different points."
                if repeated
                else "All three points lie on a straight line. Pick three that form a proper "
                "triangle around your tissue."
            )
            + "\n\nThis has to stop here: the LMD software would accept the resulting file "
            "without complaint and cut in the wrong place."
        )
        st.session_state.calib_array = None
        logger.error(f"Stopping: degenerate calibration triangle from {st.session_state.calibs}")
        st.stop()

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

    Optional (`decisions.md` 038): without it, only shape counts and cell-count budgets are
    available. Required before any *area* figure is shown (`decisions.md` 011). The entered
    value is never overwritten by the implied one — a mismatch is reported and the user decides.
    """
    if st.session_state.gdf is None:
        return None

    st.markdown(f"## Step {step} (optional): Image scale")
    st.markdown(
        "How many micrometres does one pixel of your image cover? QuPath shows this in "
        "*Image → Image properties → Pixel width*.\n\n"
        "**Only needed if you want to work in areas.** If you intend to collect a number of "
        "cells, skip this. Every area figure in the app is computed from this number, so a "
        "wrong value gives you a correct-looking collection of the wrong amount of tissue — "
        "which is why it is better left blank than guessed."
    )

    # Three things matter for this input to behave:
    #  - value=None starts it genuinely empty and returns None until the user types, so
    #    there is no 0.0 sentinel to confuse with a real entry.
    #  - value= is NOT re-passed on later reruns: with key= set, the widget's own state is
    #    the source of truth, and re-passing value fights it and snaps the field back.
    #  - step must match the displayed precision. The rendered HTML input carries
    #    step as an attribute and browsers snap off-grid entries to it, so a coarser step
    #    than the format turns a typed 0.3467 into 0.35.
    input_column, reference_column = st.columns([2, 3])

    with input_column:
        entered = st.number_input(
            "Micrometres per pixel (µm/px)",
            min_value=PIXEL_SIZE_MIN,
            max_value=PIXEL_SIZE_MAX,
            value=None,
            step=PIXEL_SIZE_STEP,
            format=PIXEL_SIZE_FORMAT,
            key="pixel_size_input",
            placeholder="e.g. 0.3467",
            help=(
                f"Accepted to {PIXEL_SIZE_DECIMALS} decimal places, between {PIXEL_SIZE_MIN} and "
                f"{PIXEL_SIZE_MAX:g} µm/px. Type the value directly — the arrows step by "
                f"{PIXEL_SIZE_STEP:g}."
            ),
        )

    with reference_column:
        st.markdown("**If you only know the magnification**")
        st.dataframe(stats.reference_pixel_sizes(), width="stretch")
        st.warning(
            "**Magnification does not tell you the pixel size.** Pixel size is your camera's "
            "sensor pitch divided by the *total* magnification, so the same 20× objective can "
            "differ by more than 2× between two microscopes — and any additional coupler or "
            "zoom changes it again. Treat the table as a rough sanity check only, and get the "
            "real number from QuPath under *Image → Image properties → Pixel width*."
        )

    if entered is None:
        st.caption(
            "Left blank. You can still collect by number of cells; areas and area budgets "
            "stay unavailable until a scale is entered."
        )
        return None

    report = qc.pixel_size_qc(st.session_state.gdf, entered)
    if report.implied_um_per_px is None:
        # Measurements are optional on export and most files will not have them, so this is
        # the normal case. Stating it quietly keeps the real warnings worth reading.
        st.caption(
            "This file carries no QuPath measurements, so the value could not be "
            "cross-checked automatically. Nothing else needs them."
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

    plate_col, margin_col, step_row_col, step_col_col, random_col = st.columns(5)
    with plate_col:
        plate_string = st.selectbox("Select a plate type", ("384 well plate", "96 well plate"))
    with margin_col:
        margin = st.number_input("Margin (integer)", min_value=0, max_value=10, value=1)
    with step_row_col:
        step_row = st.number_input("Space between rows", min_value=1, max_value=10, value=1)
    with step_col_col:
        step_col = st.number_input("Space between columns", min_value=1, max_value=10, value=1)
    with random_col:
        randomize = st.toggle(
            "Randomize wells",
            value=False,
            help=(
                "Spread samples over the plate instead of filling it in order, so a "
                "systematic plate-position effect cannot be mistaken for a biological one. "
                "Seeded, so the layout is still reproducible."
            ),
        )

    plate_type = plate_string.split(" ")[0]
    return {
        "plate_type": plate_type,
        "margins": margin,
        "step_row": step_row,
        "step_col": step_col,
        "randomize": randomize,
        "wells": plate.acceptable_wells(
            plate=plate_type, margins=margin, step_row=step_row, step_col=step_col
        ),
    }


def plate_layout_step(settings: dict, uploaded_file) -> None:
    """Plate views, layout confirmation, and the custom samples-and-wells override."""
    plate_type, wells = settings["plate_type"], settings["wells"]

    randomize = settings["randomize"]

    col1, col2 = st.columns(2)
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

    params = {k: settings[k] for k in ("plate_type", "margins", "step_row", "step_col", "randomize")}
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
                plate_preview(
                    plate.layout_to_saw(st.session_state.plate_df),
                    plate_type,
                    wells=wells,
                    key_suffix="legacy",
                )

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


def plate_preview(
    samples_and_wells: dict[str, str],
    plate_type: str,
    wells: list[str] | None = None,
    key_suffix: str = "",
) -> None:
    """Show the plate with each sample in its well, and offer the scheme as a download.

    The single plate renderer for both workflows, so what a user sees does not depend on
    which one they picked (`decisions.md` 045).
    """
    if not samples_and_wells:
        st.warning("No wells assigned yet.")
        return

    layout = plate.placement_dataframe(samples_and_wells, plate=plate_type)
    st.dataframe(layout.style.map(plate.highlight(set(samples_and_wells))), width="stretch")
    st.caption(f"{len(samples_and_wells)} wells in use on a {plate_type} well plate.")

    if wells:
        with st.expander(f"Which wells the current margin and spacing leave usable ({len(wells)})"):
            usable = plate.default_layout(plate=plate_type)
            st.dataframe(usable.style.map(plate.highlight(set(wells))), width="stretch")

    st.download_button(
        label="Download samples and wells setup",
        data=json.dumps(samples_and_wells, indent=4),
        file_name="samples_and_wells.json",
        mime="application/json",
        key=f"saw_download_{plate_type}_{len(samples_and_wells)}_{key_suffix}",
    )


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

    tolerance, path_order = _export_parameters(step)

    if st.button("Process files"):
        logger.info("Process files button clicked")
        if st.session_state.gdf is None:
            st.warning("Please upload a GeoJSON file first.")
        elif st.session_state.saw is None:
            st.warning("Please confirm a plate layout or upload a samples-and-wells scheme first.")
        elif st.session_state.calib_array is None:
            st.warning("Please select three calibration points first.")
        else:
            _process(settings, build_plan, tolerance, path_order)

    if st.session_state.zip_buffer:
        st.download_button(
            label="Download files",
            data=st.session_state.zip_buffer.getvalue(),
            file_name=st.session_state.bundle_name or "collection.zip",
            mime="application/zip",
        )


PATH_ORDER_LABELS = {
    export.PathOrder.NONE: "As loaded — no reordering",
    export.PathOrder.GROUPED: "Group each well together",
    export.PathOrder.HILBERT: "Group by well and shorten the path within each (recommended)",
}


def _export_parameters(step: str) -> tuple[float, export.PathOrder]:
    """Simplification tolerance and cut order. Both default to today's behaviour."""
    tolerance_column, order_column = st.columns([1, 2])

    with tolerance_column:
        tolerance = st.number_input(
            "Smoothing tolerance (pixels)",
            min_value=0.0,
            max_value=100.0,
            value=export.DEFAULT_SIMPLIFY_TOLERANCE,
            step=0.5,
            key=f"simplify_tolerance_{step}",
            help=(
                "How far an outline may move when spare points are removed from it. Higher "
                "values mean fewer points, so the stage traces the shape faster, but the cut "
                "follows your annotation less exactly. Lower values follow it more closely at "
                "the cost of a slower cut. The default of 1 pixel is what this app has always "
                "used."
            ),
        )

    with order_column:
        path_order = st.selectbox(
            "Cutting order",
            options=list(export.PathOrder),
            format_func=lambda mode: PATH_ORDER_LABELS[mode],
            key=f"path_order_{step}",
            help=(
                "The order shapes are written in is the order the LMD cuts them. Grouping a "
                "well's shapes together means the collector moves once per well instead of "
                "once per shape; shortening the path within each well cuts down how far the "
                "stage travels. Neither changes which tissue lands in which well."
            ),
        )

    return float(tolerance), path_order


def _process(settings: dict, build_plan, tolerance: float, path_order) -> None:
    """Build the plan, render it, and stash the bundle for download."""
    plate_type = settings["plate_type"]
    plan = build_plan(settings)
    plan.params.update(
        {"simplify_tolerance_px": tolerance, "path_order": path_order.value}
    )

    skipped = plan.skipped
    if not skipped.empty:
        st.warning(
            f"{len(skipped)} of {len(plan.shapes)} shapes have no well and will not be cut. "
            f"Their classes: {', '.join(sorted(set(skipped[CLASS_NAME]))[:10])}"
        )

    try:
        result = export.build_collection(
            plan,
            samples_and_wells=st.session_state.saw,
            simplify_tolerance=tolerance,
            plate=plate_type,
            path_order=path_order,
        )
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
    _report_path(result, plan.pixel_size_um)
    st.image(result.image_path, caption="Your Contours", width="content")
    st.success("All files have been processed and are ready for download.")
    logger.success("All files processed and zipped successfully")


def _report_path(result, pixel_size_um: float | None) -> None:
    """Show what the cutting order costs in stage travel and collector movements."""
    def as_distance(pixels: float) -> str:
        if pixel_size_um:
            return f"{pixels * pixel_size_um / 1000:,.1f} mm"
        return f"{pixels:,.0f} px"

    line = (
        f"Cut path: **{as_distance(result.path_length_px)}** of stage travel, "
        f"**{result.collector_moves}** collector movements."
    )
    saved = result.baseline_path_length_px - result.path_length_px
    if saved > 0 or result.baseline_collector_moves > result.collector_moves:
        line += (
            f" Without reordering it would be {as_distance(result.baseline_path_length_px)} and "
            f"{result.baseline_collector_moves} movements."
        )
    st.write(line)


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
