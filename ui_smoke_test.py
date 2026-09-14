from pathlib import Path
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PySide6.QtWidgets import QApplication

from arabic_newspaper_ocr_qt import OCRWindow


ROOT = Path(__file__).resolve().parent
PDF_PATH = ROOT / "5636-004.pdf"
REPORT_DIR = ROOT / "test-results"
IMAGE_PATH = REPORT_DIR / "ui_smoke_image.png"
REPORT_PATH = REPORT_DIR / "ui_smoke_report.txt"


def main() -> None:
    REPORT_DIR.mkdir(exist_ok=True)
    Image.new("RGB", (640, 420), "white").save(IMAGE_PATH)

    app = QApplication.instance() or QApplication(sys.argv)
    window = OCRWindow()
    window.show()
    app.processEvents()
    window.load_file(PDF_PATH)
    app.processEvents()
    assert window.image is not None
    assert window.content_splitter.isVisible()
    assert window.editor.isVisible()
    assert window.region_editor.isVisible()
    assert window.page_input.text() == "1"

    window.load_file(IMAGE_PATH)
    assert window.image is not None
    assert window.document is None
    assert window.editor.isVisible()
    assert window.region_editor.isVisible()

    REPORT_PATH.write_text(
        "UI smoke test: PASS\n"
        "PDF opened and rendered: PASS\n"
        "Main editable text field visible: PASS\n"
        "Region correction field visible: PASS\n"
        "Image opened and rendered: PASS\n"
        f"PDF: {PDF_PATH}\n"
        f"Image: {IMAGE_PATH}\n",
        encoding="utf-8",
    )
    window.close()
    app.quit()
    print(f"UI_SMOKE_OK report={REPORT_PATH}")


if __name__ == "__main__":
    main()
