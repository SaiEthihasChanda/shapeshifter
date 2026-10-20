"""Entry point for the packaged ShapeShifter.exe (PyInstaller)."""

import sys

from shapeshifter.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
