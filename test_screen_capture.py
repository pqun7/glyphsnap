"""DPI and multi-monitor coordinate regression tests."""

from __future__ import annotations

import ctypes
import time
import unittest

from PySide6.QtCore import QRect
from PySide6.QtGui import QColor, QImage, QPainter

from PySide6.QtWidgets import QApplication

from screen_capture import DesktopCapture, GlobalHotkey, HOTKEY_ID, ScreenFrame, WM_HOTKEY


def solid_image(width: int, height: int, color: str) -> QImage:
    image = QImage(width, height, QImage.Format.Format_RGB888)
    image.fill(QColor(color))
    return image


class ScreenCaptureMappingTests(unittest.TestCase):
    def test_high_dpi_crop_keeps_physical_pixels(self) -> None:
        image = solid_image(200, 200, "red")
        painter = QPainter(image)
        painter.fillRect(QRect(20, 20, 40, 40), QColor("blue"))
        painter.end()
        capture = DesktopCapture(
            QRect(0, 0, 100, 100),
            None,
            (ScreenFrame(QRect(0, 0, 100, 100), image),),
        )
        crop = capture.crop(QRect(10, 10, 20, 20))
        self.assertEqual(crop.size, (40, 40))
        self.assertEqual(crop.getpixel((20, 20)), (0, 0, 255))

    def test_mixed_dpi_monitors_map_into_one_native_crop(self) -> None:
        frames = (
            ScreenFrame(QRect(-100, 0, 100, 100), solid_image(100, 100, "red")),
            ScreenFrame(QRect(0, 0, 100, 100), solid_image(200, 200, "blue")),
        )
        capture = DesktopCapture(QRect(-100, 0, 200, 100), None, frames)
        crop = capture.crop(QRect(50, 10, 100, 20))
        self.assertEqual(crop.size, (200, 40))
        self.assertEqual(crop.getpixel((25, 20)), (255, 0, 0))
        self.assertEqual(crop.getpixel((175, 20)), (0, 0, 255))


@unittest.skipUnless(hasattr(ctypes, "windll"), "Windows-only global hotkey test")
class GlobalHotkeyTests(unittest.TestCase):
    def test_register_dispatch_and_idempotent_shutdown(self) -> None:
        app = QApplication.instance() or QApplication([])
        activations: list[bool] = []
        hotkey = GlobalHotkey(lambda: activations.append(True))
        if not hotkey.register():
            self.skipTest(f"Ctrl+Shift+O is already registered (error {hotkey.error_code})")
        thread_id = hotkey._thread_id
        self.assertTrue(hotkey.register())
        self.assertEqual(hotkey._thread_id, thread_id)
        ctypes.windll.user32.PostThreadMessageW(thread_id, WM_HOTKEY, HOTKEY_ID, 0)
        deadline = time.monotonic() + 2.0
        while not activations and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.01)
        self.assertEqual(len(activations), 1)
        hotkey.close()
        hotkey.close()
        self.assertFalse(hotkey.registered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
