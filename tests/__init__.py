"""Test package.

Present so that `from tests.conftest import ...` resolves under pytest's default import mode.
Without it the suite depends on the rootdir happening to be on `sys.path`, which is true in
some environments and not others — it passed locally and broke after a clean `uv sync --frozen`.
"""
