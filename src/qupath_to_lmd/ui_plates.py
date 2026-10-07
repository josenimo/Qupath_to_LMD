"""Stage 3, Plates: fit the samples onto as many plates as they need.

Reads only `SampleSet.samples`, so it is the same stage whatever chose the samples. One plate is
the layout the app has always made; more appear only when the samples need them or the user
asks (`decisions.md` 076).
"""

from dataclasses import dataclass

import streamlit as st
from loguru import logger

from qupath_to_lmd import plate, qc, ui_shared
from qupath_to_lmd.model import SampleSet

DISTRIBUTION_LABELS = {
    plate.PlateDistribution.BALANCED: "Balanced — every plate holds every class (recommended)",
    plate.PlateDistribution.SEQUENTIAL: "Fill plate 1, then plate 2",
}


@dataclass
class PlateLayout:
    """Where every sample goes, and the settings that put it there."""

    plate_type: str
    settings: dict
    assignment: dict[str, tuple[str, str]]
    source: str = "plate builder"

    @property
    def schemes(self) -> dict[str, dict[str, str]]:
        """One samples-and-wells scheme per plate."""
        return plate.per_plate(self.assignment)

    @property
    def n_plates(self) -> int:
        """Plates that receive at least one sample."""
        return len(self.schemes)

    def params(self) -> dict:
        """The plate settings, for provenance."""
        recorded = {
            "plate": self.plate_type,
            "margins": self.settings["margins"],
            "step_row": self.settings["step_row"],
            "step_col": self.settings["step_col"],
            "randomize_wells": self.settings["randomize"],
            "samples_and_wells_source": self.source,
        }
        if self.n_plates > 1:
            recorded.update({"plates": self.n_plates, "plate_distribution": self.settings["distribution"]})
        return recorded


def settings_step() -> dict:
    """Plate type, margin, spacing, start well and randomizing."""
    st.markdown(
        "Choose the plate, how many wells to leave as a margin (for a 384 well plate we suggest "
        "2), and how many to leave blank between samples for easier pipetting."
    )
    plate_col, margin_col, step_row_col, step_col_col, start_col, random_col = st.columns(6)
    with plate_col:
        plate_string = st.selectbox("Plate type", ("384 well plate", "96 well plate"), key="plate_type")
    with margin_col:
        margin = st.number_input("Margin (integer)", min_value=0, max_value=10, value=1, key="plate_margin")
    with step_row_col:
        step_row = st.number_input("Space between rows", min_value=1, max_value=10, value=1, key="plate_step_row")
    with step_col_col:
        step_col = st.number_input("Space between columns", min_value=1, max_value=10, value=1, key="plate_step_col")
    with start_col:
        start_well = st.text_input(
            "Start at well",
            value="",
            placeholder="auto",
            key="plate_start_well",
            help=(
                "Fill the first plate from this well onwards instead of the first usable one — "
                "for adding to a plate that already holds samples from an earlier run. Leave "
                "blank to start at the beginning."
            ),
        )
    with random_col:
        randomize = st.toggle(
            "Randomize wells",
            value=False,
            key="plate_randomize",
            help=(
                "Spread samples over the plate instead of filling it in order, so a "
                "systematic plate-position effect cannot be mistaken for a biological one. "
                "Seeded, so the layout is still reproducible."
            ),
        )

    plate_type = plate_string.split(" ")[0]
    usable = plate.acceptable_wells(plate=plate_type, margins=margin, step_row=step_row, step_col=step_col)
    start = start_well.strip().upper() or None
    first_plate = plate.wells_from(usable, start)
    if start and first_plate is usable:
        st.warning(
            f"**{start_well}** is not one of the {len(usable)} wells this margin and spacing "
            "leave usable, so filling starts from the beginning instead."
        )
        start = None
    return {
        "plate_type": plate_type,
        "margins": margin,
        "step_row": step_row,
        "step_col": step_col,
        "randomize": randomize,
        "start_well": start,
        "usable": usable,
        "first_plate": first_plate,
    }


def _plates_control(samples: list[str], settings: dict) -> tuple[int, plate.PlateDistribution]:
    """How many plates, and how samples are spread over them — only shown when it matters."""
    usable = settings["usable"]
    if not usable:
        st.error("This margin and spacing leave no usable wells. Lower them, or use a 384 well plate.")
        st.stop()
    needed = plate.plates_needed(len(samples), len(usable), len(settings["first_plate"]))

    plates_column, distribution_column = st.columns([1, 3])
    with plates_column:
        n_plates = st.number_input(
            "Plates",
            min_value=1,
            max_value=50,
            value=needed,
            step=1,
            key=f"n_plates_{needed}",
            help=(
                f"These {len(samples)} samples need at least {needed}. More plates spread the "
                "samples thinner, which some designs want."
            ),
        )
    distribution = plate.PlateDistribution.BALANCED
    if n_plates > 1:
        with distribution_column:
            distribution = st.radio(
                "How samples are spread over the plates",
                options=list(DISTRIBUTION_LABELS),
                format_func=lambda option: DISTRIBUTION_LABELS[option],
                key="plate_distribution_choice",
                help=(
                    "Filling plate 1 first puts whole classes on one plate, so anything that "
                    "differs between plates — a batch, a run, a day — would look like a difference "
                    "between classes. Balanced puts every class on every plate where the "
                    "replicates allow."
                ),
            )
    st.session_state.n_plates = int(n_plates)
    st.session_state.plate_distribution = distribution.value
    return int(n_plates), distribution


def _custom_scheme(samples: list[str], plate_type: str) -> dict[str, str] | None:
    """A samples-and-wells file of the user's own, overriding the generated layout on one plate."""
    with st.expander("Use your own samples-and-wells file instead"):
        st.caption(
            "A `.txt` or `.json` holding a Python dictionary of sample name to well, e.g. "
            "`{'Tumor': 'C3', 'Stroma': 'C5'}`. It replaces the layout above and uses one plate."
        )
        uploaded = st.file_uploader(
            "Samples-and-wells file", type=["txt", "json"], accept_multiple_files=False, key="saw_uploader"
        )
        if uploaded is None:
            return None
        try:
            candidate = plate.parse_saw_file(uploaded)
        except plate.SawParseError as error:
            st.error(f"Could not read that samples-and-wells file: {error}")
            logger.error(f"Samples-and-wells parse failed: {error}")
            return None
        report = qc.validate_saw(candidate, samples, plate=plate_type)
        if report.missing_classes:
            st.warning(
                f"{len(report.missing_classes)} samples have no well in your file and will not be "
                f"collected: {', '.join(sorted(report.missing_classes)[:10])}"
            )
        if report.duplicate_wells:
            st.warning(f"Wells receiving more than one sample: {report.duplicate_wells}")
        if report.invalid_wells:
            st.error(
                f"These wells do not exist on a {plate_type} well plate: {sorted(report.invalid_wells)}. "
                "Fix the file or change the plate type."
            )
            return None
        st.success(f"Using your samples-and-wells file: {len(candidate)} samples.")
        return candidate


def render(sample_set: SampleSet) -> PlateLayout | None:
    """Stage 3. Returns where every sample goes."""
    st.markdown("## 3 · Plates")
    samples = sample_set.samples
    if not samples:
        st.info("No samples to place yet.")
        return None

    settings = settings_step()
    plate_type = settings["plate_type"]
    n_plates, distribution = _plates_control(samples, settings)
    settings["distribution"] = distribution.value

    custom = _custom_scheme(samples, plate_type)
    if custom is not None:
        assignment = {sample: ("P1", well) for sample, well in custom.items()}
        layout = PlateLayout(plate_type, settings, assignment, source="uploaded")
    else:
        assignment = plate.assign_to_plates(
            samples, settings["usable"], n_plates, distribution,
            randomize=settings["randomize"], start_well=settings["start_well"],
        )
        layout = PlateLayout(plate_type, settings, assignment)

    offered = len(settings["first_plate"]) + len(settings["usable"]) * (n_plates - 1)
    st.write(
        f"These samples need **{len(samples)} wells**. "
        + (f"This plate offers **{offered}**." if n_plates == 1 else f"{n_plates} plates offer **{offered}**.")
    )
    unplaced = [sample for sample in samples if sample not in layout.assignment]
    if unplaced:
        st.warning(
            f"{len(unplaced)} sample(s) have no well and will not be collected: "
            f"{', '.join(unplaced[:8])}{' ...' if len(unplaced) > 8 else ''}. Add a plate, reduce "
            "the replicates, or lower the margin or spacing."
        )

    schemes = layout.schemes
    names = list(schemes)
    if not names:
        st.warning("No sample has a well, so there is nothing to cut.")
        return None
    edited: dict[str, tuple[str, str]] = {}
    containers = st.tabs(names) if len(names) > 1 else [st.container()]
    for container, name in zip(containers, names, strict=True):
        with container:
            wells = settings["first_plate"] if name == "P1" else settings["usable"]
            scheme = ui_shared.editable_plate(schemes[name], plate_type, key_suffix=name)
            ui_shared.plate_preview(scheme, plate_type, wells=wells, key_suffix=name)
            edited.update({sample: (name, well) for sample, well in scheme.items()})
    layout.assignment = edited
    return layout
