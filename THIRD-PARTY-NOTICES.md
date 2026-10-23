# Third-party notices

ShapeShifter 2.6.1 is released under the **GNU AGPL v3 or later** (see [LICENSE](LICENSE)). The Windows installer bundles the components below, each under its own licence. This file is generated from the contents of the built application by `packaging/collect_licenses.py`; the installer also ships the full licence texts.

## Components

| Component | Licence | Notes |
|---|---|---|
| [Python 3.12](https://docs.python.org/3/license.html) | PSF-2.0 | The Python runtime is bundled in the application folder. |
| [Qt 6 (via PySide6) 6.x](https://www.qt.io/licensing/open-source-lgpl-obligations) | LGPL-3.0-only | Qt is used as dynamic libraries (separate .dll files in the application folder), so you may replace them with your own build. Source: https://download.qt.io/official_releases/qt/ |
| [MuPDF (inside PyMuPDF)](https://github.com/ArtifexSoftware/mupdf) | AGPL-3.0 (or Artifex commercial) | PDF engine. ShapeShifter itself is also AGPL-3.0-or-later; see SOURCE.txt. |
| [Windows OCR (Windows.Media.Ocr)](https://learn.microsoft.com/uwp/api/windows.media.ocr) | Part of Windows | Called through Windows PowerShell on your PC. Not distributed with ShapeShifter. |
| [pandoc, LibreOffice](https://pandoc.org and https://www.libreoffice.org) | GPL-2.0-or-later / MPL-2.0 | Optional: ShapeShifter uses them if you install them yourself. Not distributed with it. |
| [ffmpeg version 9.0.2-full_build-www.gyan.dev](https://ffmpeg.org) | GPL-3.0-or-later (this build enables GPL components) | Runs as a separate program. Source: <https://ffmpeg.org/download.html#get-sources>; build: <https://www.gyan.dev/ffmpeg/builds/> |

## Python packages

| Package | Version | Licence |
|---|---|---|
| [backports.zstd](https://github.com/rogdham/backports.zstd) | 1.8.0 | PSF-2.0 |
| [brotli](https://github.com/google/brotli) | 1.2.0 | MIT |
| [comtypes](https://github.com/enthought/comtypes) | 1.4.17 | MIT |
| [et_xmlfile](https://foss.heptapod.net/openpyxl/et_xmlfile) | 2.0.0 | MIT |
| [inflate64](https://github.com/miurahr/inflate64) | 1.0.4 | LGPL-2.1-or-later |
| [multivolumefile](https://github.com/miurahr/multivolume) | 0.2.3 | LGPL-2.1+ |
| [openpyxl](https://openpyxl.readthedocs.io) | 3.1.5 | MIT |
| [pillow](https://python-pillow.org) | 13.0.0 | MIT-CMU (HPND) |
| [pillow_heif](https://github.com/bigcat88/pillow_heif) | 1.8.1 | BSD-3-Clause |
| [psutil](https://github.com/giampaolo/psutil) | 7.2.2 | BSD-3-Clause |
| [py7zr](https://py7zr.readthedocs.io/) | 1.1.4 | LGPL-2.1-or-later |
| [pybcj](https://pypi.org/project/pybcj) | 1.0.8 | LGPL-2.1-or-later |
| [pycryptodomex](https://www.pycryptodome.org) | 3.24.1 | BSD-2-Clause / Public Domain |
| [pymupdf](https://github.com/pymupdf/pymupdf) | 2.0 | AGPL-3.0 (or Artifex commercial) |
| [pyppmd](https://github.com/miurahr/pyppmd) | 1.3.1 | LGPL-2.1-or-later |
| [PySide6_Essentials](https://pyside.org) | 6.12.0 | LGPL-3.0-only (Qt for Python) |
| [pywin32](https://github.com/mhammond/pywin32) | 312 | PSF-2.0 |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT |
| [Send2Trash](https://github.com/arsenetar/send2trash) | 2.1.0 | BSD-3-Clause |
| [shiboken6](https://pyside.org) | 6.12.0 | LGPL-3.0-only |
| [tabulate](https://github.com/astanin/python-tabulate) | 0.10.0 | MIT |
| [texttable](https://github.com/foutaise/texttable/) | 1.7.2 | MIT |

## Source code

The complete corresponding source of ShapeShifter is at <https://github.com/SaiEthihasChanda/shapeshifter>. For components that are only re-distributed (Qt, FFmpeg, Python), use the links above; a copy of ffmpeg's licence is included with the installer.
