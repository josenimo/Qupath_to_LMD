"""The whole app, driven headlessly: upload, every stage, and the download.

Streamlit's `AppTest` runs `streamlit_app.py` as a user's browser would, without one. Its file
uploader cannot be driven, so the slide uploader is replaced with one that hands over demo files.
Slow-ish (seconds per run), so marked `slow` alongside the golden gate.
"""

import io
import pathlib
import zipfile

import pytest
import streamlit
from streamlit.testing.v1 import AppTest

REPO = pathlib.Path(__file__).resolve().parent.parent
DEMO = REPO / "demo_Qupath_project"
GOLDEN = REPO / "tools" / "golden"

pytestmark = pytest.mark.slow


class _Upload:
    def __init__(self, path: pathlib.Path):
        self.name = path.name
        self._data = path.read_bytes()
        self.size = len(self._data)

    def getvalue(self) -> bytes:
        return self._data


def _run(monkeypatch, files: list[str], method: str | None = None, actions=()) -> AppTest:
    uploads = [_Upload(DEMO / name) for name in files]
    real = streamlit.file_uploader

    def uploader(label, *args, **kwargs):
        return uploads if kwargs.get("key") == "slide_uploader" else real(label, *args, **kwargs)

    monkeypatch.setattr(streamlit, "file_uploader", uploader)
    app = AppTest.from_file(str(REPO / "streamlit_app.py"), default_timeout=180)
    app.run()
    if method:
        app.radio(key="workflow_choice").set_value(method).run()
    for action in actions:
        action(app)
        app.run()
    assert not app.exception, f"The app raised: {[e.value for e in app.exception]}"
    process = [button for button in app.button if "Process" in str(button.label)]
    assert process, "No 'Process files' button: a stage before the cut stopped the page."
    process[0].click().run()
    assert not app.exception, f"Processing raised: {[e.value for e in app.exception]}"
    assert app.session_state.zip_buffer is not None, (
        f"Processing produced no download. Errors on the page: {[e.value for e in app.error]}"
    )
    return app


def _contents(app: AppTest) -> dict[str, bytes]:
    archive = zipfile.ZipFile(io.BytesIO(app.session_state.zip_buffer.getvalue()))
    return {name: archive.read(name) for name in archive.namelist()}


def test_one_annotation_file_through_the_app_matches_the_golden_reference(monkeypatch):
    """The annotations workflow moved into the shared stages; what it cuts must not have moved."""
    files = _contents(_run(monkeypatch, ["TD_01_verysmall_mIF.geojson"]))
    assert files["TD_01_verysmall_mIF.xml"] == (GOLDEN / "annotations.xml").read_bytes(), (
        "The app's .xml for the demo annotations differs from tools/golden/annotations.xml. A "
        "single-slide whole-shapes collection would cut differently from before the stages."
    )
    assert files["TD_01_verysmall_mIF_384_wellplate.csv"] == (GOLDEN / "annotations.csv").read_bytes()


def test_two_slides_on_two_plates_give_four_cutting_files(monkeypatch):
    def two_plates(app):
        key = next(w.key for w in app.number_input if w.key and w.key.startswith("n_plates_"))
        app.number_input(key=key).set_value(2)

    app = _run(monkeypatch, ["Single_cells.geojson", "multiclass_cells.geojson"], "cells", [two_plates])
    files = _contents(app)
    xmls = sorted(name for name in files if name.endswith(".xml"))
    assert len(xmls) == 4, f"Two slides on two plates should give four .xml files, got {xmls}."
    for expected in ("samples.csv", "plate_P1.csv", "plate_P2.csv", "HOW_TO_CUT.txt"):
        assert expected in files, f"{expected} is missing from the experiment download."


def test_regions_across_two_slides_run_to_a_download(monkeypatch):
    files = _contents(_run(monkeypatch, ["Single_cells.geojson", "Single_cells.geojson"], "regions"))
    assert {"slide_Single_cells/Single_cells__P1.xml", "slide_Single_cells_2/Single_cells_2__P1.xml"} <= set(files), (
        f"Each copy of the slide should get its own .xml: {sorted(files)}"
    )
