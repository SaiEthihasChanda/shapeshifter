# ShapeShifter 2.5.0

The biggest release so far: more file types, and the wheel now understands what is *inside* your files.

## New things you can do
- **Fill in forms** that are only pictures or PDFs: blanks, check boxes and signatures are found for you.
- **Tables** in both directions: PDF, Word, web pages, slides and even pictures of tables into Excel / CSV / JSON, and spreadsheets into Word, PDF, HTML and Markdown.
- **Presentations**: PowerPoint and OpenDocument slides to PDF, pictures, text, Word, and back; PDFs and documents into slides.
- **Combine** several selected files into one: pictures into a GIF, PDF, slide deck or slideshow video; PDFs; workbooks; documents; audio and video clips.
- **Same-format actions**: make a picture, PDF or video smaller, resize, rotate, remove location data, split a PDF, tidy JSON, fix line endings, even out audio, remove a video's sound.
- **Smarter choices**: the wheel asks *how* (every frame or the first, every sheet, one tall image, a contact sheet of a video…) only when the file really has a choice. Videos offer frames and subtitles; silent videos don't offer audio.

## More file types
Old **.doc / .xls / .ppt**, **RTF**, **ODT**, Outlook **.msg**, **camera RAW** previews (CR2, NEF, ARW, DNG, ORF, RW2, RAF), **glTF / GLB** 3D models, **TTC** fonts, and with 7-Zip installed **RAR, ISO, CAB, WIM, DMG, VHD(X)** and more.

## Good to know
- Old binary Office files are read for text, tables and values, not formatting; LibreOffice does them better if installed.
- Slides are redrawn from their shapes. The PPTX files written here follow the standard and open in pandoc but haven't been opened in PowerPoint itself.
- RAW conversion uses the camera's full-size preview JPEG, not developed sensor data.
- 7-Zip support is untested against a real 7-Zip in this release.

**Download:** `ShapeShifter-Setup-2.5.0.exe` (Windows 10/11, 64-bit). Verify it against `SHA256SUMS.txt`.
The installer is not code-signed, so SmartScreen may warn: choose **More info > Run anyway**. It installs per user and upgrades earlier versions in place.

Licence: GNU AGPL-3.0-or-later. Third-party notices: `THIRD-PARTY-NOTICES.md`.
