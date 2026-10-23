"""Finish ShapeShifter.app after PyInstaller: Info.plist keys and the licence files.

    python packaging/mac_bundle.py dist/ShapeShifter.app 2.9.0 [licenses-dir]

* LSUIElement: a menu-bar app (no Dock icon unless Settings is open).
* CFBundleDocumentTypes: accepts any file, so Finder's Quick Action (``open -b``), Open With and dropping files on
  the app all hand them over as "open" events.
* Usage descriptions for the permissions macOS asks for: controlling Finder (reading its selection) and the
  Desktop / Documents / Downloads folders (converting files there).
"""

from __future__ import annotations

import plistlib
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from shapeshifter import AUTHOR, COPYRIGHT  # noqa: E402
from shapeshifter.mac import BUNDLE_ID  # noqa: E402

WHY_FILES = "ShapeShifter converts the files you choose and saves the result next to them."


def main() -> int:
    app = Path(sys.argv[1])
    version = sys.argv[2]
    licenses = Path(sys.argv[3]) if len(sys.argv) > 3 else None
    info_path = app / "Contents" / "Info.plist"
    with open(info_path, "rb") as f:
        info = plistlib.load(f)
    info.update({
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleName": "ShapeShifter",
        "CFBundleDisplayName": "ShapeShifter",
        "CFBundleShortVersionString": version,
        "CFBundleVersion": version,
        "LSMinimumSystemVersion": "12.0",
        "LSUIElement": True,
        "LSApplicationCategoryType": "public.app-category.utilities",
        "NSHighResolutionCapable": True,
        "NSHumanReadableCopyright": COPYRIGHT,
        "CFBundleDocumentTypes": [{
            "CFBundleTypeName": "Any file",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["public.item", "public.data", "public.content"],
        }],
        "NSAppleEventsUsageDescription": "ShapeShifter asks Finder which files are selected when you press a "
                                         "ShapeShifter hotkey or mouse trigger.",
        "NSDesktopFolderUsageDescription": WHY_FILES,
        "NSDocumentsFolderUsageDescription": WHY_FILES,
        "NSDownloadsFolderUsageDescription": WHY_FILES,
        "NSRemovableVolumesUsageDescription": WHY_FILES,
        "NSNetworkVolumesUsageDescription": WHY_FILES,
        "ShapeShifterAuthor": AUTHOR,
    })
    with open(info_path, "wb") as f:
        plistlib.dump(info, f)

    res = app / "Contents" / "Resources"
    res.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "LICENSE", res / "LICENSE.txt")
    for name in ("NOTICE", "README.md", "CHANGELOG.md"):
        if (ROOT / name).exists():
            shutil.copyfile(ROOT / name, res / (name if "." in name else f"{name}.txt"))
    if licenses and licenses.is_dir():
        notice = licenses / "THIRD-PARTY-NOTICES.txt"
        if notice.exists():
            shutil.copyfile(notice, res / "THIRD-PARTY-NOTICES.txt")
        if (licenses / "licenses").is_dir():
            shutil.copytree(licenses / "licenses", res / "licenses", dirs_exist_ok=True)
    print(f"Info.plist: {BUNDLE_ID} {version}; licences in {res}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
