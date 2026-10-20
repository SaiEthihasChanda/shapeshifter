"""Keep tests away from the user's real ShapeShifter data."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture(autouse=True)
def _isolated_user_data(tmp_path_factory, monkeypatch):
    from shapeshifter import config, learned
    root = tmp_path_factory.mktemp("userdata")
    monkeypatch.setattr(learned, "FILE", root / "learned.json")
    monkeypatch.setattr(config, "STATE_FILE", root / "state.json")      # OCR status etc.
