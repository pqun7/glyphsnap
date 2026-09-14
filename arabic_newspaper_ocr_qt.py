"""Native Windows OCR studio with proper bidirectional Arabic text editing."""

from __future__ import annotations

import queue
import re
import shutil
import subprocess
import sys
import threading
import os
from pathlib import Path

# PyInstaller extracts the Qt runtime beside the executable.  Register those
# folders before importing PySide6 so Windows never resolves a mismatched Qt DLL
# from another installed application.
def application_dir() -> Path:
    if getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def application_icon() -> Path:
    return application_dir() / "assets" / "app.ico"


if getattr(sys, "frozen", False):
    _bundle_dir = application_dir()
    if _bundle_dir.is_dir():
        os.add_dll_directory(str(_bundle_dir))
    _qt_dir = _bundle_dir / "PySide6"
    if _qt_dir.is_dir():
        os.add_dll_directory(str(_qt_dir))

import pymupdf
import pytesseract
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QImage, QKeySequence, QPen, QPixmap, QShortcut, QTextOption
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QGraphicsRectItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QSplitter,
    QVBoxLayout, QWidget, QLineEdit,
)


TITLE = "استخراج النص من الصحف العربية"
BLUE = "#1769c2"

# Qt Style Sheet: applied once to the whole application in main().
APP_STYLE = f"""
    QMainWindow, QWidget {{ background: #f3f6fa; color: #17243b; font: 10pt 'Segoe UI'; }}
    QLabel#title {{ font: bold 17pt 'Segoe UI'; }}
    QLabel#muted {{ color: #52647b; }}
    QFrame#panel {{ background: white; border: 1px solid #d8e1ed; border-radius: 8px; }}
    QFrame#regionPanel {{ background: #edf4fc; border: 1px solid #ccdef3; border-radius: 8px; }}
    QPlainTextEdit {{ background: white; border: 1px solid #c9d5e3; border-radius: 5px;
                      padding: 9px; selection-background-color: #b7d8fa; selection-color: #17243b; }}
    QLabel#emptyState {{ background: white; border: 2px dashed #b8cce2; border-radius: 12px;
                         color: #52647b; font: 13pt 'Segoe UI'; padding: 42px; }}
    QPushButton {{ background: white; border: 1px solid #c5d2e2; border-radius: 5px; padding: 7px 12px; }}
    QPushButton:hover {{ background: #e8f2fc; }}
    QPushButton:disabled {{ color: #8a99aa; background: #edf1f5; }}
    QPushButton#primary {{ background: {BLUE}; color: white; border-color: {BLUE}; }}
    QPushButton#primary:hover {{ background: #0d519a; }}
    QSplitter::handle {{ background: #cad8e8; }}
"""


def locate_tesseract() -> str | None:
    for candidate in (
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        shutil.which("tesseract"),
    ):
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def check_tesseract(cmd: str | None) -> bool:
    if not cmd:
        return False
    try:
        result = subprocess.run([cmd, "--list-langs"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "ara" in result.stdout.splitlines()


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([،؛؟,:.!])", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def render_pdf_page(page: pymupdf.Page, dpi: int) -> Image.Image:
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def crop_image(image: Image.Image, box: tuple[int, int, int, int]) -> Image.Image:
    x1, y1, x2, y2 = box
    x1, x2 = sorted((max(0, min(image.width, x1)), max(0, min(image.width, x2))))
    y1, y2 = sorted((max(0, min(image.height, y1)), max(0, min(image.height, y2))))
    if x2 - x1 < 8 or y2 - y1 < 8:
        raise ValueError("حدد مساحة أوضح حول النص.")
    return image.crop((x1, y1, x2, y2))


def recognize(image: Image.Image, cmd: str, psm: int) -> str:
    pytesseract.pytesseract.tesseract_cmd = cmd
    return clean_text(pytesseract.image_to_string(
        image.convert("RGB"), lang="ara", config=f"--oem 1 --psm {psm}"))


class ArabicEditor(QPlainTextEdit):
    """Qt's bidi text engine keeps logical Arabic order while editing."""

    def __init__(self, font_size: int, parent=None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        option = self.document().defaultTextOption()
        option.setTextDirection(Qt.LayoutDirection.RightToLeft)
        option.setAlignment(Qt.AlignmentFlag.AlignRight)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        font = QFont("Segoe UI", font_size)
        self.setFont(font)
        self.setTabChangesFocus(False)
        self.setPlaceholderText("النص يظهر هنا بعد الاستخراج. يمكنك تحديده وتعديله مباشرة.")


class ImageView(QGraphicsView):
    def __init__(self, on_selection, parent=None) -> None:
        super().__init__(parent)
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.setBackgroundBrush(QBrush(QColor("#e7edf5")))
        self.setRenderHints(self.renderHints())
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.on_selection = on_selection
        self.image: Image.Image | None = None
        self.pixmap_item = None
        self.rect_item: QGraphicsRectItem | None = None
        self.handle_items: dict[str, QGraphicsRectItem] = {}
        self.overlay_items: list[QGraphicsRectItem] = []
        self.start_point: QPointF | None = None
        self.selection: tuple[int, int, int, int] | None = None
        self.selection_mode = False
        self.drag_mode_name: str | None = None
        self.drag_origin: QPointF | None = None
        self.drag_rect: QRectF | None = None
        self.previous_selection: tuple[int, int, int, int] | None = None
        self.fit_mode = True

    def set_image(self, image: Image.Image) -> None:
        self.image = image
        self.scene_obj.clear()
        self.rect_item = None
        self.handle_items.clear()
        self.overlay_items.clear()
        self.selection = None
        self.selection_mode = False
        rgb = image.convert("RGB")
        qimage = QImage(rgb.tobytes(), rgb.width, rgb.height,
                        rgb.width * 3, QImage.Format.Format_RGB888).copy()
        self.pixmap_item = self.scene_obj.addPixmap(QPixmap.fromImage(qimage))
        self.scene_obj.setSceneRect(QRectF(0, 0, rgb.width, rgb.height))
        self.fit_width()
        self.horizontalScrollBar().setValue(0)
        self.verticalScrollBar().setValue(0)
        self.on_selection(None)

    def fit_width(self) -> None:
        if not self.image:
            return
        self.resetTransform()
        scale = max(0.03, min(1.0, (self.viewport().width() - 18) / self.image.width))
        self.scale(scale, scale)
        self.fit_mode = True

    def zoom(self, factor: float) -> None:
        if not self.image:
            return
        current = self.transform().m11()
        target = max(0.04, min(3.0, current * factor))
        self.scale(target / current, target / current)
        self.fit_mode = False
        self._refresh_selection_items()

    def zoom_percent(self) -> int:
        return round(self.transform().m11() * 100)

    def start_selection(self) -> None:
        self.selection_mode = True
        self.setCursor(Qt.CursorShape.CrossCursor)

    def _handle_positions(self, rect: QRectF) -> dict[str, QPointF]:
        cx = (rect.left() + rect.right()) / 2
        cy = (rect.top() + rect.bottom()) / 2
        return {
            "nw": QPointF(rect.left(), rect.top()), "n": QPointF(cx, rect.top()),
            "ne": QPointF(rect.right(), rect.top()), "w": QPointF(rect.left(), cy),
            "e": QPointF(rect.right(), cy), "sw": QPointF(rect.left(), rect.bottom()),
            "s": QPointF(cx, rect.bottom()), "se": QPointF(rect.right(), rect.bottom()),
        }

    def _refresh_selection_items(self) -> None:
        if not self.rect_item or not self.selection:
            return
        rect = QRectF(
            self.selection[0], self.selection[1],
            self.selection[2] - self.selection[0], self.selection[3] - self.selection[1],
        )
        self.rect_item.setRect(rect)
        for name, item in self.handle_items.items():
            point = self._handle_positions(rect)[name]
            item.setRect(QRectF(point.x() - 5, point.y() - 5, 10, 10))
        self._refresh_overlay(rect)

    def _refresh_overlay(self, rect: QRectF) -> None:
        if not self.image:
            return
        bounds = QRectF(0, 0, self.image.width, self.image.height)
        regions = [
            QRectF(bounds.left(), bounds.top(), bounds.width(), max(0, rect.top() - bounds.top())),
            QRectF(bounds.left(), rect.bottom(), bounds.width(), max(0, bounds.bottom() - rect.bottom())),
            QRectF(bounds.left(), rect.top(), max(0, rect.left() - bounds.left()), rect.height()),
            QRectF(rect.right(), rect.top(), max(0, bounds.right() - rect.right()), rect.height()),
        ]
        if not self.overlay_items:
            for region in regions:
                item = self.scene_obj.addRect(
                    region, QPen(Qt.PenStyle.NoPen), QBrush(QColor(15, 35, 60, 55))
                )
                item.setZValue(1)
                self.overlay_items.append(item)
        for item, region in zip(self.overlay_items, regions):
            item.setRect(region)

    def _set_selection(self, rect: QRectF) -> None:
        if not self.image:
            return
        rect = rect.normalized()
        rect.setLeft(max(0, min(self.image.width - 8, rect.left())))
        rect.setTop(max(0, min(self.image.height - 8, rect.top())))
        rect.setRight(max(rect.left() + 8, min(self.image.width, rect.right())))
        rect.setBottom(max(rect.top() + 8, min(self.image.height, rect.bottom())))
        self.selection = (round(rect.left()), round(rect.top()), round(rect.right()), round(rect.bottom()))
        if not self.rect_item:
            pen = QPen(QColor("#1769c2"), 2)
            pen.setCosmetic(True)
            self.rect_item = self.scene_obj.addRect(rect, pen)
            self.rect_item.setZValue(2)
        if not self.handle_items:
            for name in self._handle_positions(rect):
                handle = self.scene_obj.addRect(QRectF(0, 0, 10, 10),
                                                QPen(QColor("#ffffff"), 1),
                                                QBrush(QColor("#1769c2")))
                handle.setZValue(3)
                self.handle_items[name] = handle
        self._refresh_selection_items()
        self.on_selection(self.selection)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.fit_mode and self.image:
            self.fit_width()

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.zoom(1.2 if event.angleDelta().y() > 0 else 1 / 1.2)
            event.accept()
        else:
            super().wheelEvent(event)

    def _bounded(self, point: QPointF) -> QPointF:
        if not self.image:
            return point
        return QPointF(max(0, min(self.image.width, point.x())),
                       max(0, min(self.image.height, point.y())))

    def _hit_target(self, point: QPointF) -> str | None:
        if not self.selection:
            return None
        rect = QRectF(
            self.selection[0], self.selection[1],
            self.selection[2] - self.selection[0], self.selection[3] - self.selection[1],
        )
        for name, handle in self._handle_positions(rect).items():
            if QRectF(handle.x() - 10, handle.y() - 10, 20, 20).contains(point):
                return name
        if rect.contains(point):
            return "move"
        return None

    def mousePressEvent(self, event) -> None:
        point = self._bounded(self.mapToScene(event.position().toPoint()))
        if event.button() == Qt.MouseButton.LeftButton and self.image:
            hit = self._hit_target(point) if self.selection else None
            if hit and not self.selection_mode:
                self.drag_mode_name = hit
                self.drag_origin = point
                self.drag_rect = QRectF(
                    self.selection[0], self.selection[1],
                    self.selection[2] - self.selection[0], self.selection[3] - self.selection[1],
                )
            else:
                self.start_point = point
                self.previous_selection = self.selection
                self.selection_mode = False
                self.setCursor(Qt.CursorShape.ArrowCursor)
                if not self.rect_item:
                    pen = QPen(QColor("#1769c2"), 2)
                    pen.setCosmetic(True)
                    self.rect_item = self.scene_obj.addRect(QRectF(point, point), pen)
                    self.rect_item.setZValue(2)
                else:
                    self.rect_item.setRect(QRectF(point, point))
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        point = self._bounded(self.mapToScene(event.position().toPoint()))
        if self.drag_mode_name and self.drag_origin and self.drag_rect:
            dx = point.x() - self.drag_origin.x()
            dy = point.y() - self.drag_origin.y()
            rect = QRectF(self.drag_rect)
            if self.drag_mode_name == "move":
                rect.translate(dx, dy)
            else:
                if "w" in self.drag_mode_name:
                    rect.setLeft(self.drag_rect.left() + dx)
                if "e" in self.drag_mode_name:
                    rect.setRight(self.drag_rect.right() + dx)
                if "n" in self.drag_mode_name:
                    rect.setTop(self.drag_rect.top() + dy)
                if "s" in self.drag_mode_name:
                    rect.setBottom(self.drag_rect.bottom() + dy)
            self._set_selection(rect)
            event.accept()
            return
        if self.start_point is None and self.image:
            hit = self._hit_target(point)
            if hit in ("n", "s"):
                self.setCursor(Qt.CursorShape.SizeVerCursor)
            elif hit in ("e", "w"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif hit in ("nw", "se"):
                self.setCursor(Qt.CursorShape.SizeFDiagCursor)
            elif hit in ("ne", "sw"):
                self.setCursor(Qt.CursorShape.SizeBDiagCursor)
            elif hit == "move":
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor if self.selection_mode else Qt.CursorShape.ArrowCursor)
        if self.start_point is not None and self.rect_item:
            self.rect_item.setRect(QRectF(self.start_point, point).normalized())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.drag_mode_name:
            self.drag_mode_name = None
            self.drag_origin = None
            self.drag_rect = None
            event.accept()
            return
        if self.start_point is not None and self.image:
            point = self._bounded(self.mapToScene(event.position().toPoint()))
            rect = QRectF(self.start_point, point).normalized()
            self.start_point = None
            box = (round(rect.left()), round(rect.top()), round(rect.right()), round(rect.bottom()))
            try:
                crop_image(self.image, box)
            except ValueError:
                if self.previous_selection:
                    old = self.previous_selection
                    self._set_selection(QRectF(
                        old[0], old[1], old[2] - old[0], old[3] - old[1]
                    ))
                else:
                    self.clear_selection()
            else:
                self._set_selection(rect)
            self.previous_selection = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def clear_selection(self) -> None:
        self.selection = None
        self.start_point = None
        self.selection_mode = False
        self.drag_mode_name = None
        self.setCursor(Qt.CursorShape.ArrowCursor)
        if self.rect_item:
            self.scene_obj.removeItem(self.rect_item)
            self.rect_item = None
        for item in self.handle_items.values():
            self.scene_obj.removeItem(item)
        self.handle_items.clear()
        for item in self.overlay_items:
            self.scene_obj.removeItem(item)
        self.overlay_items.clear()
        self.on_selection(None)

    def keyPressEvent(self, event) -> None:
        if self.selection and event.key() in (
            Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down
        ):
            step = 10 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 2
            dx = (-step if event.key() == Qt.Key.Key_Left else step if event.key() == Qt.Key.Key_Right else 0)
            dy = (-step if event.key() == Qt.Key.Key_Up else step if event.key() == Qt.Key.Key_Down else 0)
            rect = QRectF(
                self.selection[0], self.selection[1],
                self.selection[2] - self.selection[0], self.selection[3] - self.selection[1],
            )
            self._set_selection(rect.translated(dx, dy))
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape and self.selection:
            self.clear_selection()
            event.accept()
            return
        super().keyPressEvent(event)


class OCRWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(TITLE)
        self.resize(1320, 840)
        self.setMinimumSize(980, 620)
        self.path: Path | None = None
        self.document: pymupdf.Document | None = None
        self.page_index = 0
        self.image: Image.Image | None = None
        self.text_by_page: dict[int, str] = {}
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.cancel_event: threading.Event | None = None
        self.setAcceptDrops(True)
        self._build_ui()
        self.open_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.open_shortcut.activated.connect(self.open_file)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_text)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_events)
        self.timer.start(100)
        self._update_buttons()

    def _button(self, label: str, action, primary=False) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(action)
        if primary:
            button.setObjectName("primary")
        return button

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(16, 14, 16, 12)
        outer.setSpacing(9)
        title = QLabel(TITLE)
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addWidget(title)
        subtitle = QLabel("افتح الصحيفة، اختر الصفحة، شغّل OCR، ثم راجع النص وصدّره.")
        subtitle.setObjectName("muted")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignRight)
        outer.addWidget(subtitle)

        self.empty_state = QLabel(
            "اسحب ملف PDF أو صورة هنا\n\n"
            "حوّل الصحف المصوّرة إلى نص عربي قابل للتحرير\n"
            "PDF • PNG • JPG • WEBP • BMP • TIFF"
        )
        self.empty_state.setObjectName("emptyState")
        self.empty_state.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(self.empty_state, 1)

        file_row = QHBoxLayout()
        self.open_btn = self._button("فتح PDF أو صورة", self.open_file, True)
        file_row.addWidget(self.open_btn)
        self.file_label = QLabel("لم يُفتح ملف بعد")
        file_row.addWidget(self.file_label, 1)
        file_row.addWidget(QLabel("دقة PDF"))
        self.dpi_box = QComboBox()
        self.dpi_box.addItems(["180", "240", "300", "360", "420"])
        self.dpi_box.setCurrentText("240")
        self.dpi_box.currentTextChanged.connect(self._dpi_changed)
        file_row.addWidget(self.dpi_box)
        outer.addLayout(file_row)

        action_row = QHBoxLayout()
        self.prev_btn = self._button("السابق", lambda: self._go_page(-1))
        self.next_btn = self._button("التالي", lambda: self._go_page(1))
        self.page_input = QLineEdit()
        self.page_input.setFixedWidth(58)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.setPlaceholderText("صفحة")
        self.page_input.returnPressed.connect(self._jump_to_page)
        self.page_label = QLabel("من 0")
        self.full_btn = self._button("استخراج الصفحة / الصورة", self.extract_current, True)
        self.all_btn = self._button("استخراج PDF كامل", self.extract_all)
        self.cancel_btn = self._button("إلغاء الاستخراج", self.cancel_ocr)
        self.select_btn = self._button("تحديد منطقة", self.image_view_start_selection)
        self.crop_btn = self._button("استخراج الجزء المحدد", self.extract_selection)
        self.clear_btn = self._button("إلغاء التحديد", self.image_view_clear)
        for widget in (self.prev_btn, self.page_input, self.next_btn, self.page_label, self.full_btn,
                       self.select_btn,
                       self.all_btn, self.cancel_btn, self.crop_btn, self.clear_btn):
            action_row.addWidget(widget)
        action_row.addStretch(1)
        outer.addLayout(action_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter = splitter
        outer.addWidget(splitter, 1)
        image_panel = QFrame()
        image_panel.setObjectName("panel")
        image_layout = QVBoxLayout(image_panel)
        image_head = QHBoxLayout()
        self.fit_btn = self._button("ملاءمة", lambda: self.image_view.fit_width())
        self.minus_btn = self._button("−", lambda: self.image_view.zoom(1 / 1.2))
        self.plus_btn = self._button("+", lambda: self.image_view.zoom(1.2))
        image_head.addWidget(self.fit_btn)
        image_head.addWidget(self.minus_btn)
        image_head.addWidget(self.plus_btn)
        self.zoom_label = QLabel("100%")
        image_head.addWidget(self.zoom_label)
        image_head.addStretch(1)
        image_head.addWidget(QLabel("معاينة الصفحة / الصورة"))
        image_layout.addLayout(image_head)
        self.image_view = ImageView(self._selection_changed)
        image_layout.addWidget(self.image_view, 1)
        splitter.addWidget(image_panel)

        right = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter = right
        splitter.addWidget(right)
        main_panel = QFrame()
        main_panel.setObjectName("panel")
        main_layout = QVBoxLayout(main_panel)
        main_head = QHBoxLayout()
        main_head.addWidget(self._button("نسخ", lambda: self.copy_field(self.editor)))
        main_head.addStretch(1)
        main_head.addWidget(QLabel("النص المستخرج — عدّله مباشرة"))
        main_layout.addLayout(main_head)
        self.editor = ArabicEditor(12)
        self.editor.textChanged.connect(self._mark_dirty)
        main_layout.addWidget(self.editor, 1)
        right.addWidget(main_panel)

        region_panel = QFrame()
        region_panel.setObjectName("regionPanel")
        region_layout = QVBoxLayout(region_panel)
        region_head = QHBoxLayout()
        region_head.addWidget(self._button("نسخ", lambda: self.copy_field(self.region_editor)))
        region_head.addStretch(1)
        region_head.addWidget(QLabel("نتيجة الجزء المحدد — اسحب الفاصل لتغيير الحجم"))
        region_layout.addLayout(region_head)
        self.region_editor = ArabicEditor(11)
        region_layout.addWidget(self.region_editor, 1)
        hint = QLabel("حدد منطقة من الصورة، راجع النتيجة، ثم استبدل التحديد أو أدرج النص عند المؤشر.")
        hint.setObjectName("muted")
        hint.setAlignment(Qt.AlignmentFlag.AlignRight)
        region_layout.addWidget(hint)
        self.replace_btn = self._button("استبدال التحديد", self.replace_text)
        region_layout.addWidget(self.replace_btn, alignment=Qt.AlignmentFlag.AlignRight)
        right.addWidget(region_panel)
        right.setSizes([430, 230])
        splitter.setSizes([620, 650])

        footer = QHBoxLayout()
        self.progress = QProgressBar()
        self.progress.setFixedWidth(120)
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        footer.addWidget(self.progress)
        self.status_label = QLabel("جاهز — عجلة الماوس لتمرير الصورة، وCtrl+العجلة لتكبيرها.")
        self.status_label.setObjectName("muted")
        footer.addWidget(self.status_label, 1)
        self.save_btn = self._button("تصدير النص TXT", self.save_text, True)
        footer.addWidget(self.save_btn)
        outer.addLayout(footer)
        self.empty_state.setVisible(True)
        self.content_splitter.setVisible(False)
        self.cancel_btn.setVisible(False)

    def _status(self, text: str) -> None:
        self.status_label.setText(text)

    def _mark_dirty(self) -> None:
        if self.path is not None and not self.busy:
            self.file_label.setText(f"{self.path.name}  •  غير محفوظ")

    def image_view_start_selection(self) -> None:
        self.image_view.start_selection()
        self._status("اسحب فوق الصورة لتحديد منطقة OCR.")

    def _jump_to_page(self) -> None:
        if self.document is None:
            return
        try:
            target = int(self.page_input.text()) - 1
        except ValueError:
            self._status("أدخل رقم صفحة صحيحًا.")
            return
        if 0 <= target < len(self.document):
            self._go_page(target - self.page_index)
        else:
            self._status(f"رقم الصفحة يجب أن يكون بين 1 و{len(self.document)}.")

    def dragEnterEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if urls and Path(urls[0].toLocalFile()).suffix.lower() in {
            ".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"
        }:
            event.acceptProposedAction()
            self._status("أفلت الملف لفتحه.")
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        urls = event.mimeData().urls()
        if not urls:
            event.ignore()
            return
        path = Path(urls[0].toLocalFile())
        if path.suffix.lower() not in {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}:
            self._status("صيغة الملف غير مدعومة.")
            event.ignore()
            return
        self.load_file(path)
        event.acceptProposedAction()

    def _selection_changed(self, box) -> None:
        self.zoom_label.setText(f"{self.image_view.zoom_percent()}%")
        self._update_buttons()
        if box:
            self._status(f"المساحة المحددة: {box[2]-box[0]} × {box[3]-box[1]} بكسل. اضغط استخراج الجزء المحدد.")

    def _update_buttons(self) -> None:
        has_image = self.image is not None
        count = len(self.document) if self.document is not None else (1 if has_image else 0)
        self.page_label.setText(f"من {count}" if count else "من 0")
        if count and self.page_input.text() != str(self.page_index + 1):
            self.page_input.setText(str(self.page_index + 1))
        self.open_btn.setEnabled(not self.busy)
        self.dpi_box.setEnabled(not self.busy)
        self.prev_btn.setEnabled(bool(self.document and self.page_index > 0 and not self.busy))
        self.next_btn.setEnabled(bool(self.document and self.page_index+1 < count and not self.busy))
        self.full_btn.setEnabled(has_image and not self.busy)
        self.select_btn.setEnabled(has_image and not self.busy)
        self.all_btn.setEnabled(self.document is not None and not self.busy)
        self.cancel_btn.setVisible(self.busy)
        self.cancel_btn.setEnabled(self.busy)
        self.crop_btn.setEnabled(bool(self.image_view.selection and not self.busy))
        self.clear_btn.setEnabled(bool(self.image_view.selection and not self.busy))
        self.save_btn.setEnabled(self.path is not None and not self.busy)

    def open_file(self) -> None:
        if self.busy:
            return
        filename, _ = QFileDialog.getOpenFileName(
            self, "اختر صحيفة أو صورة", "",
            "PDF والصور (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;كل الملفات (*)")
        if filename:
            self.load_file(Path(filename))

    def load_file(self, path: Path) -> None:
        try:
            if path.suffix.lower() == ".pdf":
                document = pymupdf.open(path)
                if document.needs_pass or len(document) == 0:
                    document.close()
                    raise ValueError("ملف PDF محمي بكلمة مرور أو فارغ.")
                image = render_pdf_page(document[0], int(self.dpi_box.currentText()))
            else:
                document = None
                with Image.open(path) as opened:
                    image = opened.convert("RGB")
        except Exception as exc:
            QMessageBox.critical(self, "تعذر فتح الملف", str(exc))
            return
        if self.document is not None:
            self.document.close()
        self.document = document
        self.path = path
        self.page_index = 0
        self.text_by_page.clear()
        self.editor.setPlainText("")
        self.region_editor.setPlainText("")
        self.image = image
        self.image_view.set_image(image)
        self.file_label.setText(path.name)
        self.empty_state.setVisible(False)
        self.content_splitter.setVisible(True)
        self.page_input.setText("1")
        self._update_buttons()
        self._status("تم فتح الملف. استخرج الصفحة كاملة أو حدد جزءًا بالفأرة.")

    def _remember_page(self) -> None:
        if self.path:
            self.text_by_page[self.page_index] = self.editor.toPlainText()

    def _go_page(self, direction: int) -> None:
        if self.document is None or self.busy:
            return
        target = self.page_index + direction
        if not 0 <= target < len(self.document):
            return
        self._remember_page()
        try:
            image = render_pdf_page(self.document[target], int(self.dpi_box.currentText()))
        except Exception as exc:
            QMessageBox.critical(self, "تعذر عرض الصفحة", str(exc))
            return
        self.page_index = target
        self.image = image
        self.image_view.set_image(image)
        self.editor.setPlainText(self.text_by_page.get(target, ""))
        self.region_editor.setPlainText("")
        self._update_buttons()

    def _dpi_changed(self, _value: str) -> None:
        if self.document is None or self.busy:
            return
        self._remember_page()
        try:
            image = render_pdf_page(self.document[self.page_index], int(self.dpi_box.currentText()))
            self.image = image
            self.image_view.set_image(image)
        except Exception as exc:
            QMessageBox.critical(self, "تعذر تغيير الدقة", str(exc))

    def image_view_clear(self) -> None:
        self.image_view.clear_selection()

    def _tesseract(self) -> str | None:
        cmd = locate_tesseract()
        if not check_tesseract(cmd):
            QMessageBox.critical(self, "Tesseract غير جاهز",
                                 "يلزم Tesseract مع لغة ara في C:\\Program Files\\Tesseract-OCR.")
            return None
        return cmd

    def _start_worker(self, job, kind: str) -> None:
        self.busy = True
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self.progress.setRange(0, 0)
        self._status("يجري استخراج النص…")
        self._update_buttons()

        def run():
            try:
                result = job(cancel_event)
                self.events.put(("cancelled" if cancel_event.is_set() else kind, result))
            except Exception as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=run, daemon=True).start()

    def cancel_ocr(self) -> None:
        if self.cancel_event is not None:
            self.cancel_event.set()
            self._status("جارٍ إيقاف الاستخراج بعد إكمال العملية الحالية…")

    def extract_current(self) -> None:
        if self.image is None or self.busy:
            return
        cmd = self._tesseract()
        if cmd:
            image = self.image.copy()
            index = self.page_index
            self._start_worker(lambda _cancel: (index, recognize(image, cmd, 3)), "full")

    def extract_selection(self) -> None:
        if self.image is None or self.image_view.selection is None or self.busy:
            return
        cmd = self._tesseract()
        if cmd:
            image = crop_image(self.image, self.image_view.selection)
            self._start_worker(lambda _cancel: recognize(image, cmd, 6), "region")

    def extract_all(self) -> None:
        if self.document is None or self.path is None or self.busy:
            return
        cmd = self._tesseract()
        if not cmd:
            return
        self._remember_page()
        path = self.path
        dpi = int(self.dpi_box.currentText())

        def job(cancel_event):
            texts = {}
            with pymupdf.open(path) as doc:
                for index in range(len(doc)):
                    if cancel_event.is_set():
                        break
                    page = doc[index]
                    texts[index] = recognize(render_pdf_page(page, dpi), cmd, 3)
                    self.events.put(("progress", f"اكتملت الصفحة {index+1} من {len(doc)}"))
            return texts

        self._start_worker(job, "all")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    self._status(payload)
                    continue
                self.busy = False
                self.cancel_event = None
                self.progress.setRange(0, 1)
                self.progress.setValue(0)
                if kind == "error":
                    QMessageBox.critical(self, "فشل استخراج النص", payload)
                    self._status("تعذر الاستخراج. تحقق من الملف وTesseract.")
                elif kind == "cancelled":
                    if isinstance(payload, dict):
                        self.text_by_page.update(payload)
                    self.editor.setPlainText(self.text_by_page.get(self.page_index, ""))
                    self._status("تم إلغاء الاستخراج مع الاحتفاظ بالنتائج المكتملة.")
                elif kind == "full":
                    index, text = payload
                    self.text_by_page[index] = text
                    if self.page_index == index:
                        self.editor.setPlainText(text)
                    self._status(f"اكتمل استخراج الصفحة. عدد الأحرف: {len(text)}")
                elif kind == "region":
                    self.region_editor.setPlainText(payload)
                    self._fit_region_result(payload)
                    self._status("اكتمل استخراج الجزء المحدد. راجع النتيجة ثم استبدل النص الخاطئ.")
                elif kind == "all":
                    self.text_by_page.update(payload)
                    self.editor.setPlainText(self.text_by_page.get(self.page_index, ""))
                    self._status(f"اكتمل استخراج {len(payload)} صفحة.")
                self._update_buttons()
        except queue.Empty:
            pass

    def _fit_region_result(self, text: str) -> None:
        splitter = self.right_splitter
        available = splitter.height()
        lines = sum(max(1, (len(line) + 54) // 55) for line in text.splitlines())
        desired = min(int(available * 0.55), max(170, min(380, 115 + min(lines, 12) * 23)))
        splitter.setSizes([max(180, available - desired), desired])

    def replace_text(self) -> None:
        replacement = self.region_editor.toPlainText().strip()
        if not replacement:
            QMessageBox.information(self, "لا توجد نتيجة", "استخرج الجزء المحدد أولًا أو اكتب النص المصحح.")
            return
        cursor = self.editor.textCursor()
        cursor.beginEditBlock()
        cursor.insertText(replacement)
        cursor.endEditBlock()
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()
        self._remember_page()
        self._status("أُدرج النص المصحح. راجع موضعه قبل التصدير.")

    def copy_field(self, editor: QPlainTextEdit) -> None:
        text = editor.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self._status("نُسخ محتوى الحقل كاملًا.")
        else:
            self._status("الحقل فارغ؛ لا يوجد نص لنسخه.")

    def save_text(self) -> None:
        if self.path is None:
            return
        self._remember_page()
        filename, _ = QFileDialog.getSaveFileName(
            self, "تصدير النص", f"{self.path.stem}_OCR.txt", "ملف نصي (*.txt)")
        if not filename:
            return
        if self.document is None:
            content = self.text_by_page.get(0, "")
        else:
            content = "\n\n".join(
                f"=== الصفحة {index+1} ===\n{self.text_by_page.get(index, '')}"
                for index in range(len(self.document)))
        try:
            Path(filename).write_text(content, encoding="utf-8-sig")
        except Exception as exc:
            QMessageBox.critical(self, "تعذر حفظ الملف", str(exc))
            return
        self._status(f"حُفظ النص في {filename}")

    def closeEvent(self, event) -> None:
        if self.document is not None:
            self.document.close()
        super().closeEvent(event)


def run_smoke_test(pdf_path: Path, output_dir: Path) -> None:
    app = QApplication.instance() or QApplication(["ArabicNewspaperOCR", "-platform", "offscreen"])
    pdf_path = pdf_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with pymupdf.open(str(pdf_path)) as document:
        if len(document) == 0:
            raise RuntimeError("PDF contains no pages.")
        image = render_pdf_page(document[0], 120)

    tesseract_cmd = locate_tesseract()
    if not check_tesseract(tesseract_cmd):
        raise RuntimeError("Tesseract with the Arabic 'ara' language is required.")
    text = recognize(image, tesseract_cmd, 3)
    region = crop_image(image, (0, 0, max(8, image.width // 2), max(8, image.height // 2)))
    region_text = recognize(region, tesseract_cmd, 6)
    export_path = output_dir / f"{pdf_path.stem}_smoke.txt"
    export_path.write_text(text + "\n\n[REGION]\n" + region_text, encoding="utf-8")
    if not export_path.is_file():
        raise RuntimeError("Smoke-test TXT export failed.")
    print(f"SMOKE_TEST_OK PDF={pdf_path} OUTPUT={export_path}")
    app.quit()


def main() -> None:
    if len(sys.argv) >= 3 and sys.argv[1] == "--smoke-test":
        run_smoke_test(Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) >= 4 else Path.cwd() / "smoke-output")
        return
    app = QApplication(sys.argv)
    icon_path = application_icon()
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
    app.setStyleSheet(APP_STYLE)
    window = OCRWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
