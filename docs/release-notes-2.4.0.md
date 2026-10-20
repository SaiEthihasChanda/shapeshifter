# ShapeShifter 2.4.0

**Fill in forms that are only pictures or PDFs.** Choose **FILL FORM** on the wheel for a photo, a scan, or a PDF.
ShapeShifter finds the blanks, check boxes and signature lines; you type the answers, tick boxes and add a signature
(draw it or import a picture); it saves a filled copy next to the original, which is never changed. PDFs work page by page; real fillable PDF fields are filled natively and stay editable.

**Also new**
- **Targets that ask how.** DOCX and PDF appear once on the wheel. For a picture, DOCX can be *Text (OCR)* or *Picture* (the picture on a Word page, for adding text and converting back), and PDF can be *Picture* or *Searchable*. ShapeShifter looks inside the file first and only asks when there is a real choice.
- **OCR PDF** is only offered for PDFs that have scanned pages.
- A pink **Inferred** group holds what only exists because the file was read: Fill form, and OCR to TXT/Markdown/HTML.
- `shapeshifter fill` on the command line.

**Download:** `ShapeShifter-Setup-2.4.0.exe` (Windows 10/11, 64-bit). Verify it against `SHA256SUMS.txt`.
The installer is not code-signed, so SmartScreen may warn: choose **More info > Run anyway**. It installs per user and
upgrades earlier versions in place.

Licence: GNU AGPL-3.0-or-later. Third-party notices: `THIRD-PARTY-NOTICES.md`.
