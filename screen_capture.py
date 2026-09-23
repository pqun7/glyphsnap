"""Native-resolution multi-monitor capture and a reliable Windows hotkey."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import math
import threading

from PIL import Image
from PySide6.QtCore import QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QImage, QKeyEvent, QMouseEvent, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QWidget


HOTKEY_ID = 0xA0C1
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000


@dataclass(frozen=True)
class ScreenFrame:
    """One monitor: logical Qt geometry plus its untouched physical pixels."""

    logical_geometry: QRect
    image: QImage

    @property
    def scale_x(self) -> float:
        return self.image.width() / max(1, self.logical_geometry.width())

    @property
    def scale_y(self) -> float:
        return self.image.height() / max(1, self.logical_geometry.height())


@dataclass(frozen=True)
class DesktopCapture:
    logical_geometry: QRect
    preview: QPixmap | None
    frames: tuple[ScreenFrame, ...]

    def crop(self, local_selection: QRect) -> Image.Image:
        selection = local_selection.normalized().intersected(
            QRect(QPoint(0, 0), self.logical_geometry.size())
        )
        if selection.width() < 1 or selection.height() < 1:
            raise ValueError("The screen selection is empty.")
        global_selection = selection.translated(self.logical_geometry.topLeft())
        pieces: list[tuple[QRect, Image.Image, ScreenFrame]] = []
        for frame in self.frames:
            intersection = global_selection.intersected(frame.logical_geometry)
            if intersection.isEmpty():
                continue
            relative = intersection.translated(-frame.logical_geometry.topLeft())
            left = math.floor(relative.left() * frame.scale_x)
            top = math.floor(relative.top() * frame.scale_y)
            right = math.ceil((relative.right() + 1) * frame.scale_x)
            bottom = math.ceil((relative.bottom() + 1) * frame.scale_y)
            native_rect = QRect(left, top, right - left, bottom - top).intersected(frame.image.rect())
            if native_rect.isEmpty():
                continue
            pieces.append((intersection, qimage_to_pil(frame.image.copy(native_rect)), frame))
        if not pieces:
            raise RuntimeError("The selected region did not intersect an available screen.")

        target_scale_x = max(frame.scale_x for _, _, frame in pieces)
        target_scale_y = max(frame.scale_y for _, _, frame in pieces)
        output = Image.new(
            "RGB",
            (
                max(1, round(global_selection.width() * target_scale_x)),
                max(1, round(global_selection.height() * target_scale_y)),
            ),
            "white",
        )
        for intersection, piece, _frame in pieces:
            target_size = (
                max(1, round(intersection.width() * target_scale_x)),
                max(1, round(intersection.height() * target_scale_y)),
            )
            if piece.size != target_size:
                piece = piece.resize(target_size, Image.Resampling.LANCZOS)
            offset = (
                round((intersection.left() - global_selection.left()) * target_scale_x),
                round((intersection.top() - global_selection.top()) * target_scale_y),
            )
            output.paste(piece, offset)
        return output


def qimage_to_pil(image: QImage) -> Image.Image:
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    raw = bytes(converted.bits()[: converted.sizeInBytes()])
    return Image.frombuffer(
        "RGBA",
        (converted.width(), converted.height()),
        raw,
        "raw",
        "RGBA",
        converted.bytesPerLine(),
        1,
    ).copy().convert("RGB")


def capture_virtual_desktop() -> DesktopCapture:
    screens = QApplication.screens()
    if not screens:
        raise RuntimeError("No screen is available for capture.")
    geometry = screens[0].geometry()
    for screen in screens[1:]:
        geometry = geometry.united(screen.geometry())

    preview = QPixmap(geometry.size())
    preview.fill(Qt.GlobalColor.transparent)
    painter = QPainter(preview)
    frames: list[ScreenFrame] = []
    for screen in screens:
        shot = screen.grabWindow(0)
        raw_image = shot.toImage().copy()
        frames.append(ScreenFrame(QRect(screen.geometry()), raw_image))
        target = QRect(screen.geometry().topLeft() - geometry.topLeft(), screen.geometry().size())
        # Only the overlay preview is scaled to logical coordinates. OCR crops are
        # taken later from raw_image, which retains every physical screen pixel.
        painter.drawPixmap(target, shot, shot.rect())
    painter.end()
    return DesktopCapture(QRect(geometry), preview, tuple(frames))


class GlobalHotkey(QObject):
    """Own RegisterHotKey and its Windows message loop on one dedicated thread."""

    activated = Signal()

    def __init__(self, callback=None, parent: QObject | None = None) -> None:
        super().__init__(parent)
        if callback is not None:
            self.activated.connect(callback)
        self.registered = False
        self.error_code = 0
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._lock = threading.Lock()

    def register(self) -> bool:
        if not hasattr(ctypes, "windll"):
            return False
        with self._lock:
            if self._thread and self._thread.is_alive():
                return self.registered
            self._ready.clear()
            self._thread = threading.Thread(target=self._message_loop, daemon=True)
            self._thread.start()
        self._ready.wait(timeout=3.0)
        return self.registered

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = int(kernel32.GetCurrentThreadId())
        registered = bool(
            user32.RegisterHotKey(
                None,
                HOTKEY_ID,
                MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT,
                ord("O"),
            )
        )
        self.registered = registered
        self.error_code = 0 if registered else int(kernel32.GetLastError())
        self._ready.set()
        if not registered:
            return
        message = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == WM_HOTKEY and message.wParam == HOTKEY_ID:
                    self.activated.emit()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False

    def close(self) -> None:
        with self._lock:
            thread = self._thread
            thread_id = self._thread_id
        if thread and thread.is_alive() and thread_id and hasattr(ctypes, "windll"):
            ctypes.windll.user32.PostThreadMessageW(thread_id, WM_QUIT, 0, 0)
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
            self._thread_id = 0
            self.registered = False


class ScreenRegionSelector(QWidget):
    captured = Signal(object)
    cancelled = Signal()

    def __init__(self, capture: DesktopCapture) -> None:
        super().__init__(None)
        self.capture = capture
        self.start: QPoint | None = None
        self.end: QPoint | None = None
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setGeometry(capture.logical_geometry)
        self.setCursor(QCursor(Qt.CursorShape.CrossCursor))
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def selection(self) -> QRect:
        if self.start is None or self.end is None:
            return QRect()
        return QRect(self.start, self.end).normalized().intersected(self.rect())

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        if self.capture.preview is not None:
            painter.drawPixmap(self.rect(), self.capture.preview)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 105))
        selected = self.selection()
        if not selected.isEmpty():
            if self.capture.preview is not None:
                painter.drawPixmap(selected, self.capture.preview, selected)
            painter.setPen(QPen(QColor("#35a7ff"), 2))
            painter.drawRect(selected.adjusted(0, 0, -1, -1))
            label = f"{selected.width()} × {selected.height()}"
            label_box = QRect(selected.left(), max(0, selected.top() - 30), 150, 25)
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
        image = self.capture.crop(selected)
        self.hide()
        self.captured.emit(image)
        self.deleteLater()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            self.cancelled.emit()
            self.deleteLater()
            return
        super().keyPressEvent(event)
