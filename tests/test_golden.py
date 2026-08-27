"""The byte-equality gate.

This is the only check that can see a coordinate shifted by a pixel, an inverted Y flip, or
shapes written in a different order. None of those are visible in the running app, and all of
them mean the laser cuts the wrong tissue.
"""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tools" / "golden_harness.py"


@pytest.mark.slow
def test_output_is_byte_identical_to_the_golden_files():
    """Runs `tools/golden_harness.py check` and fails with its output if anything differs.

    If this fails, read the harness output above: it names which artefact differs. Either the
    change was unintended — in which case fix it — or it was intended, in which case re-bless
    with `python tools/golden_harness.py capture`, state why in the commit message, and never
    hand-edit the files in `tools/golden/`.
    """
    completed = subprocess.run(
        [sys.executable, str(HARNESS), "check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        env={"MPLBACKEND": "Agg", "PATH": "/usr/bin:/bin"},
        check=False,
    )
    if completed.returncode == 0:
        return

    # The harness runs in a subprocess with its own logger, so its stdout is mostly log lines.
    # Surface only the verdicts, or the actual failure is buried.
    verdicts = [
        line for line in completed.stdout.splitlines()
        if line.startswith(("DIFFER", "MISSING", "match")) or "mismatch" in line
    ]
    differing = [line for line in verdicts if not line.startswith("match")]

    raise AssertionError(
        "The collection output no longer matches the golden files in tools/golden/.\n\n"
        + ("Artefacts that differ:\n  " + "\n  ".join(differing) + "\n\n" if differing else "")
        + "If this change was NOT intended, something altered the coordinates, the calibration\n"
        "handling, the simplification or the order shapes are written in. All of those are\n"
        "invisible in the running app and mis-cut real tissue.\n\n"
        "If it WAS intended, first confirm only what you meant to change has changed, then\n"
        "re-bless with:  python tools/golden_harness.py capture\n"
        "and say so explicitly in the commit message. Never hand-edit tools/golden/.\n\n"
        "Full verdicts:\n  " + "\n  ".join(verdicts or ["(harness produced no verdict lines)"])
    )


def test_the_harness_and_its_reference_files_are_present():
    """Guards against the gate being deleted or renamed and nobody noticing."""
    assert HARNESS.exists(), f"{HARNESS} is missing, so nothing is checking the output bytes."
    golden = ROOT / "tools" / "golden"
    artefacts = sorted(p.name for p in golden.glob("*"))
    assert len(artefacts) >= 10, (
        f"Expected at least 10 reference artefacts in {golden}, found {len(artefacts)}: {artefacts}"
    )
