"""GlyphSnap native Windows OCR application."""

from __future__ import annotations

import queue
import sys
import threading
import os
import ctypes
import time
from datetime import datetime
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


def asset_icon(name: str) -> Path:
    return application_dir() / "assets" / "icons" / f"{name}.png"


def logo_path() -> Path:
    return asset_icon("glyphsnap_logo")


def configure_windows_identity() -> None:
    """Give Windows a stable taskbar identity for icon and shortcut grouping."""
    if sys.platform == "win32":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "GlyphSnap.DesktopOCR.2"
            )
        except (AttributeError, OSError):
            pass


if getattr(sys, "frozen", False):
    _bundle_dir = application_dir()
    if _bundle_dir.is_dir():
        os.add_dll_directory(str(_bundle_dir))
    _qt_dir = _bundle_dir / "PySide6"
    if _qt_dir.is_dir():
        os.add_dll_directory(str(_qt_dir))

import pymupdf
from PIL import Image
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QImage, QKeySequence, QPen, QPixmap,
    QShortcut, QTextCharFormat, QTextListFormat, QTextOption,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGraphicsEllipseItem,
    QGraphicsRectItem,
    QGraphicsScene, QGraphicsView, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QProgressBar, QPushButton, QScrollArea,
    QSplitter, QStackedWidget, QTextEdit, QVBoxLayout, QWidget, QLineEdit,
)

from ocr_engine import (
    LANGUAGE_LABELS,
    OCRResult,
    installed_languages,
    locate_tesseract,
    recognize as run_ocr,
)
from screen_capture import GlobalHotkey, ScreenRegionSelector, capture_virtual_desktop
from glyphsnap_storage import GlyphSnapSettings, HistoryRecord, HistoryStore


TITLE = "GlyphSnap"
BLUE = "#0877F9"
REQUIRED_UI_ICONS = {
    "add_selection", "bold", "bullet_list", "clear", "copy", "dropdown",
    "extract_page", "extract_selection", "fit_view", "fit_width", "full_image",
    "glyphsnap_logo", "help", "history", "home", "info", "italic", "language",
    "next_page", "numbered_list", "ocr_badge", "open_image", "open_pdf",
    "previous_page", "processing_time", "redo", "reset_selection", "save_pdf",
    "save_txt", "screen_capture", "select_region", "selected_area", "settings",
    "success", "underline", "undo", "zoom_in", "zoom_out",
}

# Qt Style Sheet: applied once to the whole application in main().
APP_STYLE = f"""
    QMainWindow, QWidget {{ background: #F4F8FC; color: #081A3A; font: 10pt "Segoe UI"; }}
    QLabel {{ background: transparent; }}
    QWidget#sectionText {{ background: transparent; }}
    QLabel#title {{ color: #081A3A; font: 700 17pt "Segoe UI"; }}
    QLabel#subtitle, QLabel#muted, QLabel#helper {{ color: #536B8F; }}
    QLabel#sectionTitle {{ color: #081A3A; font: 700 11.5pt "Segoe UI"; }}
    QLabel#settingLabel {{ color: #304D77; font: 9.5pt "Segoe UI"; }}
    QLabel#panelTitle {{ color: #081A3A; font: 700 12pt "Segoe UI"; }}
    QLabel#pageTitle {{ color: #081A3A; font: 700 18pt "Segoe UI"; }}
    QLabel#smallMeta {{ color: #536B8F; font: 9pt "Segoe UI"; }}
    QLabel#blueBadge {{ color: #0877F9; background: #EAF3FF; border-radius: 11px; padding: 4px 10px; font: 9pt "Segoe UI"; }}
    QLabel#greenBadge {{ color: #11885B; background: #E8F8F1; border-radius: 11px; padding: 4px 10px; font: 600 9pt "Segoe UI"; }}
    QLabel#ocrBadge {{ color: white; background: #0877F9; border-radius: 5px; padding: 2px 6px; font: 700 8pt "Segoe UI"; }}
    QLabel#metricTitle {{ color: #536B8F; font: 9pt "Segoe UI"; }}
    QLabel#metricValue {{ color: #081A3A; font: 600 10pt "Segoe UI"; }}
    QLabel#shortcut {{ color: #4D78B7; font: 8pt "Segoe UI"; }}

    QFrame#appHeader {{ background: #FFFFFF; border-bottom: 1px solid #DEE8F2; }}
    QFrame#sidebar {{ background: #FFFFFF; border-right: 1px solid #DEE8F2; }}
    QFrame#surfaceCard, QFrame#panel, QFrame#sourceCard, QFrame#statusCard {{
        background: #FFFFFF; border: 1px solid #DEE8F2; border-radius: 12px;
    }}
    QFrame#pageCard, QFrame#historyCard {{ background: #FFFFFF; border: 1px solid #DEE8F2; border-radius: 12px; }}
    QFrame#toolbar, QFrame#regionToolbar {{ background: #F8FBFE; border: 1px solid #DEE8F2; border-radius: 9px; }}
    QFrame#separator {{ background: #DEE8F2; min-width: 1px; max-width: 1px; border: 0; }}
    QFrame#regionPanel {{ background: #F8FBFE; border: 1px solid #DEE8F2; border-radius: 9px; }}
    QFrame#hotkeyCard {{ background: #F8FBFE; border: 1px solid #DEE8F2; border-radius: 10px; }}
    QFrame#emptyState {{ background: #FFFFFF; border: 1px solid #DEE8F2; border-radius: 12px; }}

    QPushButton {{ min-height: 38px; background: #FFFFFF; color: #081A3A; border: 1px solid #CBD9E8;
        border-radius: 8px; padding: 0 13px; font: 600 9.5pt "Segoe UI"; }}
    QPushButton:hover {{ background: #F8FBFE; border-color: #9DB5D0; }}
    QPushButton:pressed {{ background: #EDF3F9; }}
    QPushButton:focus {{ border: 2px solid #72AEF8; }}
    QPushButton:disabled {{ color: #8A9BB2; background: #F0F3F7; border-color: #E3E9F0; }}
    QPushButton#primary {{ background: {BLUE}; color: #FFFFFF; border-color: {BLUE}; }}
    QPushButton#primary:hover {{ background: #066BE4; border-color: #066BE4; }}
    QPushButton#primary:pressed {{ background: #075DC3; border-color: #075DC3; }}
    QPushButton#subtle {{ background: #FFFFFF; border-color: #DEE8F2; }}
    QPushButton#ghost {{ background: transparent; border-color: transparent; }}
    QPushButton#ghost:hover {{ background: #EAF3FF; color: #0877F9; }}
    QPushButton#iconButton {{ min-width: 38px; max-width: 38px; padding: 0; background: #FFFFFF; border-color: #DEE8F2; font: 700 12pt "Segoe UI Symbol"; }}
    QPushButton#toolButton {{ min-width: 36px; max-width: 42px; min-height: 34px; max-height: 34px; padding: 0; border-radius: 6px; border-color: transparent; font: 600 11pt "Segoe UI"; }}
    QPushButton#toolButton:hover {{ background: #EAF3FF; border-color: #D4E7FF; }}
    QPushButton#danger {{ color: #E85050; background: #FFF3F3; border-color: #F6CACA; }}
    QPushButton#navItem {{ min-height: 56px; text-align: left; padding: 4px 12px; background: transparent; border: 0; border-radius: 10px; color: #17345F; font: 9.5pt "Segoe UI"; }}
    QPushButton#navItem:hover {{ background: #F2F7FD; }}
    QPushButton#navItem[selected="true"] {{ background: #EAF3FF; color: #0877F9; border-left: 3px solid #0877F9; font-weight: 600; }}
    QPushButton#sourceTile {{ min-height: 58px; text-align: left; padding: 7px 14px; background: #F8FBFE; border-color: #DEE8F2; }}
    QPushButton#sourceTile:hover {{ background: #F3F8FD; border-color: #AFC9E7; }}
    QPushButton#sourceTile[selected="true"] {{ background: #F0F7FF; color: #081A3A; border: 2px solid #0877F9; }}

    QComboBox, QLineEdit {{ min-height: 40px; background: #FFFFFF; color: #081A3A; border: 1px solid #CBD9E8; border-radius: 8px; padding: 0 10px; selection-background-color: #D8EAFE; }}
    QComboBox:hover, QLineEdit:hover {{ border-color: #9DB5D0; }}
    QComboBox:focus, QLineEdit:focus {{ border: 2px solid #72AEF8; }}
    QComboBox:disabled, QLineEdit:disabled {{ color: #8A9BB2; background: #F0F3F7; border-color: #E3E9F0; }}
    QComboBox::drop-down {{ border: 0; width: 28px; }}
    QComboBox::down-arrow {{
        image: url("{asset_icon('dropdown').as_posix()}"); width: 11px; height: 7px;
    }}
    QCheckBox {{ color: #304D77; spacing: 7px; background: transparent; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}

    QTextEdit, QPlainTextEdit {{ background: #FFFFFF; color: #081A3A; border: 1px solid #DEE8F2; border-radius: 9px; padding: 12px; selection-background-color: #CFE4FF; selection-color: #081A3A; }}
    QTextEdit:focus, QPlainTextEdit:focus {{ border: 2px solid #72AEF8; }}
    QGraphicsView {{ border: 0; border-radius: 9px; background: #E7EEF6; }}
    QProgressBar {{ min-height: 7px; max-height: 7px; background: #E6EDF4; border: 0; border-radius: 3px; text-align: center; }}
    QProgressBar::chunk {{ background: #18B879; border-radius: 3px; }}
    QSplitter::handle {{ background: #F4F8FC; width: 9px; height: 9px; }}
    QScrollArea {{ background: transparent; border: 0; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QStackedWidget {{ background: transparent; }}
    QToolTip {{ color: #FFFFFF; background: #081A3A; border: 1px solid #081A3A; padding: 6px 8px; font: 9pt "Segoe UI"; }}
"""


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


class OCRTextEditor(QTextEdit):
    """Bidirectional editor for Arabic, English, and mixed OCR output."""

    def __init__(self, font_size: int, parent=None) -> None:
        super().__init__(parent)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        option = self.document().defaultTextOption()
        option.setTextDirection(Qt.LayoutDirection.RightToLeft)
        option.setAlignment(Qt.AlignmentFlag.AlignRight)
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDefaultTextOption(option)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        font = QFont("Segoe UI", font_size)
        self.setFont(font)
        self.setTabChangesFocus(False)
        self.setPlaceholderText("النص يظهر هنا بعد الاستخراج. يمكنك تحديده وتعديله مباشرة.")

    def toggle_character_format(self, kind: str) -> None:
        cursor = self.textCursor()
        current = cursor.charFormat()
        fmt = QTextCharFormat()
        if kind == "bold":
            fmt.setFontWeight(QFont.Weight.Normal if current.fontWeight() >= QFont.Weight.Bold else QFont.Weight.Bold)
        elif kind == "italic":
            fmt.setFontItalic(not current.fontItalic())
        elif kind == "underline":
            fmt.setFontUnderline(not current.fontUnderline())
        cursor.mergeCharFormat(fmt)
        self.mergeCurrentCharFormat(fmt)

    def make_list(self, numbered: bool) -> None:
        cursor = self.textCursor()
        style = (
            QTextListFormat.Style.ListDecimal
            if numbered else QTextListFormat.Style.ListDisc
        )
        list_format = QTextListFormat()
        list_format.setStyle(style)
        cursor.createList(list_format)

    def set_ocr_direction(self, languages: list[str]) -> None:
        has_rtl = any(language in {"ara", "fas", "urd", "heb"} for language in languages)
        has_ltr = any(language in {"eng", "fra", "deu", "spa", "tur"} for language in languages)
        if has_rtl and has_ltr:
            direction = Qt.LayoutDirection.LayoutDirectionAuto
        elif has_rtl:
            direction = Qt.LayoutDirection.RightToLeft
        else:
            direction = Qt.LayoutDirection.LeftToRight
        self.setLayoutDirection(direction)
        option = self.document().defaultTextOption()
        option.setTextDirection(direction)
        option.setAlignment(Qt.AlignmentFlag.AlignLeading)
        self.document().setDefaultTextOption(option)


class ImageView(QGraphicsView):
    def __init__(self, on_selection, on_zoom=None, parent=None) -> None:
        super().__init__(parent)
        self.scene_obj = QGraphicsScene(self)
        self.setScene(self.scene_obj)
        self.setBackgroundBrush(QBrush(QColor("#e7edf5")))
        self.setRenderHints(self.renderHints())
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.on_selection = on_selection
        self.on_zoom = on_zoom
        self.image: Image.Image | None = None
        self.pixmap_item = None
        self.rect_item: QGraphicsRectItem | None = None
        self.handle_items: dict[str, QGraphicsEllipseItem] = {}
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
        if self.on_zoom:
            self.on_zoom(self.zoom_percent())

    def fit_view(self) -> None:
        if not self.image:
            return
        self.fitInView(self.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self.fit_mode = False
        self._refresh_selection_items()
        if self.on_zoom:
            self.on_zoom(self.zoom_percent())

    def set_zoom(self, percent: int) -> None:
        if not self.image:
            return
        target = max(4, min(300, percent)) / 100
        self.resetTransform()
        self.scale(target, target)
        self.fit_mode = False
        self._refresh_selection_items()
        if self.on_zoom:
            self.on_zoom(self.zoom_percent())

    def zoom(self, factor: float) -> None:
        if not self.image:
            return
        current = self.transform().m11()
        target = max(0.04, min(3.0, current * factor))
        self.scale(target / current, target / current)
        self.fit_mode = False
        self._refresh_selection_items()
        if self.on_zoom:
            self.on_zoom(self.zoom_percent())

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
                    region, QPen(Qt.PenStyle.NoPen), QBrush(QColor(15, 35, 60, 32))
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
            pen = QPen(QColor(BLUE), 2)
            pen.setCosmetic(True)
            self.rect_item = self.scene_obj.addRect(rect, pen)
            self.rect_item.setZValue(2)
        if not self.handle_items:
            for name in self._handle_positions(rect):
                handle = self.scene_obj.addEllipse(
                    QRectF(0, 0, 10, 10),
                    QPen(QColor("#ffffff"), 1),
                    QBrush(QColor(BLUE)),
                )
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
                    pen = QPen(QColor(BLUE), 2)
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
        icon_path = application_icon()
        if icon_path.is_file():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.resize(1400, 900)
        self.setMinimumSize(1100, 700)
        self.path: Path | None = None
        self.document: pymupdf.Document | None = None
        self.page_index = 0
        self.image: Image.Image | None = None
        self.text_by_page: dict[int, str] = {}
        self.events: queue.Queue = queue.Queue()
        self.busy = False
        self.cancel_event: threading.Event | None = None
        self.ocr_started_at: float | None = None
        self.last_processing_seconds = 0.0
        self.tesseract_command = locate_tesseract()
        self.available_languages = installed_languages(self.tesseract_command)
        self.preferences = GlyphSnapSettings()
        self.history_store = HistoryStore()
        self.screen_selector: ScreenRegionSelector | None = None
        self.setAcceptDrops(True)
        self._build_ui()
        self.open_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.open_shortcut.activated.connect(self.open_file)
        self.save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        self.save_shortcut.activated.connect(self.save_text)
        self.global_hotkey = GlobalHotkey(self.start_screen_capture, self)
        self.global_hotkey_registered = self.global_hotkey.register()
        self.capture_shortcut: QShortcut | None = None
        if not self.global_hotkey_registered:
            self.capture_shortcut = QShortcut(QKeySequence("Ctrl+Shift+O"), self)
            self.capture_shortcut.activated.connect(self.start_screen_capture)
        self.extract_selection_shortcut = QShortcut(QKeySequence("Ctrl+Return"), self)
        self.extract_selection_shortcut.activated.connect(self.extract_selection)
        self.extract_page_shortcut = QShortcut(QKeySequence("Alt+Return"), self)
        self.extract_page_shortcut.activated.connect(self.extract_current)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._poll_events)
        self.timer.start(100)
        self._update_buttons()
        if not self.global_hotkey_registered:
            self._status(
                "تعذر تسجيل Ctrl+Shift+O كاختصار عام؛ قد يكون مستخدمًا من تطبيق آخر. "
                f"يبقى زر التقاط الشاشة متاحًا. (Windows error {self.global_hotkey.error_code})"
            )

    def _button(
        self,
        label: str,
        action,
        primary: bool = False,
        tooltip: str | None = None,
        object_name: str | None = None,
    ) -> QPushButton:
        button = QPushButton(label)
        button.clicked.connect(action)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        if primary:
            button.setObjectName("primary")
        elif object_name:
            button.setObjectName(object_name)
        if tooltip:
            button.setToolTip(tooltip)
        return button

    @staticmethod
    def _set_icon(button: QPushButton, name: str, size: int = 18) -> QPushButton:
        path = asset_icon(name)
        if path.is_file():
            button.setIcon(QIcon(str(path)))
            button.setIconSize(QSize(size, size))
        return button

    @staticmethod
    def _icon_label(name: str, size: int) -> QLabel:
        return OCRWindow._image_label(name, size, size)

    @staticmethod
    def _image_label(name: str, width: int, height: int) -> QLabel:
        label = QLabel()
        path = asset_icon(name)
        if path.is_file():
            label.setPixmap(QPixmap(str(path)).scaled(
                width,
                height,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        label.setFixedSize(width, height)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    @staticmethod
    def _section_text(title: str, helper: str | None = None) -> QWidget:
        wrapper = QWidget()
        wrapper.setObjectName("sectionText")
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(title_label)
        if helper:
            helper_label = QLabel(helper)
            helper_label.setObjectName("helper")
            helper_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            layout.addWidget(helper_label)
        return wrapper

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        header = QFrame()
        header.setObjectName("appHeader")
        header.setFixedHeight(86)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(24, 14, 24, 12)
        header_row.setSpacing(14)
        app_icon = QLabel()
        icon_path = logo_path()
        if icon_path.is_file():
            app_icon.setPixmap(QPixmap(str(icon_path)).scaled(
                42, 42, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
        app_icon.setFixedSize(44, 44)
        header_row.addWidget(app_icon)
        brand = QVBoxLayout()
        brand.setSpacing(1)
        brand_title = QHBoxLayout()
        brand_title.setSpacing(8)
        title = QLabel(TITLE)
        title.setObjectName("title")
        brand_title.addWidget(title)
        badge = self._image_label("ocr_badge", 43, 25)
        badge.setAccessibleName("OCR")
        brand_title.addWidget(badge)
        brand_title.addStretch(1)
        brand.addLayout(brand_title)
        subtitle = QLabel("Extract text from images, PDFs and your screen")
        subtitle.setObjectName("subtitle")
        brand.addWidget(subtitle)
        header_row.addLayout(brand)
        header_row.addStretch(1)
        settings_header = self._button("", lambda: self._navigate("settings"), tooltip="Open OCR settings.", object_name="ghost")
        self._set_icon(settings_header, "settings", 22)
        settings_header.setFixedWidth(42)
        settings_header.setAccessibleName("Settings")
        header_row.addWidget(settings_header)
        help_header = self._button("", lambda: self._navigate("help"), tooltip="Open shortcuts and usage guide.", object_name="ghost")
        self._set_icon(help_header, "help", 22)
        help_header.setFixedWidth(42)
        help_header.setAccessibleName("Help")
        header_row.addWidget(help_header)
        outer.addWidget(header)

        body = QHBoxLayout()
        body.setContentsMargins(16, 0, 16, 16)
        body.setSpacing(8)
        sidebar = self._build_sidebar()
        body.addWidget(sidebar)

        workspace = QWidget()
        self.home_page = workspace
        workspace_layout = QVBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 12, 0, 0)
        workspace_layout.setSpacing(10)
        top_cards = QHBoxLayout()
        top_cards.setSpacing(10)
        top_cards.addWidget(self._build_source_card(), 55)
        top_cards.addWidget(self._build_settings_card(), 45)
        workspace_layout.addLayout(top_cards)

        self.empty_state = self._build_empty_state()
        workspace_layout.addWidget(self.empty_state, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_preview_panel())
        right = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter = right
        right.setChildrenCollapsible(False)
        right.addWidget(self._build_text_panel())
        self.region_panel = self._build_region_panel()
        right.addWidget(self.region_panel)
        self.region_panel.setMaximumHeight(0)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 56)
        splitter.setStretchFactor(1, 44)
        splitter.setSizes([720, 560])
        right.setSizes([680, 0])
        workspace_layout.addWidget(splitter, 1)

        workspace_layout.addWidget(self._build_status_card())
        self.pages = QStackedWidget()
        self.pages.addWidget(workspace)
        self.page_indices = {"home": 0}
        for name, page in (
            ("history", self._build_history_page()),
            ("settings", self._build_settings_page()),
            ("help", self._build_help_page()),
        ):
            self.page_indices[name] = self.pages.addWidget(page)
        body.addWidget(self.pages, 1)
        outer.addLayout(body, 1)

        self.empty_state.setVisible(True)
        self.content_splitter.setVisible(False)
        self.cancel_btn.setVisible(False)
        self._load_preferences()
        self._language_changed(self.language_box.currentIndex())
        self._navigate("home")

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(198)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 20, 14, 14)
        layout.setSpacing(7)
        entries = [
            ("home", "Home\nExtract text easily", "home"),
            ("history", "History\nRecent files", "history"),
            ("settings", "Settings\nOCR & Preferences", "settings"),
            ("help", "Help\nShortcuts & Guide", "help"),
        ]
        self.nav_buttons: dict[str, QPushButton] = {}
        for name, label, icon in entries:
            button = self._button(label, lambda _checked=False, page=name: self._navigate(page), object_name="navItem")
            self._set_icon(button, icon, 23)
            button.setProperty("selected", name == "home")
            button.setAccessibleName(label.splitlines()[0])
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            layout.addWidget(button)
            self.nav_buttons[name] = button
        layout.addStretch(1)
        hotkey = QFrame()
        hotkey.setObjectName("hotkeyCard")
        hotkey_layout = QVBoxLayout(hotkey)
        hotkey_layout.setContentsMargins(14, 13, 14, 12)
        hotkey_layout.setSpacing(7)
        hot_title = QLabel("Global Hotkey")
        hot_title.setObjectName("sectionTitle")
        hotkey_layout.addWidget(hot_title)
        keys = QLabel("  Ctrl  +  Shift  +  O  ")
        keys.setObjectName("blueBadge")
        keys.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hotkey_layout.addWidget(keys)
        hot_copy = QLabel("Capture any screen region\nfrom anywhere.")
        hot_copy.setObjectName("smallMeta")
        hotkey_layout.addWidget(hot_copy)
        learn = self._button("Learn more", lambda: self._navigate("help"), object_name="ghost")
        self._set_icon(learn, "help", 15)
        learn.setMinimumHeight(28)
        hotkey_layout.addWidget(learn)
        layout.addWidget(hotkey)
        return sidebar

    def _build_source_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(10)
        title_row = QHBoxLayout()
        title = QLabel("1. Choose Source")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addWidget(self._icon_label("info", 17))
        title_row.addStretch(1)
        layout.addLayout(title_row)
        row = QHBoxLayout()
        row.setSpacing(9)
        self.screen_btn = self._button("Screen Capture\nCapture a region or full screen", self.start_screen_capture, tooltip="Capture any screen region — Ctrl + Shift + O", object_name="sourceTile")
        self._set_icon(self.screen_btn, "screen_capture", 27)
        self.screen_btn.setProperty("selected", True)
        row.addWidget(self.screen_btn)
        self.open_btn = self._button("Open Image\nPNG, JPG, BMP, etc.", lambda: self.open_file("image"), tooltip="Open an image — Ctrl + O", object_name="sourceTile")
        self._set_icon(self.open_btn, "open_image", 27)
        row.addWidget(self.open_btn)
        self.pdf_btn = self._button("Open PDF\nExtract text from PDF pages", lambda: self.open_file("pdf"), tooltip="Open a PDF document.", object_name="sourceTile")
        self._set_icon(self.pdf_btn, "open_pdf", 27)
        row.addWidget(self.pdf_btn)
        layout.addLayout(row)
        self.file_label = QLabel("No source selected")
        self.file_label.setObjectName("smallMeta")
        self.file_label.setVisible(False)
        layout.addWidget(self.file_label)
        return card

    def _build_settings_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("surfaceCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(9)
        title_row = QHBoxLayout()
        title = QLabel("2. OCR Settings")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addWidget(self._icon_label("info", 17))
        title_row.addStretch(1)
        self.preprocess_box = QCheckBox("Auto enhance")
        self.preprocess_box.setToolTip("Automatically improve contrast, scale and alignment before OCR.")
        self.preprocess_box.setVisible(False)
        title_row.addWidget(self.preprocess_box)
        layout.addLayout(title_row)
        controls = QHBoxLayout()
        controls.setSpacing(16)
        language_col = QVBoxLayout()
        language_col.setSpacing(5)
        language_label = QLabel("Language(s)")
        language_label.setObjectName("settingLabel")
        language_col.addWidget(language_label)
        self.language_box = QComboBox()
        self.language_box.setToolTip("Choose the languages expected in the document.")
        self._populate_languages()
        self.language_box.currentIndexChanged.connect(self._language_changed)
        language_col.addWidget(self.language_box)
        controls.addLayout(language_col, 1)
        dpi_col = QVBoxLayout()
        dpi_col.setSpacing(5)
        dpi_label = QLabel("Image DPI (for PDF)")
        dpi_label.setObjectName("settingLabel")
        dpi_col.addWidget(dpi_label)
        self.dpi_box = QComboBox()
        self.dpi_box.addItems(["180", "240", "300 (Recommended)", "360", "420"])
        self.dpi_box.setCurrentText("300 (Recommended)")
        self.dpi_box.setToolTip("Higher PDF DPI can improve OCR, but takes longer to process.")
        self.dpi_box.currentTextChanged.connect(self._dpi_changed)
        dpi_col.addWidget(self.dpi_box)
        controls.addLayout(dpi_col, 1)
        layout.addLayout(controls)
        return card

    def _build_empty_state(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("emptyState")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(32, 32, 32, 32)
        layout.addStretch(1)
        icon = self._icon_label("glyphsnap_logo", 54)
        layout.addWidget(icon)
        title = QLabel("ابدأ باختيار مصدر للنص")
        title.setObjectName("title")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        copy = QLabel("افتح صورة أو PDF، أو التقط جزءًا من الشاشة لبدء استخراج النص.")
        copy.setObjectName("subtitle")
        copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(copy)
        actions = QHBoxLayout()
        actions.addStretch(1)
        open_empty = self._button("فتح ملف", lambda: self.open_file(), True)
        self._set_icon(open_empty, "open_image", 19)
        actions.addWidget(open_empty)
        capture_empty = self._button("التقاط من الشاشة", self.start_screen_capture)
        self._set_icon(capture_empty, "screen_capture", 19)
        actions.addWidget(capture_empty)
        actions.addStretch(1)
        layout.addLayout(actions)
        formats = QLabel("PDF  •  PNG  •  JPG  •  WEBP  •  BMP  •  TIFF")
        formats.setObjectName("smallMeta")
        formats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(formats)
        layout.addStretch(1)
        return frame

    def _icon_button(self, text: str, action, tooltip: str, icon: str | None = None) -> QPushButton:
        button = self._button(text, action, tooltip=tooltip, object_name="iconButton")
        if icon:
            self._set_icon(button, icon, 19)
        button.setAccessibleName(tooltip.split("—")[0].strip())
        return button

    def _build_preview_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        head = QHBoxLayout()
        head.setSpacing(8)
        title = QLabel("Image Preview")
        title.setObjectName("panelTitle")
        head.addWidget(title)
        self.source_badge = QLabel("Screen Capture")
        self.source_badge.setObjectName("blueBadge")
        head.addWidget(self.source_badge)
        self.resolution_label = QLabel("— × — px")
        self.resolution_label.setObjectName("smallMeta")
        head.addWidget(self.resolution_label)
        head.addStretch(1)
        self.minus_btn = self._icon_button("", lambda: self.image_view.zoom(1 / 1.2), "Zoom out — Ctrl + mouse wheel", "zoom_out")
        head.addWidget(self.minus_btn)
        self.plus_btn = self._icon_button("", lambda: self.image_view.zoom(1.2), "Zoom in — Ctrl + mouse wheel", "zoom_in")
        head.addWidget(self.plus_btn)
        self.fit_btn = self._icon_button("", lambda: self.image_view.fit_width(), "Fit image to the preview width.", "fit_width")
        head.addWidget(self.fit_btn)
        self.zoom_box = QComboBox()
        self.zoom_box.setFixedWidth(78)
        self.zoom_box.addItems(["50%", "75%", "100%", "125%", "150%", "200%"])
        self.zoom_box.setCurrentText("100%")
        self.zoom_box.activated.connect(
            lambda _index: self.image_view.set_zoom(int(self.zoom_box.currentText()[:-1]))
        )
        head.addWidget(self.zoom_box)
        self.fit_view_btn = self._icon_button("", lambda: self.image_view.fit_view(), "Fit the full image in the preview.", "fit_view")
        head.addWidget(self.fit_view_btn)
        layout.addLayout(head)

        self.page_controls = QWidget()
        page_row = QHBoxLayout(self.page_controls)
        page_row.setContentsMargins(0, 0, 0, 0)
        page_row.setSpacing(7)
        self.prev_btn = self._icon_button("", lambda: self._go_page(-1), "Previous PDF page.", "previous_page")
        page_row.addWidget(self.prev_btn)
        self.page_input = QLineEdit()
        self.page_input.setFixedWidth(48)
        self.page_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_input.setPlaceholderText("Page")
        self.page_input.setToolTip("Enter a page number and press Enter.")
        self.page_input.returnPressed.connect(self._jump_to_page)
        page_row.addWidget(self.page_input)
        self.page_label = QLabel("of 0")
        self.page_label.setObjectName("smallMeta")
        page_row.addWidget(self.page_label)
        self.next_btn = self._icon_button("", lambda: self._go_page(1), "Next PDF page.", "next_page")
        page_row.addWidget(self.next_btn)
        page_row.addStretch(1)
        self.all_btn = self._button("Extract Entire PDF", self.extract_all, tooltip="Run OCR on every page in this PDF.")
        self._set_icon(self.all_btn, "open_pdf", 18)
        page_row.addWidget(self.all_btn)
        layout.addWidget(self.page_controls)

        self.image_view = ImageView(self._selection_changed, self._zoom_changed)
        self.image_view.setToolTip("Drag to select text; use the blue handles to resize the OCR region.")
        layout.addWidget(self.image_view, 1)

        toolbar = QFrame()
        toolbar.setObjectName("regionToolbar")
        toolbar_layout = QVBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(12, 8, 12, 8)
        toolbar_layout.setSpacing(5)
        tool_title = QLabel("Selection Tools")
        tool_title.setObjectName("sectionTitle")
        toolbar_layout.addWidget(tool_title)
        tools = QHBoxLayout()
        tools.setSpacing(8)
        self.select_btn = self._button("Select Region", self.image_view_start_selection, tooltip="Select a region to extract from the image.", object_name="ghost")
        self._set_icon(self.select_btn, "select_region", 21)
        self.select_btn.setMinimumWidth(108)
        tools.addWidget(self.select_btn)
        self.full_image_btn = self._button("Full Image", self._select_full_image, tooltip="Select the complete image.", object_name="ghost")
        self._set_icon(self.full_image_btn, "full_image", 21)
        self.full_image_btn.setMinimumWidth(96)
        tools.addWidget(self.full_image_btn)
        self.clear_btn = self._button("Reset", self.image_view_clear, tooltip="Clear the current image selection.", object_name="ghost")
        self._set_icon(self.clear_btn, "reset_selection", 21)
        self.clear_btn.setMinimumWidth(82)
        tools.addWidget(self.clear_btn)
        divider = QFrame()
        divider.setObjectName("separator")
        tools.addWidget(divider)
        tools.addStretch(1)
        self.crop_btn = self._button("Extract Selection", self.extract_selection, True, "Extract text from the selected region — Ctrl + Enter")
        self._set_icon(self.crop_btn, "extract_selection", 20)
        crop_col = QVBoxLayout()
        crop_col.setSpacing(1)
        crop_col.addWidget(self.crop_btn)
        crop_shortcut = QLabel("Ctrl + Enter")
        crop_shortcut.setObjectName("shortcut")
        crop_shortcut.setAlignment(Qt.AlignmentFlag.AlignCenter)
        crop_col.addWidget(crop_shortcut)
        tools.addLayout(crop_col)
        self.full_btn = self._button("Extract Full Page", self.extract_current, tooltip="Extract text from the full image — Alt + Enter")
        self._set_icon(self.full_btn, "extract_page", 20)
        full_col = QVBoxLayout()
        full_col.setSpacing(1)
        full_col.addWidget(self.full_btn)
        full_shortcut = QLabel("Alt + Enter")
        full_shortcut.setObjectName("shortcut")
        full_shortcut.setAlignment(Qt.AlignmentFlag.AlignCenter)
        full_col.addWidget(full_shortcut)
        tools.addLayout(full_col)
        self.cancel_btn = self._button("Cancel", self.cancel_ocr, tooltip="Cancel OCR after the current operation finishes.", object_name="danger")
        self._set_icon(self.cancel_btn, "clear", 17)
        tools.addWidget(self.cancel_btn)
        toolbar_layout.addLayout(tools)
        layout.addWidget(toolbar)
        return panel

    def _build_text_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(9)
        head = QHBoxLayout()
        title = QLabel("Extracted Text")
        title.setObjectName("panelTitle")
        head.addWidget(title)
        self.ready_badge = QLabel("Ready")
        self.ready_badge.setObjectName("greenBadge")
        head.addWidget(self.ready_badge)
        head.addStretch(1)
        self.word_count_label = QLabel("0 words")
        self.word_count_label.setObjectName("smallMeta")
        head.addWidget(self.word_count_label)
        layout.addLayout(head)

        toolbar = QFrame()
        toolbar.setObjectName("toolbar")
        row = QHBoxLayout(toolbar)
        row.setContentsMargins(7, 5, 7, 5)
        row.setSpacing(2)
        self.editor = OCRTextEditor(12)
        format_actions = [
            ("bold", lambda: self.editor.toggle_character_format("bold"), "Bold"),
            ("italic", lambda: self.editor.toggle_character_format("italic"), "Italic"),
            ("underline", lambda: self.editor.toggle_character_format("underline"), "Underline"),
            ("bullet_list", lambda: self.editor.make_list(False), "Bulleted list"),
            ("numbered_list", lambda: self.editor.make_list(True), "Numbered list"),
            ("undo", self.editor.undo, "Undo"),
            ("redo", self.editor.redo, "Redo"),
        ]
        for icon_name, action, tip in format_actions:
            button = self._button("", action, tooltip=tip, object_name="toolButton")
            button.setAccessibleName(tip)
            self._set_icon(button, icon_name, 19)
            row.addWidget(button)
        row.addStretch(1)
        copy_tool = self._button("", lambda: self.copy_field(self.editor), tooltip="Copy all extracted text.", object_name="toolButton")
        self._set_icon(copy_tool, "copy", 19)
        row.addWidget(copy_tool)
        clear_tool = self._button("Clear", self.editor.clear, tooltip="Clear the extracted text.", object_name="subtle")
        self._set_icon(clear_tool, "clear", 17)
        row.addWidget(clear_tool)
        layout.addWidget(toolbar)
        self.editor.setToolTip("Editable OCR output with automatic Arabic and English text direction.")
        self.editor.textChanged.connect(self._mark_dirty)
        layout.addWidget(self.editor, 1)

        actions = QHBoxLayout()
        actions.setSpacing(9)
        copy_button = self._button("Copy Text\nCtrl + C", lambda: self.copy_field(self.editor), True)
        self._set_icon(copy_button, "copy", 20)
        actions.addWidget(copy_button, 1)
        self.save_btn = self._button("Save as TXT", self.save_text, tooltip="Save the extracted text as a TXT file — Ctrl + S")
        self._set_icon(self.save_btn, "save_txt", 20)
        actions.addWidget(self.save_btn, 1)
        save_pdf = self._button("Save as PDF", lambda: None)
        self._set_icon(save_pdf, "save_pdf", 20)
        save_pdf.setEnabled(False)
        save_pdf.setToolTip("PDF export is not available in the current OCR backend.")
        actions.addWidget(save_pdf, 1)
        layout.addLayout(actions)
        return panel

    def _build_region_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("regionPanel")
        layout = QHBoxLayout(panel)
        layout.setContentsMargins(10, 8, 10, 8)
        title = QLabel("Selection result")
        title.setObjectName("sectionTitle")
        layout.addWidget(title)
        self.region_editor = OCRTextEditor(11)
        self.region_editor.setToolTip("Review the selected-region result before inserting it into the main text.")
        self.region_editor.textChanged.connect(self._update_buttons)
        layout.addWidget(self.region_editor, 1)
        self.replace_btn = self._button("Insert in Text", self.replace_text, True, "Insert this result at the current editor cursor.")
        self._set_icon(self.replace_btn, "add_selection", 18)
        layout.addWidget(self.replace_btn)
        return panel

    def _build_status_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("statusCard")
        card.setMinimumHeight(100)
        footer = QHBoxLayout(card)
        footer.setContentsMargins(20, 12, 20, 12)
        footer.setSpacing(18)
        success = self._icon_label("success", 38)
        footer.addWidget(success)
        status_col = QVBoxLayout()
        status_col.setSpacing(3)
        self.status_title = QLabel("Ready for OCR")
        self.status_title.setStyleSheet("color:#11885B;font:700 11pt 'Segoe UI';")
        status_col.addWidget(self.status_title)
        self.status_label = QLabel("Choose a source, then extract a page or selection.")
        self.status_label.setObjectName("smallMeta")
        status_col.addWidget(self.status_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setToolTip("OCR processing progress.")
        progress_row = QHBoxLayout()
        progress_row.setSpacing(10)
        progress_row.addWidget(self.progress, 1)
        self.progress_percent = QLabel("0%")
        self.progress_percent.setObjectName("smallMeta")
        progress_row.addWidget(self.progress_percent)
        status_col.addLayout(progress_row)
        footer.addLayout(status_col, 3)
        for label, value_widget, icon_name in [
            ("Detected Language", "language", "language"),
            ("Processing Time", "time", "processing_time"),
            ("Selected Area", "area", "selected_area"),
        ]:
            sep = QFrame()
            sep.setObjectName("separator")
            footer.addWidget(sep)
            metric_wrapper = QHBoxLayout()
            metric_wrapper.setSpacing(10)
            metric_wrapper.addWidget(self._icon_label(icon_name, 32))
            metric = QVBoxLayout()
            metric.setSpacing(4)
            metric_title = QLabel(label)
            metric_title.setObjectName("metricTitle")
            metric.addWidget(metric_title)
            value = QLabel("—")
            value.setObjectName("metricValue")
            metric.addWidget(value)
            if value_widget == "language":
                self.detected_language_value = value
            elif value_widget == "time":
                self.processing_time_value = value
            else:
                self.area_value = value
            metric_wrapper.addLayout(metric)
            footer.addLayout(metric_wrapper, 1)
        return card

    def _build_page_shell(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 24, 0, 0)
        layout.setSpacing(16)
        heading = QLabel(title)
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        description = QLabel(subtitle)
        description.setObjectName("subtitle")
        description.setWordWrap(True)
        layout.addWidget(description)
        return page, layout

    def _build_history_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "History",
            "Recent OCR results are stored locally on this computer. Screen images are never retained.",
        )
        actions = QHBoxLayout()
        self.history_count_label = QLabel("0 items")
        self.history_count_label.setObjectName("smallMeta")
        actions.addWidget(self.history_count_label)
        actions.addStretch(1)
        self.clear_history_btn = self._button(
            "Clear History", self._clear_history, tooltip="Delete all saved OCR history.", object_name="danger"
        )
        self._set_icon(self.clear_history_btn, "clear", 17)
        actions.addWidget(self.clear_history_btn)
        layout.addLayout(actions)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.history_container = QWidget()
        self.history_layout = QVBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 8, 0)
        self.history_layout.setSpacing(10)
        self.history_layout.addStretch(1)
        scroll.setWidget(self.history_container)
        layout.addWidget(scroll, 1)
        return page

    def _build_settings_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "Settings",
            "Choose the defaults GlyphSnap uses for new images, PDFs and screen captures.",
        )
        card = QFrame()
        card.setObjectName("pageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 20, 22, 20)
        card_layout.setSpacing(14)
        title = QLabel("OCR & PDF preferences")
        title.setObjectName("panelTitle")
        card_layout.addWidget(title)

        language_label = QLabel("Default language(s)")
        language_label.setObjectName("settingLabel")
        card_layout.addWidget(language_label)
        self.settings_language_box = QComboBox()
        for index in range(self.language_box.count()):
            self.settings_language_box.addItem(
                self.language_box.itemIcon(index),
                self.language_box.itemText(index),
                self.language_box.itemData(index),
            )
        card_layout.addWidget(self.settings_language_box)

        dpi_label = QLabel("Default PDF rendering DPI")
        dpi_label.setObjectName("settingLabel")
        card_layout.addWidget(dpi_label)
        self.settings_dpi_box = QComboBox()
        self.settings_dpi_box.addItems(["180", "240", "300 (Recommended)", "360", "420"])
        card_layout.addWidget(self.settings_dpi_box)

        self.settings_preprocess_box = QCheckBox("Automatically enhance scans before OCR")
        self.settings_preprocess_box.setToolTip(
            "Improves contrast, alignment and scale before recognition."
        )
        card_layout.addWidget(self.settings_preprocess_box)

        privacy = QLabel(
            "Privacy: OCR stays on this computer. History stores extracted text and source paths, "
            "but never stores captured screen images."
        )
        privacy.setObjectName("smallMeta")
        privacy.setWordWrap(True)
        card_layout.addWidget(privacy)
        layout.addWidget(card)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        reset = self._button("Reset Defaults", self._reset_preferences, object_name="subtle")
        self._set_icon(reset, "reset_selection", 17)
        buttons.addWidget(reset)
        apply_button = self._button("Apply Settings", self._apply_preferences, True)
        self._set_icon(apply_button, "settings", 18)
        buttons.addWidget(apply_button)
        layout.addLayout(buttons)
        self.settings_feedback = QLabel("")
        self.settings_feedback.setObjectName("smallMeta")
        self.settings_feedback.setAlignment(Qt.AlignmentFlag.AlignRight)
        layout.addWidget(self.settings_feedback)
        layout.addStretch(1)
        return page

    def _build_help_page(self) -> QWidget:
        page, layout = self._build_page_shell(
            "Help",
            "Use these shortcuts from the Home workspace. The global capture shortcut works while GlyphSnap is running.",
        )
        card = QFrame()
        card.setObjectName("pageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(22, 20, 22, 20)
        card_layout.setSpacing(12)
        heading = QLabel("Shortcuts & guide")
        heading.setObjectName("panelTitle")
        card_layout.addWidget(heading)
        shortcuts = [
            ("Ctrl + O", "Open an image or PDF"),
            ("Ctrl + Shift + O", "Capture a screen region"),
            ("Ctrl + Enter", "Extract the selected region"),
            ("Alt + Enter", "Extract the full page"),
            ("Ctrl + S", "Save extracted text as TXT"),
            ("Ctrl + mouse wheel", "Zoom the image preview"),
        ]
        for key, description in shortcuts:
            row = QHBoxLayout()
            key_label = QLabel(key)
            key_label.setObjectName("blueBadge")
            key_label.setMinimumWidth(145)
            key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            row.addWidget(key_label)
            row.addWidget(QLabel(description), 1)
            card_layout.addLayout(row)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _navigate(self, page: str) -> None:
        if not hasattr(self, "pages") or page not in self.page_indices:
            return
        if page == "history":
            self._refresh_history()
        elif page == "settings":
            self._sync_settings_page()
        self.pages.setCurrentIndex(self.page_indices[page])
        for name, button in self.nav_buttons.items():
            button.setProperty("selected", name == page)
            button.style().unpolish(button)
            button.style().polish(button)

    def _refresh_history(self) -> None:
        while self.history_layout.count():
            item = self.history_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        records = self.history_store.load()
        self.history_count_label.setText(f"{len(records)} item{'s' if len(records) != 1 else ''}")
        self.clear_history_btn.setEnabled(bool(records))
        if not records:
            empty = QFrame()
            empty.setObjectName("pageCard")
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(28, 42, 28, 42)
            empty_layout.addWidget(self._icon_label("history", 42), 0, Qt.AlignmentFlag.AlignHCenter)
            title = QLabel("No OCR history yet")
            title.setObjectName("panelTitle")
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(title)
            copy = QLabel("Completed OCR results will appear here.")
            copy.setObjectName("smallMeta")
            copy.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(copy)
            self.history_layout.addWidget(empty)
            self.history_layout.addStretch(1)
            return
        for record in records:
            self.history_layout.addWidget(self._history_row(record))
        self.history_layout.addStretch(1)

    def _history_row(self, record: HistoryRecord) -> QFrame:
        row = QFrame()
        row.setObjectName("historyCard")
        layout = QHBoxLayout(row)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(14)
        source_icon = {
            "screen": "screen_capture", "pdf": "open_pdf", "image": "open_image"
        }.get(record.source_type, "text_file")
        layout.addWidget(self._icon_label(source_icon, 30))
        details = QVBoxLayout()
        details.setSpacing(4)
        title = QLabel(record.source_name or "OCR result")
        title.setObjectName("panelTitle")
        details.addWidget(title)
        try:
            created = datetime.fromisoformat(record.created_at).astimezone().strftime("%b %d, %Y  %H:%M")
        except ValueError:
            created = record.created_at
        language = " + ".join(code.upper() for code in record.languages) or "Unknown language"
        meta = QLabel(
            f"{created}  •  {language}  •  {record.word_count} words  •  "
            f"{record.processing_seconds:.1f} seconds"
        )
        meta.setObjectName("smallMeta")
        details.addWidget(meta)
        preview = " ".join(record.text.split())
        if len(preview) > 150:
            preview = preview[:147] + "…"
        preview_label = QLabel(preview or "No text was detected.")
        preview_label.setWordWrap(True)
        details.addWidget(preview_label)
        layout.addLayout(details, 1)
        copy_button = self._button("Copy", lambda _checked=False, text=record.text: self._copy_text(text), object_name="subtle")
        self._set_icon(copy_button, "copy", 17)
        layout.addWidget(copy_button)
        open_button = self._button("Open", lambda _checked=False, item=record: self._open_history(item), object_name="subtle")
        self._set_icon(open_button, "open_image", 17)
        source_exists = bool(record.source_path and Path(record.source_path).is_file())
        open_button.setEnabled(source_exists)
        if not source_exists:
            open_button.setToolTip("The source file is unavailable. Copy the saved text instead.")
        layout.addWidget(open_button)
        delete_button = self._button("Delete", lambda _checked=False, item=record: self._delete_history(item), object_name="danger")
        self._set_icon(delete_button, "clear", 17)
        layout.addWidget(delete_button)
        return row

    def _copy_text(self, text: str) -> None:
        if text:
            QApplication.clipboard().setText(text)
            self._status("Copied saved OCR text to the clipboard.")

    def _open_history(self, record: HistoryRecord) -> None:
        path = Path(record.source_path)
        if not path.is_file():
            return
        self.load_file(path)
        self.editor.setPlainText(record.text)
        self.text_by_page[0] = record.text
        self._navigate("home")
        self._status("Reopened the source and restored its saved OCR text.")

    def _delete_history(self, record: HistoryRecord) -> None:
        answer = QMessageBox.question(
            self,
            "Delete history item",
            f"Delete the saved OCR result for {record.source_name}?",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.history_store.delete(record.id)
            self._refresh_history()

    def _clear_history(self) -> None:
        answer = QMessageBox.question(
            self,
            "Clear OCR history",
            "Delete all locally saved OCR history? This cannot be undone.",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.history_store.clear()
            self._refresh_history()

    def _find_language_index(self, box: QComboBox, languages: list[str]) -> int:
        for index in range(box.count()):
            if list(box.itemData(index) or []) == languages:
                return index
        return 0

    def _load_preferences(self) -> None:
        languages = self.preferences.languages()
        self.language_box.setCurrentIndex(self._find_language_index(self.language_box, languages))
        dpi_text = str(self.preferences.pdf_dpi())
        index = self.dpi_box.findText(dpi_text, Qt.MatchFlag.MatchStartsWith)
        self.dpi_box.setCurrentIndex(max(0, index))
        self.preprocess_box.setChecked(self.preferences.preprocess())
        self._sync_settings_page()

    def _sync_settings_page(self) -> None:
        if not hasattr(self, "settings_language_box"):
            return
        self.settings_language_box.setCurrentIndex(
            self._find_language_index(self.settings_language_box, self.selected_languages())
        )
        dpi_index = self.settings_dpi_box.findText(
            str(self._pdf_dpi()), Qt.MatchFlag.MatchStartsWith
        )
        self.settings_dpi_box.setCurrentIndex(max(0, dpi_index))
        self.settings_preprocess_box.setChecked(self.preprocess_box.isChecked())

    def _apply_preferences(self) -> None:
        languages = list(self.settings_language_box.currentData() or [])
        self.language_box.setCurrentIndex(self._find_language_index(self.language_box, languages))
        dpi = int(self.settings_dpi_box.currentText().split()[0])
        dpi_index = self.dpi_box.findText(str(dpi), Qt.MatchFlag.MatchStartsWith)
        self.dpi_box.setCurrentIndex(max(0, dpi_index))
        self.preprocess_box.setChecked(self.settings_preprocess_box.isChecked())
        self.preferences.set_languages(languages)
        self.preferences.set_pdf_dpi(dpi)
        self.preferences.set_preprocess(self.preprocess_box.isChecked())
        self.preferences.sync()
        self.settings_feedback.setText("Settings saved.")
        self._status("OCR settings updated.")

    def _reset_preferences(self) -> None:
        self.preferences.reset()
        self._load_preferences()
        self.settings_feedback.setText("Default settings restored.")

    def _populate_languages(self) -> None:
        installed = set(self.available_languages)
        language_icon = QIcon(str(asset_icon("language")))
        presets = [
            ("Arabic + English (ara+eng)", ["ara", "eng"]),
            ("Arabic (ara)", ["ara"]),
            ("English (eng)", ["eng"]),
        ]
        for label, languages in presets:
            if all(language in installed for language in languages):
                self.language_box.addItem(language_icon, label, languages)
        used = {language for _, languages in presets for language in languages}
        for language, label in LANGUAGE_LABELS.items():
            if language in installed and language not in used:
                self.language_box.addItem(language_icon, label, [language])
                used.add(language)
        for language in sorted(installed):
            if language in used or language == "osd":
                continue
            self.language_box.addItem(
                language_icon, LANGUAGE_LABELS.get(language, language), [language]
            )
        if self.language_box.count() == 0:
            self.language_box.addItem(language_icon, "لا توجد لغات OCR مثبتة", [])

    def _pdf_dpi(self) -> int:
        return int(self.dpi_box.currentText().split()[0])

    def _show_help(self) -> None:
        self._navigate("help")

    def _select_full_image(self) -> None:
        if self.image is None:
            return
        self.image_view._set_selection(QRectF(0, 0, self.image.width, self.image.height))
        self._status("The full image is selected. You can extract it now.")

    def selected_languages(self) -> list[str]:
        value = self.language_box.currentData()
        return list(value) if isinstance(value, list) else []

    def _language_changed(self, _index: int) -> None:
        languages = self.selected_languages()
        self.editor.set_ocr_direction(languages)
        self.region_editor.set_ocr_direction(languages)
        if hasattr(self, "detected_language_value"):
            labels = {"ara": "Arabic", "eng": "English"}
            self.detected_language_value.setText(
                " + ".join(labels.get(language, language.upper()) for language in languages) or "—"
            )
        if languages:
            self._status("OCR language set to " + " + ".join(languages) + ".")
            self.preferences.set_languages(languages)
            self.preferences.sync()

    def _status(self, text: str) -> None:
        self.status_label.setText(text)

    def _set_source_selection(self, source: str) -> None:
        for button, name in (
            (self.screen_btn, "screen"),
            (self.open_btn, "image"),
            (self.pdf_btn, "pdf"),
        ):
            button.setProperty("selected", name == source)
            button.style().unpolish(button)
            button.style().polish(button)

    def _mark_dirty(self) -> None:
        if self.path is not None and not self.busy:
            self.file_label.setText(f"{self.path.name}  •  غير محفوظ")
        if hasattr(self, "save_btn"):
            if hasattr(self, "word_count_label"):
                words = len(self.editor.toPlainText().split())
                self.word_count_label.setText(f"{words} word{'s' if words != 1 else ''}")
            self._update_buttons()

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
        self._update_buttons()
        if box:
            width, height = box[2] - box[0], box[3] - box[1]
            self.area_value.setText(f"{width} × {height} px")
            self._status("A region is selected. Extract it or adjust the blue handles.")
        elif hasattr(self, "area_value"):
            self.area_value.setText("—")

    def _zoom_changed(self, percent: int) -> None:
        if hasattr(self, "zoom_box"):
            value = f"{percent}%"
            if self.zoom_box.findText(value) < 0:
                self.zoom_box.addItem(value)
            self.zoom_box.setCurrentText(value)

    def _update_buttons(self) -> None:
        has_image = self.image is not None
        count = len(self.document) if self.document is not None else (1 if has_image else 0)
        self.page_controls.setVisible(self.document is not None)
        self.page_label.setText(f"of {count}" if count else "of 0")
        if count and self.page_input.text() != str(self.page_index + 1):
            self.page_input.setText(str(self.page_index + 1))
        self.open_btn.setEnabled(not self.busy)
        self.screen_btn.setEnabled(not self.busy)
        self.pdf_btn.setEnabled(not self.busy)
        self.language_box.setEnabled(not self.busy)
        self.preprocess_box.setEnabled(not self.busy)
        self.dpi_box.setEnabled(not self.busy)
        self.page_input.setEnabled(self.document is not None and not self.busy)
        self.prev_btn.setEnabled(bool(self.document and self.page_index > 0 and not self.busy))
        self.next_btn.setEnabled(bool(self.document and self.page_index+1 < count and not self.busy))
        self.full_btn.setEnabled(has_image and not self.busy)
        self.select_btn.setEnabled(has_image and not self.busy)
        self.full_image_btn.setEnabled(has_image and not self.busy)
        self.all_btn.setEnabled(self.document is not None and not self.busy)
        self.cancel_btn.setVisible(self.busy)
        self.cancel_btn.setEnabled(self.busy)
        self.crop_btn.setEnabled(bool(self.image_view.selection and not self.busy))
        self.clear_btn.setEnabled(bool(self.image_view.selection and not self.busy))
        self.replace_btn.setEnabled(bool(self.region_editor.toPlainText().strip()) and not self.busy)
        has_text = bool(self.editor.toPlainText().strip() or self.text_by_page)
        self.save_btn.setEnabled(self.path is not None and has_text and not self.busy)

    def open_file(self, source: str | None = None) -> None:
        if self.busy:
            return
        if source == "image":
            file_filter = "Images (*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;All files (*)"
        elif source == "pdf":
            file_filter = "PDF documents (*.pdf);;All files (*)"
        else:
            file_filter = "PDF and images (*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff);;All files (*)"
        filename, _ = QFileDialog.getOpenFileName(
            self, "اختر صورة أو مستند PDF", "",
            file_filter)
        if filename:
            self.load_file(Path(filename))

    def load_file(self, path: Path) -> None:
        try:
            if path.suffix.lower() == ".pdf":
                document = pymupdf.open(path)
                if document.needs_pass or len(document) == 0:
                    document.close()
                    raise ValueError("ملف PDF محمي بكلمة مرور أو فارغ.")
                image = render_pdf_page(document[0], self._pdf_dpi())
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
        self.resolution_label.setText(f"{image.width} × {image.height} px")
        is_pdf = path.suffix.lower() == ".pdf"
        self.source_badge.setText("PDF" if is_pdf else "Open Image")
        self._set_source_selection("pdf" if is_pdf else "image")
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
            image = render_pdf_page(self.document[target], self._pdf_dpi())
        except Exception as exc:
            QMessageBox.critical(self, "تعذر عرض الصفحة", str(exc))
            return
        self.page_index = target
        self.image = image
        self.image_view.set_image(image)
        self.resolution_label.setText(f"{image.width} × {image.height} px")
        self.editor.setPlainText(self.text_by_page.get(target, ""))
        self.region_editor.setPlainText("")
        self._update_buttons()

    def _dpi_changed(self, _value: str) -> None:
        self.preferences.set_pdf_dpi(self._pdf_dpi())
        self.preferences.sync()
        if self.document is None or self.busy:
            return
        self._remember_page()
        try:
            image = render_pdf_page(self.document[self.page_index], self._pdf_dpi())
            self.image = image
            self.image_view.set_image(image)
            self.resolution_label.setText(f"{image.width} × {image.height} px")
        except Exception as exc:
            QMessageBox.critical(self, "تعذر تغيير الدقة", str(exc))

    def image_view_clear(self) -> None:
        self.image_view.clear_selection()

    def _tesseract(self) -> str | None:
        cmd = self.tesseract_command
        languages = self.selected_languages()
        if not cmd or not languages:
            QMessageBox.critical(
                self,
                "Tesseract غير جاهز",
                "ثبّت Tesseract وحزمة لغة واحدة على الأقل، ثم أعد تشغيل التطبيق.",
            )
            return None
        missing = [language for language in languages if language not in self.available_languages]
        if missing:
            QMessageBox.critical(
                self,
                "حزمة لغة غير مثبتة",
                "حزم Tesseract المطلوبة غير موجودة: " + ", ".join(missing),
            )
            return None
        return cmd

    def _recognize(
        self,
        image: Image.Image,
        cmd: str,
        psm: int,
        languages: list[str],
        preprocess: bool,
    ) -> OCRResult:
        return run_ocr(
            image,
            cmd,
            languages,
            psm,
            preprocess=preprocess,
        )

    @staticmethod
    def _detected_language(text: str) -> str:
        has_arabic = any("\u0600" <= character <= "\u06ff" for character in text)
        has_latin = any(("A" <= character <= "Z") or ("a" <= character <= "z") for character in text)
        if has_arabic and has_latin:
            return "Arabic + English"
        if has_arabic:
            return "Arabic"
        if has_latin:
            return "English"
        return "Not detected"

    def _record_history(self, text: str, source_type: str | None = None) -> None:
        if not text.strip():
            return
        if source_type is None:
            source_type = "pdf" if self.document is not None else "image"
        source_path = ""
        source_name = "Screen capture" if source_type == "screen" else "OCR result"
        if self.path is not None and self.path.is_file():
            source_path = str(self.path.resolve())
            source_name = self.path.name
        elif self.path is not None and source_type != "screen":
            source_name = self.path.name
        record = HistoryRecord.create(
            source_type=source_type,
            source_name=source_name,
            source_path=source_path,
            languages=self.selected_languages(),
            processing_seconds=self.last_processing_seconds,
            text=text,
        )
        try:
            self.history_store.add(record)
        except OSError:
            self._status("OCR completed, but the local history file could not be updated.")

    def start_screen_capture(self) -> None:
        if self.busy or self.screen_selector is not None:
            return
        if not self._tesseract():
            return
        self._status("اختر منطقة من الشاشة، أو اضغط Esc للإلغاء.")
        self.hide()
        QTimer.singleShot(180, self._show_screen_selector)

    def _show_screen_selector(self) -> None:
        try:
            capture = capture_virtual_desktop()
            selector = ScreenRegionSelector(capture)
            selector.captured.connect(self._screen_region_captured)
            selector.cancelled.connect(self._screen_capture_cancelled)
            selector.destroyed.connect(lambda: setattr(self, "screen_selector", None))
            self.screen_selector = selector
            selector.show()
            selector.raise_()
            selector.activateWindow()
            selector.setFocus()
        except Exception as exc:
            self.show()
            QMessageBox.critical(self, "تعذر التقاط الشاشة", str(exc))

    def _screen_capture_cancelled(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        self._status("أُلغي التقاط الشاشة.")

    def _screen_region_captured(self, image: Image.Image) -> None:
        self.show()
        self.raise_()
        self.activateWindow()
        cmd = self._tesseract()
        if not cmd:
            return
        captured = image.convert("RGB")
        languages = self.selected_languages()
        preprocess = self.preprocess_box.isChecked()
        self._start_worker(
            lambda _cancel: (captured, self._recognize(captured, cmd, 6, languages, preprocess)),
            "screen",
        )

    def _start_worker(self, job, kind: str) -> None:
        self.busy = True
        self.ocr_started_at = time.perf_counter()
        cancel_event = threading.Event()
        self.cancel_event = cancel_event
        self.progress.setRange(0, 0)
        self.progress_percent.setText("Working")
        self.processing_time_value.setText("—")
        self.status_title.setText("OCR in progress")
        self.ready_badge.setText("Processing")
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
            languages = self.selected_languages()
            preprocess = self.preprocess_box.isChecked()
            self._start_worker(
                lambda _cancel: (index, self._recognize(image, cmd, 3, languages, preprocess)),
                "full",
            )

    def extract_selection(self) -> None:
        if self.image is None or self.image_view.selection is None or self.busy:
            return
        cmd = self._tesseract()
        if cmd:
            image = crop_image(self.image, self.image_view.selection)
            languages = self.selected_languages()
            preprocess = self.preprocess_box.isChecked()
            self._start_worker(
                lambda _cancel: self._recognize(image, cmd, 6, languages, preprocess),
                "region",
            )

    def extract_all(self) -> None:
        if self.document is None or self.path is None or self.busy:
            return
        cmd = self._tesseract()
        if not cmd:
            return
        self._remember_page()
        path = self.path
        dpi = self._pdf_dpi()
        languages = self.selected_languages()
        preprocess = self.preprocess_box.isChecked()

        def job(cancel_event):
            texts = {}
            with pymupdf.open(path) as doc:
                for index in range(len(doc)):
                    if cancel_event.is_set():
                        break
                    page = doc[index]
                    texts[index] = self._recognize(
                        render_pdf_page(page, dpi), cmd, 3, languages, preprocess
                    ).text
                    self.events.put(("progress", (index + 1, len(doc))))
            return texts

        self._start_worker(job, "all")

    def _poll_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "progress":
                    completed, total = payload
                    self.progress.setRange(0, total)
                    self.progress.setValue(completed)
                    self.progress_percent.setText(f"{round(completed / max(1, total) * 100)}%")
                    self._status(f"اكتملت الصفحة {completed} من {total}.")
                    continue
                self.busy = False
                self.cancel_event = None
                if self.ocr_started_at is not None:
                    self.last_processing_seconds = time.perf_counter() - self.ocr_started_at
                self.ocr_started_at = None
                self.processing_time_value.setText(f"{self.last_processing_seconds:.1f} seconds")
                self.progress.setRange(0, 1)
                self.progress.setValue(0 if kind in {"error", "cancelled"} else 1)
                self.progress_percent.setText("0%" if kind in {"error", "cancelled"} else "100%")
                if kind == "error":
                    QMessageBox.critical(self, "فشل استخراج النص", payload)
                    self.status_title.setText("OCR failed")
                    self.ready_badge.setText("Failed")
                    self._status("تعذر الاستخراج. تحقق من الملف وTesseract.")
                elif kind == "cancelled":
                    if isinstance(payload, dict):
                        self.text_by_page.update(payload)
                    self.editor.setPlainText(self.text_by_page.get(self.page_index, ""))
                    self.status_title.setText("OCR cancelled")
                    self.ready_badge.setText("Cancelled")
                    self._status("تم إلغاء الاستخراج مع الاحتفاظ بالنتائج المكتملة.")
                elif kind == "full":
                    index, result = payload
                    self.text_by_page[index] = result.text
                    if self.page_index == index:
                        self.editor.setPlainText(result.text)
                    self.status_title.setText("OCR Completed")
                    self.ready_badge.setText("Ready")
                    self.detected_language_value.setText(self._detected_language(result.text))
                    self._record_history(result.text)
                    self._status("تم استخراج النص. راجع النتيجة أو انسخها مباشرة.")
                elif kind == "region":
                    self.region_editor.setPlainText(payload.text)
                    self._fit_region_result(payload.text)
                    self.status_title.setText("Selection extracted")
                    self.ready_badge.setText("Ready")
                    self.detected_language_value.setText(self._detected_language(payload.text))
                    self._record_history(payload.text, "selection")
                    self._status("تم استخراج المنطقة. راجع النتيجة ثم أدرجها في النص.")
                elif kind == "screen":
                    image, result = payload
                    if self.document is not None:
                        self.document.close()
                    self.path = Path("screen-capture.png")
                    self.document = None
                    self.page_index = 0
                    self.image = image
                    self.text_by_page = {0: result.text}
                    self.image_view.set_image(image)
                    self.resolution_label.setText(f"{image.width} × {image.height} px")
                    self.source_badge.setText("Screen Capture")
                    self._set_source_selection("screen")
                    self.editor.setPlainText(result.text)
                    self.region_editor.setPlainText("")
                    self.file_label.setText("لقطة من الشاشة")
                    self.empty_state.setVisible(False)
                    self.content_splitter.setVisible(True)
                    if result.text.strip():
                        QApplication.clipboard().setText(result.text)
                        self.status_title.setText("OCR Completed")
                        self.ready_badge.setText("Ready")
                        self.detected_language_value.setText(self._detected_language(result.text))
                        self._record_history(result.text, "screen")
                        self._status("تم استخراج النص ونسخه. راجع النتيجة أو احفظها.")
                    else:
                        self._status("اكتمل الالتقاط، لكن لم يُعثر على نص. جرّب مساحة أكبر أو لغة أخرى.")
                elif kind == "all":
                    self.text_by_page.update(payload)
                    self.editor.setPlainText(self.text_by_page.get(self.page_index, ""))
                    self.status_title.setText("OCR Completed")
                    self.ready_badge.setText("Ready")
                    combined = "\n\n".join(payload[index] for index in sorted(payload))
                    self.detected_language_value.setText(self._detected_language(combined))
                    self._record_history(combined, "pdf")
                    self._status(f"اكتمل استخراج {len(payload)} صفحة.")
                self._update_buttons()
        except queue.Empty:
            pass

    def _fit_region_result(self, text: str) -> None:
        splitter = self.right_splitter
        available = splitter.height()
        lines = sum(max(1, (len(line) + 54) // 55) for line in text.splitlines())
        desired = min(int(available * 0.42), max(150, min(270, 100 + min(lines, 7) * 20)))
        self.region_panel.setMaximumHeight(270)
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

    def copy_field(self, editor: QTextEdit) -> None:
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
        self.global_hotkey.close()
        if self.document is not None:
            self.document.close()
        super().closeEvent(event)


def run_smoke_test(pdf_path: Path, output_dir: Path) -> None:
    app = QApplication.instance() or QApplication(["GlyphSnap", "-platform", "offscreen"])
    pdf_path = pdf_path.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with pymupdf.open(str(pdf_path)) as document:
        if len(document) == 0:
            raise RuntimeError("PDF contains no pages.")
        image = render_pdf_page(document[0], 120)

    tesseract_cmd = locate_tesseract()
    languages = installed_languages(tesseract_cmd)
    if "ara" not in languages:
        raise RuntimeError("Tesseract with the Arabic 'ara' language is required.")
    text = run_ocr(image, tesseract_cmd, ["ara"], 3).text
    region = crop_image(image, (0, 0, max(8, image.width // 2), max(8, image.height // 2)))
    region_text = run_ocr(region, tesseract_cmd, ["ara"], 6).text
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
    configure_windows_identity()
    app = QApplication(sys.argv)
    app.setApplicationName("GlyphSnap")
    app.setApplicationDisplayName("GlyphSnap")
    app.setOrganizationName("GlyphSnap")
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
