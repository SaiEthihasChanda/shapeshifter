# Changelog

## 2.4.0
- **Fill form** for pictures and PDFs of forms: finds blanks, check boxes and signature lines, opens a window to type
  answers, tick boxes and add a drawn or imported signature, and saves a filled copy beside the original.
  PDFs are filled across all pages (answers laid over the page, real PDF form fields filled natively), and several
  forms can be opened at once.
  Also available as `shapeshifter fill`.
- **Targets that ask how.** DOCX and PDF are shown once; for a picture they can mean *Text (OCR)* / *Picture* (Word)
  and *Picture* / *Searchable* (PDF). ShapeShifter looks inside the file first and only asks when there is a real
  choice (a picture without text just converts). Back with the centre button or Esc.
- New **Picture** way of making a Word document from an image: embeds the picture on a Word page (no OCR needed).
- **OCR PDF** is only offered for PDFs that have scanned pages.
- Options that only exist from reading the file (Fill form, OCR to TXT/Markdown/HTML) share one **Inferred** group.

## 2.3.0
- Multi-ring wheel with rotating reveal animation and type-to-search.
- Format graph: multi-step conversion routes, remembered inverse conversions, interactive 3-D graph in settings.
- OCR engine (Windows OCR): scans and images can become text, Word documents or searchable PDFs.
- PDF and document conversions keep pictures, links, tables and headings.
- Content-based file identification, with refusal of risky files.
- New Inno Setup installer (per-user, no admin rights, optional ffmpeg component), exe version info,
  AGPL-3.0-or-later licence and third-party notices.
