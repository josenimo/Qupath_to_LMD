"""Collectors other than plates: the Eppendorf tube holder and the 8-well strip holder.

A tube holder or a strip holder plays the part of a plate. What goes wrong when these break is
always the same at the microscope: tissue sent to a cap that does not exist, or to the wrong one.
"""

import string

import pytest

from qupath_to_lmd import plate, qc


def _old_acceptable_wells(plate_type, margins=0, step_row=1, step_col=1):
    """`acceptable_wells` as it was before collectors, for plates only."""
    max_row, max_col = {"384": (16, 24), "96": (8, 12)}[plate_type]
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


def test_tube_holder_positions_are_letters_a_to_d():
    assert plate.acceptable_wells("tubes") == ["A", "B", "C", "D"], (
        "The tube holder must offer tubes A–D and nothing else; any other CapID sends tissue to a "
        "tube the LMD does not have."
    )


def test_strip_positions_are_letters_a_to_h():
    assert plate.acceptable_wells("strip") == list("ABCDEFGH"), (
        "The strip holder must offer wells A–H of its one strip; any other CapID sends tissue to "
        "a well the LMD does not have."
    )


@pytest.mark.parametrize("key", ["tubes", "strip"])
def test_margin_and_spacing_do_not_apply_to_tubes_or_strips(key):
    assert plate.acceptable_wells(key, margins=2, step_row=2, step_col=2) == plate.acceptable_wells(key), (
        f"A margin or spacing removed positions from the {key} holder. They exist for the edge "
        "wells of a 384 plate; on a holder they would silently leave tubes unused."
    )


@pytest.mark.parametrize("plate_type", ["384", "96"])
@pytest.mark.parametrize("margins", [0, 1, 2, 3])
@pytest.mark.parametrize("step", [1, 2, 3])
def test_plates_keep_their_wells(plate_type, margins, step):
    assert plate.acceptable_wells(plate_type, margins, step, step) == _old_acceptable_wells(
        plate_type, margins, step, step
    ), (
        f"The usable wells of a {plate_type} plate changed (margin {margins}, step {step}), so "
        "existing experiments would land in different wells than before."
    )


def test_a_position_splits_into_row_and_column():
    assert plate.split_position("C3") == ("C", 3)
    assert plate.split_position("P24") == ("P", 24)
    assert plate.split_position("C") == ("C", None), (
        "A tube position has no column; reading one in would invent a well that does not exist."
    )


def test_a_tube_holder_table_is_one_column():
    table = plate.placement_dataframe({"Tumor": "C"}, plate="tubes")
    assert list(table.index) == ["A", "B", "C", "D"]
    assert list(table.columns) == ["tube"], (
        f"The tube holder map has columns {list(table.columns)}; it should be one column of tubes, "
        "or the map in the download does not match the holder on the bench."
    )
    assert table.at["C", "tube"] == "Tumor"


def test_the_hand_editor_reads_tubes_back_as_letters():
    scheme = {"Tumor": "A", "Stroma": "D"}
    recovered = plate.layout_to_saw(plate.placement_dataframe(scheme, plate="tubes"), plate="tubes")
    assert recovered == scheme, (
        f"Editing the tube holder by hand gave {recovered}. A position like 'Atube' is no CapID, "
        "so the edited samples would be cut into nothing."
    )


def test_a_start_tube_is_case_insensitive_and_a_plate_well_is_ignored():
    tubes = plate.acceptable_wells("tubes")
    assert plate.wells_from(tubes, "c") == ["C", "D"], "Typing 'c' should start the holder at tube C."
    assert plate.wells_from(tubes, "C3") == tubes, (
        "A plate well typed as the start of a tube holder must fall back to the first tube, not "
        "collect nothing."
    )


def test_positions_that_do_not_exist_on_the_collector_are_invalid():
    tubes = qc.validate_saw({"a": "C3", "b": "E"}, [], plate="tubes")
    assert tubes.invalid_wells == {"C3", "E"}, (
        f"On a tube holder only A–D exist; {tubes.invalid_wells} were flagged. Unflagged, tissue "
        "goes to a cap the holder does not have."
    )
    strip = qc.validate_saw({"a": "I", "b": "A1"}, [], plate="strip")
    assert strip.invalid_wells == {"I", "A1"}, (
        f"On a strip only A–H exist; {strip.invalid_wells} were flagged."
    )


def test_an_unknown_collector_is_refused():
    with pytest.raises(ValueError):
        plate.collector("1536")
