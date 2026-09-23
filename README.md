# Arabic Newspaper OCR

[![Tests](https://github.com/pqun7/arabic-newspaper-ocr/actions/workflows/tests.yml/badge.svg)](https://github.com/pqun7/arabic-newspaper-ocr/actions/workflows/tests.yml)
[![Python](https://img.shields.io/badge/Python-3.13%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D4?logo=windows)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

A privacy-first Windows OCR desktop application for Arabic newspapers, historical
scans, screenshots, images, and PDFs. It turns difficult document images into
editable, searchable text without uploading files to a cloud service.

![Arabic Newspaper OCR desktop interface](docs/README-image.png)

## Why this project?

Historical Arabic newspapers combine degraded paper, noise, skew, complex columns,
and right-to-left text. Generic copy tools often lose text or produce an unreadable
layout. This project combines document image processing, multilingual OCR, a native
review workflow, and an optional high-recall research pipeline in one end-to-end AI
desktop application.

## Features

- **Capture anywhere:** press `Ctrl + Shift + O`, select part of any screen, run OCR,
  review the result, and copy it automatically.
- **Arabic and multilingual OCR:** Arabic, English, combined Arabic + English, and any
  additional Tesseract language installed on the computer.
- **Automatic image enhancement:** conservative deskew, denoise, CLAHE contrast,
  adaptive thresholding, and resolution-aware upscaling.
- **Best-result selection:** runs multiple image variants and keeps the result with
  the strongest OCR confidence and text coverage.
- **Document workflow:** open PDF/image files, process one page or a complete PDF,
  select difficult regions, edit bidirectional text, and export UTF-8 text.
- **Local by design:** no account, server, browser, or file upload.
- **Auditable advanced pipeline:** optional PaddleOCR layout + recall passes,
  Tesseract verification, orphan-line preservation, JSON diagnostics, TXT, and DOCX.

## Quick start

### 1. Install Tesseract

Install [Tesseract for Windows](https://github.com/UB-Mannheim/tesseract/wiki) and
select the Arabic (`ara`) and English (`eng`) language packs. Other installed packs
appear automatically in the app.

```powershell
tesseract.exe --list-langs
```

### 2. Run from source

```powershell
git clone https://github.com/pqun7/arabic-newspaper-ocr.git
cd arabic-newspaper-ocr
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python arabic_newspaper_ocr_native.py
```

Use `Ctrl + Shift + O` from any Windows application, drag over text, then review the
recognized result in the editor. `Ctrl+O` opens a file and `Ctrl+S` exports text.

## Accuracy and testing

OCR quality depends on the source, language model, scan resolution, and layout. The
application reports Tesseract's mean word confidence to make review decisions visible;
this is a useful signal, not a guarantee of correctness.

The regression suite covers:

- deskew estimation and preprocessing variants;
- degraded synthetic English text;
- a low-quality Arabic newspaper region from the project sample;
- PDF/image loading and critical UI controls.

```powershell
python -m unittest -v test_ocr_engine.py
python ui_smoke_test.py
```

For archival or legally important documents, always compare the output with the scan.
A public, licensed Arabic newspaper ground-truth dataset is planned for CER/WER
benchmarking; synthetic tests alone are not presented as production accuracy claims.

## Architecture

```text
Screen/PDF/Image
      │
      ▼
Deskew → Denoise → Contrast → Threshold → Upscale
      │
      ▼
Multilingual Tesseract OCR → Confidence-based variant selection
      │
      ▼
RTL/LTR review editor → Copy or UTF-8 export
```

| Component | Responsibility |
| --- | --- |
| `arabic_newspaper_ocr_qt.py` | Native Windows UI and document workflow |
| `ocr_engine.py` | Languages, preprocessing, OCR, and confidence selection |
| `screen_capture.py` | Global hotkey and multi-monitor region capture |
| `arabic_newspaper_ocr_core.py` | Optional high-recall newspaper research pipeline |
| `test_ocr_engine.py` | Repeatable OCR and image-processing regressions |

Built with Python, PySide6, OpenCV, Pillow, PyMuPDF, Tesseract, and optional
PaddleOCR. The separation between UI, OCR, and capture code keeps the project easier
to test, extend, and discuss in ML/AI engineering interviews.

## Build for Windows

Building can take several minutes. Install the requirements and Nuitka, then run:

```powershell
python -m pip install -r requirements.txt
python -m pip install nuitka ordered-set zstandard
.\build_nuitka.ps1
```

Output: `builds\nuitka\arabic_newspaper_ocr_native.dist\ArabicNewspaperOCR.exe`

To create the installer afterward, open `installer_script.iss` with Inno Setup and
choose **Build → Compile**. Tesseract and its language data remain separate system
dependencies.

## Roadmap

- Publish CER/WER results on a licensed Arabic historical newspaper dataset.
- Integrate the optional PaddleOCR high-recall pipeline into the desktop UI.
- Add searchable PDF and DOCX export from the main interface.
- Add configurable shortcut, tray mode, and automatic language detection.
- Package signed Windows releases with reproducible release notes.

## License

MIT © 2026 [Ali Alnazer Ahmed](https://github.com/pqun7). Contributions and issue
reports are welcome.
