# Changelog

## 2.6.1
- **Fewer wheel entries**: "ONE GIF / ONE PDF …" with several files selected is now a hidden choice inside the GIF / PDF entry (separate, or all in one), and "NO LOCATION" moved inside PROPERTIES.

## 2.6.0
- **Properties**: view and edit a file's metadata from the wheel (or `shapeshifter meta`). Pictures (EXIF: author, copyright, description, date taken, camera, GPS location; JPEG and PNG are edited without recompressing), PDF, Word / Excel / PowerPoint / OpenDocument (title, author, subject, keywords, company, dates), audio and video tags (title, artist, album, year, track …), and the file's own created / modified dates. Remove location only, remove everything, or edit many files at once.

## 2.5.0
- **Tables** in both directions between documents and data: PDF, Word, ODT, HTML, Markdown, slides and pictures ↔ CSV / TSV / Excel / ODS / JSON, and spreadsheets → HTML / Markdown / Word / PDF.
- **Presentations**: PPTX / ODP read, drawn to PDF, turned into outlines, converted to each other; PDFs, pictures and documents → slides.
- **More ways to choose**: every frame / sheet / page, video frames, contact sheets, subtitle extraction, smaller / silent / short video and GIF.
- **Combine** several selected files (pictures → GIF / PDF / deck / slideshow, PDFs, workbooks, documents, decks, audio and video, fonts).
- **Same-format actions**: smaller, resize, rotate, no location, PDF split / compress / pictures, tidy JSON, fix text, audio volume, video smaller / no sound.
- **Older and other files**: .doc / .xls / .ppt, RTF and ODT writing, Outlook .msg, camera RAW previews, glTF / GLB, TTC fonts, and many more archive types through 7-Zip.
- Fixes: a PDF layout that could loop forever on wide tables, a UTF-16 text file mistaken for MP3, text-heavy slides rendering slowly.
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
