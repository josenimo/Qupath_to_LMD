"""Keeps the vocabulary from drifting back.

The same thing was being called a shape, a polygon, an object and a contour in different parts
of the app. GLOSSARY.md settles it; these tests make it enforceable rather than aspirational.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SOURCE = sorted((ROOT / "src" / "qupath_to_lmd").glob("*.py")) + [ROOT / "streamlit_app.py"]
GLOSSARY = ROOT / "GLOSSARY.md"


def _sources_without_mock():
    """Every source file we own. `mock_streamlit.py` is legacy and excluded from lint too."""
    return [p for p in SOURCE if p.name != "mock_streamlit.py"]


def test_contour_is_not_used_anywhere():
    """It was a fourth word for a shape, in an image caption and the README.

    Reintroducing it means a user sees two names for the same thing in one session.
    """
    offenders = {
        path.name: [line for line in path.read_text().splitlines() if "contour" in line.lower()]
        for path in _sources_without_mock()
    }
    offenders = {name: lines for name, lines in offenders.items() if lines}
    assert not offenders, (
        "'contour' is a fourth name for a shape and should not appear. Found in:\n"
        + "\n".join(f"  {name}: {lines}" for name, lines in offenders.items())
        + "\nUse 'shape' — see GLOSSARY.md."
    )


def test_the_shape_limit_is_named_for_what_it_counts():
    """It was `POLYGON_LIMIT`, but it counts shapes, and a shape is not the same as a polygon."""
    from qupath_to_lmd import plot

    assert hasattr(plot, "SHAPE_LIMIT"), "plot.SHAPE_LIMIT is missing."
    assert not hasattr(plot, "POLYGON_LIMIT"), (
        "plot.POLYGON_LIMIT is back. It counts shapes, not polygons — 'polygon' is reserved for "
        "the geometry type (GLOSSARY.md)."
    )


def test_plate_dimensions_does_not_borrow_the_word_shape():
    """`plate_shape` read as though a plate were something the laser cuts."""
    from qupath_to_lmd import plate

    assert hasattr(plate, "plate_dimensions"), "plate.plate_dimensions is missing."
    assert not hasattr(plate, "plate_shape"), (
        "plate.plate_shape is back. In this app a shape is something the laser cuts, so a "
        "plate's rows and columns are its dimensions (GLOSSARY.md)."
    )


@pytest.mark.parametrize(
    "term",
    ["shape", "object", "polygon", "class", "group", "replicate", "well",
     "calibration point", "pixel size", "neighbour", "collection"],
)
def test_the_glossary_defines_every_canonical_term(term):
    """A term used in the interface but absent from the glossary is a term nobody can look up."""
    assert GLOSSARY.exists(), "GLOSSARY.md is missing."
    text = GLOSSARY.read_text().lower()
    assert f"**{term}**" in text, (
        f"GLOSSARY.md does not define {term!r}. Every term the app uses in its messages should "
        "be defined there, or a reader has no way to check what it means."
    )


def test_the_glossary_is_linked_from_the_places_people_start():
    """An unlinked glossary is a file nobody opens."""
    for document in ("README.md", "CLAUDE.md"):
        text = (ROOT / document).read_text()
        assert "GLOSSARY.md" in text, (
            f"{document} does not link GLOSSARY.md, so neither users nor contributors will find it."
        )


def test_qupath_field_names_are_left_alone():
    """`objectType` is QuPath's field, not our vocabulary. Renaming it would break reading."""
    text = (ROOT / "src" / "qupath_to_lmd" / "geojson.py").read_text()
    assert '"objectType"' in text or "'objectType'" in text, (
        "The reader no longer refers to QuPath's `objectType` field. That field name comes from "
        "QuPath and must match the file exactly, whatever we call things internally."
    )


def test_user_facing_counts_are_described_as_shapes():
    """Messages about what will be cut should say 'shapes'.

    Messages about reading the file legitimately say 'objects', because at that point they are
    still QuPath's objects — so this only checks that 'shapes' is the term actually in use.
    """
    ui = (ROOT / "src" / "qupath_to_lmd" / "ui_shared.py").read_text()
    assert re.search(r"shapes? (available|selected)|\{[^}]*\} shapes", ui), (
        "No message in ui_shared.py counts shapes. If shape counts are being described some "
        "other way, the interface and the glossary have diverged."
    )
