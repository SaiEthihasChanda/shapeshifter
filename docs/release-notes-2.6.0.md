# ShapeShifter 2.6.0

## New: Properties
Choose **PROPERTIES** on the wheel to see and change the details stored inside a file.
- **Pictures:** description, author, copyright, date taken, camera, lens and GPS location. JPEG and PNG are edited without recompressing, so there is no quality loss.
- **PDF, Word, Excel, PowerPoint, OpenDocument:** title, subject, keywords, author, company and dates.
- **Audio and video:** title, artist, album, genre, year, track and more, without re-encoding.
- **Any file:** its created and modified dates.
- Remove only the location, remove all personal details, or select many files and set the same author on all of them.
- Command line: `shapeshifter meta photo.jpg --set Author=Ada --no-location`.

## Good to know
- WebP, TIFF, HEIC and AVIF pictures have to be saved again to carry the changes.
- The installer is not code-signed, so SmartScreen may warn: choose **More info > Run anyway**. It upgrades earlier versions in place.

**Download:** `ShapeShifter-Setup-2.6.0.exe` (Windows 10/11, 64-bit). Verify it against `SHA256SUMS.txt`.

Licence: GNU AGPL-3.0-or-later. Third-party notices: `THIRD-PARTY-NOTICES.md`.
