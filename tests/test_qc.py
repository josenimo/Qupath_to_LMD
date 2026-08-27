"""Quality checks. These decide whether a collection is safe to make at all."""

import numpy
import pytest

from qupath_to_lmd import qc


def test_triangle_coverage_is_reported(cells):
    """Shapes far outside the calibration triangle are distorted by the coordinate transform,
    so the fraction inside is the user's warning that they may cut the wrong tissue."""
    gdf, points, _report = cells
    report = qc.triangle_qc(gdf, points, list(points)[:3])
    assert 0 < report.fraction_inside <= 1, f"Coverage came out as {report.fraction_inside}."
    assert report.n_shapes == len(gdf)
    assert not report.is_degenerate, "A valid three-point triangle was reported as degenerate."


@pytest.mark.parametrize(
    ("corners", "why"),
    [
        ([[0, 0], [0, 0], [0, 0]], "the same point three times"),
        ([[0, 0], [100, 0], [100, 0]], "one point repeated"),
        ([[0, 0], [50, 50], [100, 100]], "three collinear points"),
    ],
)
def test_degenerate_calibration_is_detected(cells, corners, why):
    """py-lmd accepts a degenerate triangle and writes a perfectly well-formed XML — no
    exception, no NaN. The user gets a file that looks correct and cuts in the wrong place,
    so this is the only place it can be caught."""
    gdf, _points, _report = cells
    points = {f"p{i}": corner for i, corner in enumerate(corners)}
    report = qc.triangle_qc(gdf, points, list(points))
    assert report.is_degenerate, (
        f"Calibration with {why} was not flagged as degenerate (triangle area "
        f"{report.triangle_area}). py-lmd will happily write a meaningless cutting file from it."
    )


def test_a_missing_calibration_point_raises(cells):
    gdf, points, _report = cells
    with pytest.raises(KeyError, match="not found"):
        qc.triangle_qc(gdf, points, ["calib1", "calib2", "nope"])


def test_wells_are_validated_against_the_chosen_plate():
    """Validating against a hard-coded 384 grid let `K5` pass on a 96-well plate, which has no
    row K — the tissue would go nowhere."""
    on_96 = qc.validate_saw({"a": "K5", "b": "C3"}, ["a", "b"], plate="96")
    on_384 = qc.validate_saw({"a": "K5", "b": "C3"}, ["a", "b"], plate="384")
    assert on_96.invalid_wells == {"K5"}, (
        f"K5 does not exist on a 96 plate but was accepted; invalid wells reported: {on_96.invalid_wells}."
    )
    assert not on_384.invalid_wells, "K5 exists on a 384 plate and should be accepted there."
    assert not on_96.is_usable, "A scheme naming a non-existent well must not be usable."


def test_classes_missing_from_the_scheme_are_reported():
    """These shapes will not be collected. In the annotations workflow that usually means the
    user forgot a class, so it has to be visible."""
    report = qc.validate_saw({"a": "C3"}, ["a", "b", "c"], plate="384")
    assert report.missing_classes == {"b", "c"}, (
        f"Expected b and c reported as absent from the scheme, got {report.missing_classes}."
    )
    assert report.is_usable, "Missing classes are a warning, not a blocker (decisions.md 003)."


def test_two_classes_in_one_well_are_reported():
    """Two classes sharing a well mixes two samples, which is almost never intended."""
    report = qc.validate_saw({"a": "C3", "b": "C3"}, ["a", "b"], plate="384")
    assert report.duplicate_wells == {"C3": ["a", "b"]}, (
        f"Expected C3 flagged as receiving two classes, got {report.duplicate_wells}."
    )


@pytest.mark.parametrize(
    ("entered", "concerning", "why"),
    [
        (0.3467, False, "the value this file implies"),
        (3.467, True, "a factor-of-ten slip"),
        (0.65, True, "a plausible but wrong value from another microscope"),
        (0.3500, False, "within a percent of the implied value"),
    ],
)
def test_pixel_size_mismatches_are_flagged(entered, concerning, why):
    """A wrong scale gives a correct-looking collection of the wrong amount of tissue, and a
    2x error in scale is a 4x error in every area."""
    report = qc.compare_pixel_size(entered, implied_um_per_px=0.3467, n_objects=121, relative_spread=0.002)
    assert report.is_concerning is concerning, (
        f"Entering {entered} µm/px against an implied 0.3467 ({why}) was "
        f"{'not ' if concerning else ''}flagged; ratio was {report.ratio:.2f}x."
    )


def test_pixel_size_check_degrades_when_there_is_nothing_to_compare():
    """Most exports carry no measurements, so this is the normal case and must not look like
    an error."""
    report = qc.compare_pixel_size(0.5, implied_um_per_px=None)
    assert report.implied_um_per_px is None
    assert report.ratio is None
    assert not report.is_concerning, "With nothing to compare against, nothing can be concerning."


def test_triangle_report_survives_having_no_shapes(cells):
    """A file whose shapes were all dropped still has to produce a report rather than divide
    by zero."""
    gdf, points, _report = cells
    empty = gdf.iloc[:0]
    report = qc.triangle_qc(empty, points, list(points)[:3])
    assert report.fraction_inside == 0.0
    assert not report.is_concerning, "An empty frame should not raise a distortion warning."
    assert isinstance(report.calibration_array, numpy.ndarray)
