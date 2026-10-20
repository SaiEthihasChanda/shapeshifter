"""Write a PyInstaller version-info file so the .exe shows a publisher, product and version
in Explorer's Properties dialog (and looks less anonymous to SmartScreen and antivirus).

    python packaging/make_version_info.py <out.txt> "<file description>" <original filename>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shapeshifter import APP_NAME, AUTHOR, COPYRIGHT, SOURCE_URL, __version__  # noqa: E402


def main(out: str, description: str, original: str) -> None:
    nums = [int(x) for x in (re.findall(r"\d+", __version__) + ["0", "0", "0"])[:4]]
    vtuple = ", ".join(map(str, nums))
    text = f"""# UTF-8
VSVersionInfo(
  ffi=FixedFileInfo(filevers=({vtuple}), prodvers=({vtuple}), mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', '{AUTHOR}'),
      StringStruct('FileDescription', '{description}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', '{APP_NAME}'),
      StringStruct('LegalCopyright', '{COPYRIGHT}. Licensed under the GNU AGPL v3 or later.'),
      StringStruct('OriginalFilename', '{original}'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}'),
      StringStruct('Comments', 'Source code: {SOURCE_URL}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    Path(out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[1:4])
