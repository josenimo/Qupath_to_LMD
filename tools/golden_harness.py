#!/usr/bin/env python
"""Golden-file harness: prove that a change did not alter the collection output.

This is a characterization test, not a correctness test. It cannot tell you the laser cut
the right tissue — it tells you the bytes are the same as they were before your change.
That is the check that matters for refactors, because a coordinate shifted by a pixel, an
inverted Y flip or a different shape order are all invisible in the running app.

Usage:

    python tools/golden_harness.py check      # compare current output against tools/golden/
    python tools/golden_harness.py capture    # re-bless: overwrite tools/golden/

`check` exits non-zero on any mismatch, so it can gate a commit.

Run `capture` only when output is *meant* to change, and say so in the commit message —
re-blessing silently is how a real regression gets frozen into the reference. Never
hand-edit the files in tools/golden/.

What it does not cover: the UI, the intentional behaviour changes around QC and warnings,
LineString geometries (no demo file has one), and any input shape outside the four cases
below. Add cases as the app grows.
"""

import os
import sys
from pathlib import Path

# py-lmd's Collection.plot() blocks forever under a GUI matplotlib backend, so a plain
# `python` run on macOS hangs without this. Must precede importing anything that pulls in
# matplotlib.
os.environ.setdefault("MPLBACKEND", "Agg")

from qupath_to_lmd import export, geojson, plate, qc
from qupath_to_lmd.model import CLASS_NAME, plan_from_class_wells

REPO = Path(__file__).resolve().parent.parent
GOLDEN_DIR = Path(__file__).resolve().parent / "golden"
DEMO = REPO / "demo_Qupath_project"

# Each case exercises a path where a refactor could silently change coordinates.
CASES = {
    # the ordinary mini-bulk path
    "annotations": {"source": DEMO / "TD_01_verysmall_mIF.geojson"},
    # many small shapes, cell objects carrying measurements
    "cells": {"source": DEMO / "Single_cells.geojson"},
    # the explode path: one well per shape
    "cells_exploded": {"source": DEMO / "Single_cells.geojson", "explode": ["single_cells_demo"]},
    # plate geometry differs, so the CSV and well validation do too
    "annotations_96": {"source": DEMO / "TD_01_verysmall_mIF.geojson", "plate_type": "96", "margin": 0},
}


def run_case(source, explode=None, plate_type="384", margin=1) -> tuple[str, str]:
    """Drive the whole pipeline for one case and return its XML and CSV.

    Deliberately builds the samples-and-wells scheme from sorted class names rather than
    from the plate UI, so the harness depends only on library code and stays stable.
    """
    gdf, calibration_points, _report = geojson.read_and_qc(str(source))
    calibration_names = list(calibration_points)[:3]
    triangle = qc.triangle_qc(gdf, calibration_points, calibration_names)

    if explode:
        gdf = geojson.explode_classes(gdf, explode)

    wells = plate.acceptable_wells(plate=plate_type, margins=margin)
    classes = sorted(set(gdf[CLASS_NAME]))
    samples_and_wells = dict(zip(classes, wells, strict=False))

    plan = plan_from_class_wells(
        gdf=gdf,
        samples_and_wells=samples_and_wells,
        calibration_names=calibration_names,
        calibration_array=triangle.calibration_array,
        source_file=Path(source).name,
        session_id="golden",
    )
    result = export.build_collection(plan, samples_and_wells=samples_and_wells, plate=plate_type)
    return result.xml, result.csv


def capture() -> int:
    """Write current output to tools/golden/, replacing what is there."""
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, kwargs in CASES.items():
        xml, csv = run_case(**kwargs)
        (GOLDEN_DIR / f"{name}.xml").write_text(xml)
        (GOLDEN_DIR / f"{name}.csv").write_text(csv)
        print(f"captured  {name}  xml={len(xml)}B csv={len(csv)}B")
    print(f"\nGolden files written to {GOLDEN_DIR.relative_to(REPO)}. Commit them with your change.")
    return 0


def check() -> int:
    """Compare current output against the golden files."""
    if not GOLDEN_DIR.exists():
        print(f"No golden files at {GOLDEN_DIR}. Run `capture` first.", file=sys.stderr)
        return 2

    mismatches = []
    for name, kwargs in CASES.items():
        produced = dict(zip(("xml", "csv"), run_case(**kwargs), strict=True))
        for kind, content in produced.items():
            reference_path = GOLDEN_DIR / f"{name}.{kind}"
            if not reference_path.exists():
                print(f"MISSING  {name}.{kind} has no golden file")
                mismatches.append(f"{name}.{kind}")
                continue
            reference = reference_path.read_text()
            if content == reference:
                print(f"match    {name}.{kind}  ({len(content)}B)")
            else:
                print(f"DIFFER   {name}.{kind}  produced {len(content)}B, golden {len(reference)}B")
                mismatches.append(f"{name}.{kind}")

    if mismatches:
        print(f"\n{len(mismatches)} mismatch(es): {', '.join(mismatches)}")
        print("If this change was meant to alter output, re-run with `capture` and say so in the commit.")
        return 1

    print(f"\nAll {len(CASES) * 2} artefacts identical.")
    return 0


def main() -> int:
    """Dispatch on the subcommand."""
    command = sys.argv[1] if len(sys.argv) > 1 else "check"
    if command == "check":
        return check()
    if command == "capture":
        return capture()
    print(f"Unknown command {command!r}. Use `check` or `capture`.", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
