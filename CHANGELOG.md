# Changelog

## Unreleased
- **Settings redesigned**: every page is now built from the same card-based look (instead of the OS's native group boxes), so it reads as one consistent app in both light and dark mode. The side list is grouped: General, Wheel, Hotkeys, Formats and Engines are always shown; Advanced and Graph sit below a divider for when you need them; About is pinned to the bottom.
- **A new Advanced page** collects the settings most people never touch: JPEG/WEBP/AVIF quality and PDF resolution, "prefer native converters," the OCR language picker, the wheel's ring size and hover-to-convert, learned reverse conversions, custom formats, and exporting the conversion graph (DOT/JSON). General, Wheel and Formats & mappings keep only what everyday use needs; the in-page route finder and graph export on the old Formats page are gone (the Graph page already covers routes, with a working 3D view).

## 2.9.0
- **ShapeShifter for Mac** (Apple Silicon, macOS 12+): the same app as on Windows. A menu-bar app with **right-click › Quick Actions › Convert with ShapeShifter** in Finder, hotkeys (**⌃⌥C**) and mouse triggers over Finder and the desktop, the wheel, Fill form, Properties, every converter, text recognition with the one built into macOS, the Trash for originals, and updates that install themselves. Download `ShapeShifter-macOS.dmg`.
- Settings on a Mac show whether the Accessibility and Automation permissions the hotkeys need are on, with buttons to the right System Settings page; the Engines page installs ffmpeg, pandoc, LibreOffice and 7-Zip with Homebrew.

## 2.8.0
- **Move and resize fields** in the Fill form window: drag a frame to move it, drag its edges or corners to resize it (check boxes stay square), arrow keys nudge the selected field (Shift for 10 px) and Delete removes it. Works for found fields and ones you add.
- **Fonts and sizes**: the writing can be any font installed in Windows, not only Typed / Handwriting / Typewriter, and the size is a percentage you type. Select a field to give that one answer its own font and size.
- **Fill form finds far more blanks**: dotted leaders ("........"), second lines with no label, several blanks on one line ("FROM THE …… day of …… 20……", "__ / __ / ____"), blanks inside sentences ("I, ______, residing at ______"), empty cells of ruled tables (named by their row and column, e.g. "Name — Wrong Entry"), boxes with a heading above them, and signature lines with the caption printed underneath. Junk fields from long sentences are gone.
- **Crooked and grey scans**: a photo or scan that's a few degrees off is straightened before reading, and the ink threshold follows the paper's shade, so the answers still land on the lines.
- **Better names for fields**: hints printed under a blank ("(full address)") are used, short labels are completed from the sentence ("my mobile number is"), table headings are re-read with the rules removed so letters touching them aren't lost, and words the OCR skipped (a lone "No" beside a check box) get a second, closer look. Check boxes take the label on whichever side is nearer.

## 2.7.0
- **Updates**: ShapeShifter checks GitHub once a day and shows a notification and a tray entry when a newer version exists. **Update now** downloads the installer, checks it against its published SHA-256, and upgrades in place (settings and hotkeys are kept). Also in tray › Check for updates… and Settings › About; "Skip this version" and an on/off switch are there too.

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
