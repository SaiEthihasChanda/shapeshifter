"""Finding, downloading and installing a newer ShapeShifter from the project's GitHub releases.

Nothing is sent except an ordinary request for the public "latest release" page. The installer is checked against the
SHA-256 GitHub publishes for it before it is run, and it is the same per-user installer people download by hand: it
closes the running app, upgrades in place and starts it again.

On a Mac the download is the same .dmg people download by hand; a small script waits for ShapeShifter to quit,
replaces ShapeShifter.app with the one in the .dmg and starts it again.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import __version__, config

log = logging.getLogger(__name__)

REPO = "SaiEthihasChanda/shapeshifter"
API = f"https://api.github.com/repos/{REPO}/releases/latest"
PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_WINDOWS = "ShapeShifter-Setup.exe"
ASSET_MAC = "ShapeShifter-macOS.dmg"
ASSET = ASSET_MAC if sys.platform == "darwin" else ASSET_WINDOWS
CHECK_EVERY = 24 * 3600


class UpdateError(Exception):
    """Something went wrong while checking or updating; the message is shown to the user."""


@dataclass
class Update:
    version: str
    url: str
    size: int
    sha256: str
    notes: str
    page: str


def parse_version(text: str) -> tuple:
    nums = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(candidate: str, current: str = __version__) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def _open(url: str, timeout: float = 15):
    req = urllib.request.Request(url, headers={"User-Agent": f"ShapeShifter/{__version__}", "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def from_release(data: dict) -> Update | None:
    """The installer of a GitHub release record, or None when it has none."""
    asset = next((a for a in data.get("assets", []) if a.get("name") == ASSET), None)
    tag = str(data.get("tag_name", "")).lstrip("v")
    if not asset or not tag or data.get("draft") or data.get("prerelease"):
        return None
    digest = str(asset.get("digest") or "")
    return Update(version=tag, url=asset["browser_download_url"], size=int(asset.get("size") or 0),
                  sha256=digest[7:].lower() if digest.startswith("sha256:") else "",
                  notes=str(data.get("body") or "").strip(), page=str(data.get("html_url") or PAGE))


def latest() -> Update | None:
    """The newest release, whether or not it is newer than this version. Raises UpdateError when offline."""
    try:
        with _open(API) as r:
            return from_release(json.load(r))
    except Exception as e:                                    # noqa: BLE001 - offline, rate limited, bad JSON …
        raise UpdateError("Couldn't reach GitHub to look for updates. Check your internet connection.") from e


def available() -> Update | None:
    """A release newer than the one running, or None."""
    up = latest()
    return up if up and is_newer(up.version) else None


def due(cfg: dict | None = None, now: float | None = None) -> bool:
    cfg = cfg or config.load()
    if not cfg.get("check_updates", True):
        return False
    state = config.load_state()
    if is_newer(str(state.get("update_seen") or "0")):
        return True                # one is known: fetch it again so the tray can offer it
    return (now or time.time()) - float(state.get("update_checked", 0)) >= CHECK_EVERY


def remember_check(version: str | None = None) -> None:
    config.save_state(update_checked=time.time(), **({"update_seen": version} if version else {}))


def should_announce(up: Update) -> bool:
    """Tell the user once per version, and never about a version they chose to skip."""
    state = config.load_state()
    return up.version not in (state.get("update_seen"), state.get("update_skipped"))


def skip(version: str) -> None:
    config.save_state(update_skipped=version)


def download(up: Update, progress=None, cancelled=lambda: False) -> Path:
    """Download the installer to a temporary folder and check it against the published checksum."""
    if not up.url.startswith("https://github.com/"):
        raise UpdateError("The update isn't hosted where expected, so it was not downloaded.")
    folder = Path(tempfile.mkdtemp(prefix="shapeshifter-update-"))
    dest = folder / (f"ShapeShifter-{up.version}.dmg" if sys.platform == "darwin" else f"ShapeShifter-Setup-{up.version}.exe")
    h = hashlib.sha256()
    try:
        with _open(up.url, timeout=60) as r, open(dest, "wb") as f:
            total = int(r.headers.get("Content-Length") or up.size or 0)
            done = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                if cancelled():
                    raise UpdateError("Cancelled")
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total)
    except UpdateError:
        _cleanup(dest)
        raise
    except Exception as e:                                    # noqa: BLE001
        _cleanup(dest)
        raise UpdateError("The download failed. Check your internet connection and try again.") from e
    if up.size and dest.stat().st_size != up.size:
        _cleanup(dest)
        raise UpdateError("The download was incomplete. Try again.")
    if up.sha256 and h.hexdigest() != up.sha256:
        _cleanup(dest)
        raise UpdateError("The downloaded installer doesn't match its published checksum, so it was not run.")
    return dest


def _cleanup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:
        pass


MAC_SWAP = r"""#!/bin/sh
# ShapeShifter update: wait for the app to quit, replace it with the copy in the .dmg, start it again.
pid="$1"; dmg="$2"; app="$3"
i=0
while kill -0 "$pid" 2>/dev/null && [ $i -lt 150 ]; do sleep 0.2; i=$((i+1)); done
mnt=$(mktemp -d /tmp/shapeshifter-update.XXXXXX)
hdiutil attach -nobrowse -readonly -noautoopen -mountpoint "$mnt" "$dmg" >/dev/null || { open "$app"; exit 1; }
new="$mnt/ShapeShifter.app"
if [ -d "$new" ]; then
    rm -rf "$app.updating"
    if ditto "$new" "$app.updating"; then
        rm -rf "$app" && mv "$app.updating" "$app"
    fi
fi
hdiutil detach "$mnt" -quiet || hdiutil detach "$mnt" -force -quiet
rmdir "$mnt" 2>/dev/null
xattr -dr com.apple.quarantine "$app" 2>/dev/null
rm -f "$dmg"
open "$app"
"""


def install(installer: Path) -> None:
    """Run the installer and return at once; it closes this app, upgrades it and starts the new one.
    On a Mac the caller quits the app right after this returns; the swap script waits for that."""
    if sys.platform == "darwin":
        from .mac.appkit import app_bundle
        bundle = app_bundle()
        if bundle is None:
            raise UpdateError("This copy of ShapeShifter isn't an installed app, so it can't update itself.")
        script = installer.with_name("update.sh")
        script.write_text(MAC_SWAP, encoding="utf-8")
        try:
            subprocess.Popen(["/bin/sh", str(script), str(os.getpid()), str(installer), str(bundle)],
                             start_new_session=True, close_fds=True, cwd=str(installer.parent),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            raise UpdateError(f"Couldn't start the update: {e}") from e
        return
    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        subprocess.Popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], creationflags=flags, close_fds=True,
                         cwd=str(installer.parent))
    except OSError as e:
        raise UpdateError(f"Couldn't start the installer: {e}") from e


def installed_per_user() -> bool:
    """Updating in place only makes sense for the installed app, not a source checkout."""
    if not getattr(sys, "frozen", False):
        return False
    if sys.platform == "darwin":
        from .mac.appkit import app_bundle
        bundle = app_bundle()
        return bundle is not None and os.access(bundle.parent, os.W_OK) and "/AppTranslocation/" not in str(bundle)
    return os.path.exists(Path(sys.executable).parent / "unins000.exe")
