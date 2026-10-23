import hashlib
import io

import pytest

from shapeshifter import config, updates


def release(tag="v9.0.0", **extra):
    data = {"tag_name": tag, "body": "Notes", "html_url": "https://github.com/x/y/releases/tag/" + tag, "draft": False, "prerelease": False,
            "assets": [{"name": "ShapeShifter-Setup.exe", "size": 5, "browser_download_url": "https://github.com/x/y/releases/download/a/ShapeShifter-Setup.exe",
                        "digest": "sha256:" + hashlib.sha256(b"hello").hexdigest()}]}
    data.update(extra)
    return data


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    return tmp_path


def test_version_order():
    assert updates.is_newer("2.6.2", "2.6.1") and updates.is_newer("2.10.0", "2.9.9") and updates.is_newer("3", "2.9.9")
    assert not updates.is_newer("2.6.1", "2.6.1") and not updates.is_newer("2.6.0", "2.6.1") and not updates.is_newer("2.6.1.0", "2.6.1")
    assert updates.parse_version("v2.6.1") == (2, 6, 1)


def test_release_record():
    up = updates.from_release(release())
    assert up.version == "9.0.0" and up.size == 5 and up.sha256 == hashlib.sha256(b"hello").hexdigest() and up.notes == "Notes"
    assert updates.from_release(release(prerelease=True)) is None and updates.from_release(release(draft=True)) is None
    assert updates.from_release(release(assets=[])) is None


class FakeResponse(io.BytesIO):
    headers = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def test_download_verifies_checksum(monkeypatch):
    up = updates.from_release(release())
    monkeypatch.setattr(updates, "_open", lambda url, timeout=15: FakeResponse(b"hello"))
    seen = []
    path = updates.download(up, seen.append)
    assert path.read_bytes() == b"hello" and seen and seen[-1] == 1.0
    monkeypatch.setattr(updates, "_open", lambda url, timeout=15: FakeResponse(b"HELLO"))
    with pytest.raises(updates.UpdateError, match="checksum"):
        updates.download(up)
    monkeypatch.setattr(updates, "_open", lambda url, timeout=15: FakeResponse(b"hell"))
    with pytest.raises(updates.UpdateError, match="incomplete"):
        updates.download(up)


def test_download_refuses_other_hosts():
    up = updates.from_release(release())
    up.url = "https://evil.example/ShapeShifter-Setup.exe"
    with pytest.raises(updates.UpdateError):
        updates.download(up)


def test_offline_is_a_friendly_error(monkeypatch):
    def boom(*a, **k):
        raise OSError("no network")
    monkeypatch.setattr(updates, "_open", boom)
    with pytest.raises(updates.UpdateError, match="internet"):
        updates.latest()


def test_checked_once_a_day_and_announced_once(state):
    cfg = {"check_updates": True}
    assert updates.due(cfg)
    updates.remember_check()
    assert not updates.due(cfg) and updates.due(cfg, now=__import__("time").time() + 25 * 3600)
    assert not updates.due({"check_updates": False}, now=__import__("time").time() + 99999)
    up = updates.from_release(release())
    assert updates.should_announce(up)
    updates.remember_check(up.version)
    assert not updates.should_announce(up)
    assert updates.due(cfg)                      # a known newer version: look again so the tray can offer it
    up2 = updates.from_release(release("v9.1.0"))
    assert updates.should_announce(up2)
    updates.skip("9.1.0")
    assert not updates.should_announce(up2)


def test_available_only_when_newer(monkeypatch):
    monkeypatch.setattr(updates, "latest", lambda: updates.from_release(release("v0.0.1")))
    assert updates.available() is None
    monkeypatch.setattr(updates, "latest", lambda: updates.from_release(release("v99.0.0")))
    assert updates.available().version == "99.0.0"


def test_dialog_states():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])          # keep a reference: it must outlive the dialog
    from shapeshifter.updates_dialog import UpdateDialog
    d = UpdateDialog(updates.from_release(release()))
    assert "9.0.0 is available" in d.title.text() and d.go.text() in ("Update now", "Open download page")
    assert d.notes.toPlainText() == "Notes"
    d.close()
    d.deleteLater()
    app.processEvents()
