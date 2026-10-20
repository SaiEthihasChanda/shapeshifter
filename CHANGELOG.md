# Changelog

## 2.4.0
- **Fill form** for pictures and PDFs of forms: finds blanks, check boxes and signature lines, opens a window to type
  answers, tick boxes and add a drawn or imported signature, and saves a filled copy beside the original.
  PDFs are filled across all pages (answers laid over the page, real PDF form fields filled natively), and several
  forms can be opened at once.
  Also available as `shapeshifter fill`.
- New **Word + picture** target for images: embeds the picture in a Word document (no OCR needed).
- Options that come from reading the file's contents (OCR, Word + picture, OCR PDF, Fill form) now share one
  **Inferred** group on the wheel.

## 2.3.0
- Multi-ring wheel with rotating reveal animation and type-to-search.
- Format graph: multi-step conversion routes, remembered inverse conversions, interactive 3-D graph in settings.
- OCR engine (Windows OCR): scans and images can become text, Word documents or searchable PDFs.
- PDF and document conversions keep pictures, links, tables and headings.
- Content-based file identification, with refusal of risky files.
- New Inno Setup installer (per-user, no admin rights, optional ffmpeg component), exe version info,
  AGPL-3.0-or-later licence and third-party notices.
