"""Well plates: which wells are usable, laying samples out on them, and reading layouts back."""

import ast
import string
from random import Random

import pandas
from loguru import logger

# rows, columns
PLATE_SHAPES = {"384": (16, 24), "96": (8, 12)}


class SawParseError(Exception):
    """A samples-and-wells file could not be read as a dictionary."""


def plate_dimensions(plate: str) -> tuple[int, int]:
    """Rows and columns of a supported plate.

    Named `dimensions` rather than `shape`, because in this app a shape is something the
    laser cuts (see GLOSSARY.md).
    """
    if plate not in PLATE_SHAPES:
        raise ValueError(f"Plate must be one of {sorted(PLATE_SHAPES)}, got {plate!r}")
    return PLATE_SHAPES[plate]


def acceptable_wells(plate: str = "384", margins: int = 0, step_row: int = 1, step_col: int = 1) -> list[str]:
    """Wells left usable after a margin and row/column spacing.

    The margin exists because the LMD7 collects unreliably into the outermost wells of a
    384 plate; the steps leave blanks between samples for easier pipetting.
    """
    max_row, max_col = plate_dimensions(plate)
    if not isinstance(margins, int):
        raise ValueError("margins must be an integer")

    min_row, min_col = 1, 1
    if margins > 0:
        max_row -= margins
        max_col -= margins
        min_row += margins
        min_col += margins

    return [
        f"{row}{column}"
        for row in string.ascii_uppercase[min_row - 1 : max_row : step_row]
        for column in range(min_col, max_col + 1, step_col)
    ]


def default_layout(plate: str = "384") -> pandas.DataFrame:
    """The bare plate, every cell holding its own well name."""
    rows, cols = plate_dimensions(plate)
    row_labels = list(string.ascii_uppercase[:rows])
    col_labels = list(range(1, cols + 1))
    return pandas.DataFrame(
        [[f"{row}{col}" for col in col_labels] for row in row_labels],
        index=row_labels,
        columns=col_labels,
    )


def assign_wells(
    groups: list[str],
    wells: list[str],
    randomize: bool = False,
    seed: int = 0,
) -> dict[str, str]:
    """Map each group to a well, in sorted order so the same plan always lands the same way.

    Randomizing spreads groups over the plate, which guards against a systematic
    position effect being read as a biological one. It is seeded, so a randomized layout is
    still reproducible and can be reported.
    """
    ordered = sorted(groups)
    available = list(wells)
    if randomize:
        available = Random(seed).sample(available, len(available))
    return dict(zip(ordered, available, strict=False))


def sample_layout(
    classes: list[str],
    plate: str = "384",
    wells: list[str] | None = None,
    randomize: bool = False,
    seed: int = 0,
) -> tuple[pandas.DataFrame, list[str]]:
    """Place classes into the usable wells, in order, one class per well.

    Returns the layout and the classes that did not fit, so the caller can say so rather
    than let them disappear.
    """
    rows, cols = plate_dimensions(plate)
    wells = list(wells if wells is not None else acceptable_wells(plate))

    # Sorted, so the same file laid out twice gives the same plate.
    ordered_classes = sorted(classes)
    if randomize:
        logger.info(f"Randomizing well order with seed {seed}")
        wells = Random(seed).sample(wells, len(wells))

    unplaced = ordered_classes[len(wells) :]
    if unplaced:
        logger.warning(f"{len(unplaced)} classes do not fit in {len(wells)} usable wells")

    layout = pandas.DataFrame(
        None,
        index=list(string.ascii_uppercase[:rows]),
        columns=range(1, cols + 1),
        dtype=object,
    )
    for class_name, well in zip(ordered_classes, wells, strict=False):
        layout.at[well[0], int(well[1:])] = class_name

    return layout, unplaced


def highlight(values: set[str]) -> callable:
    """Styler map: green for cells whose content is in `values`, grey otherwise."""

    def style(cell):
        if cell in values:
            return "background-color: #77dd77; color: black;"
        return "background-color: #f0f2f6;"

    return style


def layout_to_saw(layout: pandas.DataFrame) -> dict[str, str]:
    """Read a plate layout back into `{class_name: well}`."""
    return {
        class_name: f"{row}{column}"
        for row, series in layout.iterrows()
        for column, class_name in series.items()
        if class_name and pandas.notna(class_name)
    }


def placement_dataframe(samples_and_wells: dict[str, str], plate: str = "384") -> pandas.DataFrame:
    """The plate as a table of class names, for the CSV in the download bundle."""
    logger.info(f"Building placement table for a {plate} well plate")
    rows, cols = plate_dimensions(plate)

    table = pandas.DataFrame(
        "",
        index=list(string.ascii_uppercase[:rows]),
        columns=[str(i) for i in range(1, cols + 1)],
    )
    for class_name, well in samples_and_wells.items():
        table.at[well[0], well[1:]] = class_name

    return table


def parse_saw_file(source) -> dict[str, str]:
    """Read a samples-and-wells file written as a Python dict literal.

    Raises:
        SawParseError: unreadable, empty, or not a dictionary.
    """
    logger.info("Parsing samples-and-wells file")
    if isinstance(source, str):
        with open(source, encoding="utf-8-sig") as handle:
            content = handle.read()
    elif hasattr(source, "read"):
        raw = source.read()
        content = raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw
    else:
        raise SawParseError(f"Cannot read a samples-and-wells file from {type(source).__name__}")

    if not content.strip():
        raise SawParseError("The file is empty.")

    try:
        parsed = ast.literal_eval(content)
    except (ValueError, SyntaxError) as error:
        raise SawParseError(
            f"Could not read this as a dictionary ({error}). Check for a missing quote, "
            'brace or comma. It should look like {"class_name": "C3", ...}'
        ) from error

    if not isinstance(parsed, dict):
        raise SawParseError(f"The file contains a {type(parsed).__name__}, not a dictionary.")
    if not parsed:
        raise SawParseError("The dictionary is empty.")

    return parsed
