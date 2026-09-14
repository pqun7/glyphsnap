# Arabic Newspaper OCR

High-quality Arabic OCR for scanned newspapers, images, and PDF documents on Windows.
The application is a privacy-first native desktop tool: files are processed locally and
are never uploaded to a web service.

![Arabic Newspaper OCR interface](docs/README-image.png)

## Highlights

- Extract Arabic text from PDF, PNG, JPG, JPEG, WebP, BMP, and TIFF files.
- Process a single page or an entire PDF document.
- Review the source image beside an editable right-to-left Arabic editor.
- Select a region of the image and re-run OCR for difficult lines or columns.
- Insert a corrected regional result at the cursor or replace selected text.
- Export the complete document as UTF-8 text.
- Native Windows interface with no browser, local server, or account required.
- Application and window icon included in the source and packaged builds.

## Download for Windows

Download the latest `ArabicNewspaperOCR_Setup.exe` from the repository's
[Releases](../../releases/latest) page. The installer includes the complete native
application. Tesseract OCR must be installed separately because its language data is
distributed under its own project and license.

### Install the OCR engine

1. Install [Tesseract OCR for Windows](https://github.com/UB-Mannheim/tesseract/wiki).
2. During installation, include the **Arabic (`ara`)** language data.
3. The application automatically checks these locations:
   `C:\Program Files\Tesseract-OCR\tesseract.exe`,
   `C:\Program Files (x86)\Tesseract-OCR\tesseract.exe`, and the system `PATH`.

To verify the Arabic data:

```powershell
tesseract.exe --list-langs
```

The output must contain `ara`.

## How to use

1. Select **Open PDF or image** and choose a supported file.
2. Use **Extract page/image** for the current page, or **Extract full PDF** for every
   page.
3. Edit the recognized text in the right-hand editor. Page text is preserved while
   navigating through a PDF.
4. For a difficult region, drag over the text in the preview, choose **Extract selected
   region**, then insert or replace the result in the main editor.
5. Export the reviewed text as a UTF-8 `.txt` file.

Useful shortcuts:

| Shortcut | Action |
| --- | --- |
| `Ctrl+O` | Open a file |
| `Ctrl+S` | Export text |
| `Ctrl+C`, `Ctrl+X`, `Ctrl+V` | Copy, cut, and paste |
| `Ctrl+Z`, `Ctrl+Y` | Undo and redo |
| `Ctrl` + mouse wheel | Zoom the preview |

OCR quality depends on scan resolution, contrast, and layout. Review the result
against the original image before publishing or using it as archival data.

## Run from source

Python 3.14 or a compatible Python version is recommended.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python arabic_newspaper_ocr_native.py
```

The runtime dependencies are PySide6, PyMuPDF, Pillow, and pytesseract. Tesseract
itself is an external system dependency and is intentionally not bundled.

## Build a Windows release

The repository contains reproducible PowerShell build scripts. From an activated
environment with the required build tools installed:

```powershell
python -m pip install -r requirements.txt
python -m pip install Nuitka
.\build_nuitka.ps1
```

The standalone output is written to `builds\nuitka\arabic_newspaper_ocr_native.dist`.
The script embeds the application icon, includes the runtime icon asset, and runs a
PDF OCR smoke test. The installer can be generated with Inno Setup using
`installer_script.iss`.

For development or an alternative packaging path:

```powershell
python -m pip install pyinstaller
.\build_pyinstaller.ps1
```

## Project structure

```text
arabic_newspaper_ocr_qt.py   Native Qt user interface and OCR workflow
arabic_newspaper_ocr_core.py Optional advanced OCR engine
arabic_newspaper_ocr_native.py Compatibility entry point
assets/app.ico               Application icon
build_nuitka.ps1             Reproducible Nuitka release build
installer_script.iss         Windows installer definition
docs/README-image.png        README product screenshot
```

## Open source and license

Copyright © 2026 **Ali Alnazer Ahmed**. This project is released under the
[MIT License](LICENSE). Contributions, bug reports, and improvements are welcome.

Tesseract OCR, Qt/PySide6, PyMuPDF, and Pillow remain subject to their respective
licenses. See their official projects for details.

## Disclaimer

OCR is probabilistic. The author does not guarantee perfect recognition for damaged,
low-resolution, handwritten, or unusually formatted documents. Always verify extracted
text against the original source.
