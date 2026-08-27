"""CollectionPlan: the seam both workflows produce and the export consumes."""

from qupath_to_lmd import plate
from qupath_to_lmd.model import CLASS_NAME, GROUP_KEY, WELL, plan_from_class_wells


def test_legacy_plan_maps_one_class_to_one_well(cells, calibration):
    gdf, points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    scheme = dict(zip(classes, ["B2", "B3", "B4", "B5"], strict=False))

    plan = plan_from_class_wells(
        gdf=gdf, samples_and_wells=scheme,
        calibration_names=list(points)[:3], calibration_array=calibration,
    )
    assert set(plan.selected[WELL]) == set(scheme.values()), (
        f"The plan uses wells {set(plan.selected[WELL])}, which does not match the scheme {scheme}."
    )
    assert (plan.selected[GROUP_KEY] == plan.selected[CLASS_NAME]).all(), (
        "In the legacy workflow a group is a class, so group_key must equal the class name."
    )


def test_a_class_left_out_of_the_scheme_is_not_selected(cells, calibration):
    """In the annotations workflow a missing class usually means the user forgot it, so those
    shapes must be distinguishable from shapes deliberately not selected."""
    gdf, points, _report = cells
    classes = sorted(set(gdf[CLASS_NAME]))
    plan = plan_from_class_wells(
        gdf=gdf, samples_and_wells={classes[0]: "C3"},
        calibration_names=list(points)[:3], calibration_array=calibration,
    )
    assert len(plan.not_selected) > 0, (
        "Classes absent from the scheme should land in `not_selected` so the UI can warn about them."
    )
    assert plan.unplaced.empty, (
        "`unplaced` is for shapes whose group ran out of wells, not for classes the user omitted."
    )


def test_cell_plan_names_groups_by_class_and_replicate(make_plan):
    plan, samples_and_wells, _result, budgets = make_plan(replicates=2, per_replicate=5)
    assert len(samples_and_wells) == len(budgets) * 2, (
        f"Expected one well per replicate per class, got {len(samples_and_wells)} wells for "
        f"{len(budgets)} classes x 2 replicates."
    )
    assert all(key.endswith(("_r1", "_r2")) for key in samples_and_wells), (
        f"Group keys should be class_r<replicate>; got {sorted(samples_and_wells)[:3]}."
    )


def test_unselected_shapes_are_kept_so_the_app_can_say_what_it_left_out(make_plan, cells_gdf):
    """Dropping them would make the count unavailable, and the app never silently loses data."""
    plan, _saw, result, _budgets = make_plan(replicates=1, per_replicate=5)
    assert len(plan.shapes) == len(cells_gdf), (
        f"The plan holds {len(plan.shapes)} shapes but the file had {len(cells_gdf)}. "
        "Unselected shapes must be retained for reporting."
    )
    assert len(plan.selected) == result.n_selected
    assert len(plan.not_selected) == len(cells_gdf) - result.n_selected


def test_groups_beyond_the_available_wells_are_reported(make_plan):
    """A group with no well will not be cut, which the user has to be told before processing."""
    plan, samples_and_wells, _result, _budgets = make_plan(replicates=3, per_replicate=2, wells=["B2", "B3"])
    assert len(samples_and_wells) == 2, f"Only two wells were offered; {len(samples_and_wells)} were assigned."
    assert not plan.unplaced.empty, (
        "Shapes belonging to groups that got no well must appear in `unplaced`, or they vanish silently."
    )


def test_provenance_records_what_determined_the_output(make_plan):
    """The bundle has to let someone reproduce and cite the collection."""
    plan, samples_and_wells, _result, _budgets = make_plan()
    provenance = plan.provenance()
    assert provenance["workflow"] == "cells"
    assert provenance["shapes_selected"] == len(plan.selected)
    # `groups` counts groups that actually received shapes, which can be fewer than the wells
    # assigned: a class with too few shapes leaves a later replicate empty, and that replicate
    # keeps its well on the approved plate. See the dedicated test below.
    assert provenance["groups"] == len(plan.wells_used) <= len(samples_and_wells), (
        f"provenance reports {provenance['groups']} groups against {len(plan.wells_used)} wells "
        f"used and {len(samples_and_wells)} wells assigned; these should agree that way round."
    )
    assert len(provenance["calibration_points"]) == 3, (
        f"All three calibration points must be recorded; got {provenance['calibration_points']}."
    )
    assert provenance["pixel_size_um"] is not None


def test_the_plan_carries_only_the_columns_it_needs(make_plan):
    """The plan copies the frame, and a full copy is expensive on large files."""
    plan, _saw, _result, _budgets = make_plan()
    assert "measurements" not in plan.shapes.columns
    for required in ("id", "objectType", "classification", "geometry"):
        assert required in plan.shapes.columns, (
            f"{required!r} is missing from the plan, so the QuPath round-trip export will fail."
        )


def test_the_same_selection_always_lands_in_the_same_wells(make_plan):
    """Groups are sorted before assignment so a rerun does not move samples between wells."""
    first = make_plan(seed=3)[1]
    second = make_plan(seed=3)[1]
    assert first == second, f"Two identical runs assigned different wells:\n{first}\n{second}"


def test_wells_used_is_sorted_and_matches_the_selection(make_plan):
    plan, samples_and_wells, _result, _budgets = make_plan()
    assert plan.wells_used == sorted(set(plan.selected["well"]))
    assert set(plan.wells_used) <= set(samples_and_wells.values())


def test_assign_wells_and_the_plan_agree(make_plan):
    """The plate the user approved must be the plate the export uses."""
    plan, samples_and_wells, _result, budgets = make_plan()
    expected = plate.assign_wells(list(samples_and_wells), list(samples_and_wells.values()))
    assert set(plan.selected["well"]) <= set(expected.values())


def test_a_replicate_that_gets_no_shapes_keeps_its_well_but_is_not_counted_as_cut(make_plan):
    """A class with fewer shapes than `replicates x per_replicate` leaves a later replicate empty.

    The empty replicate keeps the well the user was shown, so the plate does not silently
    change under them, but `provenance["groups"]` counts only groups that received shapes —
    which is what was actually collected.
    """
    plan, samples_and_wells, _result, _budgets = make_plan(replicates=2, per_replicate=5)
    assigned = set(samples_and_wells)
    cut = set(plan.selected[GROUP_KEY])
    empty = assigned - cut
    assert empty, (
        "Fixture assumption broken: expected at least one replicate to come up empty, since "
        "one demo class has a single shape."
    )
    assert plan.provenance()["groups"] == len(cut), (
        f"provenance counted {plan.provenance()['groups']} groups but {len(cut)} received shapes."
    )
    for group in empty:
        assert group in samples_and_wells, (
            f"Empty replicate {group!r} lost its well. The approved plate must not change."
        )
