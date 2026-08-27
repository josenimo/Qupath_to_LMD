"""Per-class statistics — the figures a user chooses budgets from."""

import pandas
import pytest

from qupath_to_lmd import geojson, stats
from tests.conftest import CELLS_FILE, CELLS_PIXEL_SIZE


def test_areas_come_from_geometry_not_from_qupath_measurements(multiclass):
    """A typical QuPath export carries no measurements, so nothing may depend on them.

    This file has no `measurements` column at all, yet must still produce areas.
    """
    gdf, _points, _report = multiclass
    assert "measurements" not in gdf.columns, "Fixture assumption broken: this file should have no measurements."
    table = stats.class_statistics(gdf, pixel_size_um=0.6535)
    assert table["area_total_um2"].sum() > 0, (
        "No areas were computed for a file without QuPath measurements. Areas must come from "
        "the geometry and the pixel size, never from the measurement fields."
    )


def test_geometry_areas_agree_with_qupath_own_measurements(cells):
    """The cross-check that justifies deriving areas ourselves rather than reading them.

    Computed from a raw read, because `read_and_qc` drops the measurement strings once it has
    taken the implied scale from them. If these diverge, either the scale derivation or the
    area computation is wrong, and every µm² figure in the app is built on both.
    """
    import geopandas

    _gdf, _points, report = cells
    raw = geopandas.read_file(CELLS_FILE).set_crs(None, allow_override=True)
    qupath = geojson.area_measurements(raw)
    derived = raw.geometry.area * report.implied_pixel_size_um**2

    both = derived.notna() & qupath.notna() & (qupath > 0)
    assert both.sum() > 100, (
        f"Only {both.sum()} objects had both a QuPath area and a geometry area, so the "
        "comparison is not meaningful. Expected the file's 121 measured cells."
    )
    ratio = (derived[both] / qupath[both]).median()
    assert ratio == pytest.approx(1.0, abs=0.01), (
        f"Geometry-derived areas are {ratio:.4f}x QuPath's own over {both.sum()} cells. "
        "They should agree within a percent; a systematic gap means the scale or the area is wrong."
    )


def test_statistics_without_a_scale_return_counts_only(cells_gdf):
    """A user budgeting by cell count never needs a scale, so it must be optional — but an
    area in pixels squared is not a quantity anyone can reason about, so it is not offered."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=None)
    assert list(table.columns) == ["shapes"], (
        f"Without a scale the table should carry only shape counts, got {list(table.columns)}."
    )
    assert table["shapes"].sum() == len(cells_gdf)


def test_statistics_with_a_scale_report_the_expected_columns(cells_gdf):
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    expected = [
        "shapes", "area_total_um2", "area_median_um2", "area_std_um2",
        "area_q1_um2", "area_q3_um2", "area_min_um2", "area_max_um2",
    ]
    assert list(table.columns) == expected, (
        f"Column set changed to {list(table.columns)}. The UI and the display order depend on this."
    )
    assert (table["area_min_um2"] <= table["area_median_um2"]).all(), "Minimum area exceeds the median."
    assert (table["area_median_um2"] <= table["area_max_um2"]).all(), "Median area exceeds the maximum."


def test_a_single_shape_class_reports_no_spread(cells_gdf):
    """One shape has no standard deviation. NaN is the honest answer; zero would imply
    uniformity that was never measured."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    singles = table[table["shapes"] == 1]
    assert not singles.empty, "Fixture assumption broken: expected at least one single-shape class."
    assert singles["area_std_um2"].isna().all(), (
        "A one-shape class reported a standard deviation, which cannot be measured from one value."
    )


def test_display_rounds_areas_but_never_the_counts(cells_gdf):
    """Raw areas carry a long float tail that implies precision segmentation cannot support.
    Counts are exact and must not be touched."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    display = stats.for_display(table)
    areas = [c for c in display.columns if c != stats.DISPLAY_COLUMNS["shapes"]]
    values = [v for v in display[areas].to_numpy().ravel() if pandas.notna(v)]
    assert all(round(v, stats.DECIMALS) == v for v in values), (
        f"Some displayed areas carry more than {stats.DECIMALS} decimals, so the table implies "
        "precision the measurement does not have."
    )
    assert display[stats.DISPLAY_COLUMNS["shapes"]].tolist() == table["shapes"].tolist(), (
        "Shape counts were rounded. A count is not a measurement."
    )


def test_classes_are_ordered_by_size(cells_gdf):
    """The biggest class first is what a user scans for when deciding what is worth collecting."""
    table = stats.class_statistics(cells_gdf, pixel_size_um=CELLS_PIXEL_SIZE)
    assert table["shapes"].is_monotonic_decreasing, (
        f"Classes are not ordered by shape count: {table['shapes'].tolist()}."
    )


def test_reference_pixel_sizes_show_the_spread_rather_than_one_number(cells_gdf):
    """The table exists to demonstrate that magnification does not determine pixel size.

    A single authoritative-looking column would invite exactly the mistake it warns about.
    """
    reference = stats.reference_pixel_sizes()
    assert len(reference.columns) >= 2, (
        "The reference table needs more than one sensor pitch, or it implies magnification is enough."
    )
    row = reference.loc["20×"]
    assert row.max() / row.min() > 1.5, (
        f"The 20x row spans only {row.max() / row.min():.1f}x, which understates how much pixel "
        "size varies between microscopes."
    )
    for magnification in stats.OBJECTIVES:
        for column, pitch in zip(reference.columns, stats.SENSOR_PITCHES_UM, strict=True):
            assert reference.loc[f"{magnification}×", column] == round(pitch / magnification, 3)


def test_empty_frame_gives_an_empty_table(cells_gdf):
    assert stats.class_statistics(cells_gdf.iloc[:0], pixel_size_um=1.0).empty


def test_a_negative_scale_is_rejected(cells_gdf):
    """A negative scale would silently produce positive areas via the square."""
    with pytest.raises(ValueError, match="positive"):
        stats.class_statistics(cells_gdf, pixel_size_um=-1.0)
