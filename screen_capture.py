"""Windows global hotkey and Snipping Tool-style screen region selector."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PIL import Image
from PySide6.QtCore import QAbstractNativeEventFilter, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


HOTKEY_ID = 0xA0C1
WM_HOTKEY = 0x0312
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


def capture_virtual_desktop() -> tuple[QPixmap, QRect]:
    screens = QApplication.screens()
    if not screens:
        raise RuntimeError("No screen is available for capture.")
    geometry = screens[0].geometry()
    for screen in screens[1:]:
        geometry = geometry.united(screen.geometry())
    canvas = QPixmap(geometry.size())
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    for screen in screens:
        shot = screen.grabWindow(0)
        offset = screen.geometry().topLeft() - geometry.topLeft()
        painter.drawPixmap(offset, shot)
    painter.end()
    return canvas, geometry


def pixmap_to_pil(pixmap: QPixmap) -> Image.Image:
    image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    pointer = image.bits()
    raw = bytes(pointer[: image.sizeInBytes()])
    return Image.frombytes("RGBA", (image.width(), image.height()), raw).convert("RGB")


class GlobalHotkey(QAbstractNativeEventFilter):
    """Register Ctrl+Shift+O without adding a third-party keyboard hook."""

    def __init__(self, callback) -> None:
        super().__init__()
        self.callback = callback
        self.registered = False

    def register(self) -> bool:
        if not hasattr(ctypes, "windll"):
            return False
        self.registered = bool(
            ctypes.windll.user32.RegisterHotKey(
                None,
                HOTKEY_ID,
                MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT,
                ord("O"),
            )
        )
        if self.registered:
            QApplication.instance().installNativeEventFilter(self)
        return self.registered

    def nativeEventFilter(self, event_type, message):
        try:
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                self.callback()
                return True, 0
        except (TypeError, ValueError):
            pass
        return False, 0

    def close(self) -> None:
        if self.registered:
            QApplication.instance().removeNativeEventFilter(self)
            ctypes.windll.user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False


class ScreenRegionSelector(QWidget):
    captured = Signal(object)
    cancelled = Signal()

    def __init__(self, screenshot: QPixmap, geometry: QRect) -> None:
        super().__init__(None)
        self.screenshot = screenshot
        self.desktop_geometry = geometry
        self.start: QPoint | None = None
        self.end: QPoint | None = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setGeometry(geometry)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def selection(self) -> QRect:
        if self.start is None or self.end is None:
            return QRect()
        return QRect(self.start, self.end).normalized().intersected(self.rect())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.drawPixmap(self.rect(), self.screenshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 105))
        selected = self.selection()
        if not selected.isEmpty():
            painter.drawPixmap(selected, self.screenshot, selected)
            painter.setPen(QPen(QColor("#35a7ff"), 2))
            painter.drawRect(selected.adjusted(0, 0, -1, -1))
            label = f"{selected.width()} × {selected.height()}"
            label_box = QRect(selected.left(), max(0, selected.top() - 30), 130, 25)
            painter.fillRect(label_box, QColor(18, 35, 55, 220))
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(label_box, Qt.AlignmentFlag.AlignCenter, label)
        elif self.start is None:
            painter.setPen(Qt.GlobalColor.white)
            painter.drawText(
                self.rect(),
                Qt.AlignmentFlag.AlignCenter,
                "اسحب لتحديد النص  •  Drag to select text  •  Esc للإلغاء",
            )
        painter.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or self.start is None:
            return
        self.end = event.position().toPoint()
        selected = self.selection()
        if selected.width() < 8 or selected.height() < 8:
            self.start = None
            self.end = None
            self.update()
            return
        crop = self.screenshot.copy(selected)
        self.hide()
        self.captured.emit(pixmap_to_pil(crop))
        self.deleteLater()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.cancelled.emit()
            self.deleteLater()
            return
        super().keyPressEvent(event)
