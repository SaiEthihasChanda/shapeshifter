# Releasing ShapeShifter

1. Bump `__version__` in `shapeshifter/__init__.py`; update `CHANGELOG.md` and `docs/release-notes-<ver>.md`.
2. `.\build.ps1` (needs Python 3.12 from python.org, Inno Setup 6, ffmpeg on PATH). Output in `dist\`:
   `ShapeShifter-Setup-<ver>.exe`, `ShapeShifter-Setup.exe` (stable-name copy), `SHA256SUMS.txt`, source zip.
3. Test on a clean machine or VM: install, Ctrl+Alt+C, uninstall.
4. Commit, tag `v<ver>`, push; create a GitHub release and upload the three files above.
5. Update the hash and size in your portfolio snippet (`docs/portfolio/download.html`).

Signing (removes most SmartScreen friction): `.\build.ps1 -Sign -SignCommand 'signtool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /a $f'`.
Options: Azure Trusted Signing, an OV/EV certificate, or SignPath's free programme for open-source projects.

Licence duty: AGPL requires offering the source of the exact version distributed. Keep the tag and the release
source zip in sync with the installer you publish.
