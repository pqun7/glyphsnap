"""Headless UI smoke test with self-generated inputs."""

from pathlib import Path
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

from glyphsnap_app import OCRWindow


ROOT = Path(__file__).resolve().parent
REPORT_DIR = ROOT / "test-results"
IMAGE_PATH = REPORT_DIR / "ui_smoke_image.png"
PDF_PATH = REPORT_DIR / "ui_smoke.pdf"
REPORT_PATH = REPORT_DIR / "ui_smoke_report.txt"


def create_fixtures() -> None:
    REPORT_DIR.mkdir(exist_ok=True)
    image = Image.new("RGB", (900, 520), "white")
    ImageDraw.Draw(image).text((60, 80), "GlyphSnap - UI smoke test", fill="black")
    image.save(IMAGE_PATH)
    document = pymupdf.open()
    page = document.new_page(width=595, height=842)
    page.insert_text((72, 100), "GlyphSnap PDF smoke test")
    document.save(PDF_PATH)
    document.close()


def main() -> None:
    create_fixtures()
    app = QApplication.instance() or QApplication(sys.argv)
    window = OCRWindow()
    window.show()
    app.processEvents()

    window.load_file(PDF_PATH)
    app.processEvents()
    assert window.image is not None
    assert window.document is not None
    assert window.content_splitter.isVisible()
    assert window.page_input.text() == "1"

    window.load_file(IMAGE_PATH)
    app.processEvents()
    assert window.image is not None
    assert window.document is None
    assert window.editor.isVisible()
    assert window.region_editor.isVisible()
    assert window.language_box.count() >= 1
    assert window.preprocess_box.isChecked()
    window._navigate("history")
    app.processEvents()
    assert window.pages.currentIndex() == window.page_indices["history"]
    window._navigate("settings")
    app.processEvents()
    assert window.pages.currentIndex() == window.page_indices["settings"]
    window._navigate("help")
    app.processEvents()
    assert window.pages.currentIndex() == window.page_indices["help"]
    window._navigate("home")

    REPORT_PATH.write_text(
        "UI smoke test: PASS\n"
        "Generated PDF opened and rendered: PASS\n"
        "Generated image opened and rendered: PASS\n"
        "Language selector available: PASS\n"
        "Automatic preprocessing enabled: PASS\n"
        "History, Settings, and Help navigation: PASS\n",
        encoding="utf-8",
    )
    window.close()
    app.quit()
    print(f"UI_SMOKE_OK report={REPORT_PATH}")


if __name__ == "__main__":
    main()
