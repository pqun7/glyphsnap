"""Fast regression tests and a reproducible synthetic OCR accuracy check."""

from __future__ import annotations

import unittest
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ocr_engine import deskew, installed_languages, locate_tesseract, preprocess_variants, recognize


def normalized(value: str) -> str:
    return " ".join(value.lower().split())


class OCRPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.command = locate_tesseract()
        cls.languages = installed_languages(cls.command)

    def test_preprocessing_returns_source_and_enhanced_variants(self) -> None:
        image = Image.new("RGB", (640, 220), "white")
        ImageDraw.Draw(image).text((35, 70), "Newspaper archive 1938", fill="black")
        variants = preprocess_variants(image)
        self.assertEqual(
            [name for name, _ in variants],
            ["original", "scaled-deskewed", "contrast", "adaptive"],
        )
        self.assertTrue(all(result.width >= image.width for _, result in variants))

    def test_deskew_corrects_a_small_rotation(self) -> None:
        canvas = np.full((500, 900, 3), 255, dtype=np.uint8)
        for y in range(100, 420, 55):
            cv2.line(canvas, (100, y), (800, y), (0, 0, 0), 8)
        matrix = cv2.getRotationMatrix2D((450, 250), 5.0, 1.0)
        rotated = cv2.warpAffine(canvas, matrix, (900, 500), borderValue=(255, 255, 255))
        _, angle = deskew(rotated)
        self.assertGreater(abs(angle), 3.0)
        self.assertLess(abs(angle), 7.0)

    def test_english_ocr_accuracy_on_degraded_synthetic_scan(self) -> None:
        if not self.command or "eng" not in self.languages:
            self.skipTest("Tesseract English language data is unavailable")
        font_path = Path(r"C:\Windows\Fonts\arial.ttf")
        font = ImageFont.truetype(str(font_path), 44) if font_path.is_file() else ImageFont.load_default()
        expected = "Historical newspaper archive 1938"
        image = Image.new("L", (1050, 230), 232)
        ImageDraw.Draw(image).text((45, 72), expected, font=font, fill=32)
        array = np.asarray(image)
        noise = np.random.default_rng(42).normal(0, 11, array.shape)
        degraded = Image.fromarray(np.clip(array.astype(float) + noise, 0, 255).astype(np.uint8))
        result = recognize(degraded.convert("RGB"), self.command, ["eng"], 6, preprocess=True)
        actual = normalized(result.text)
        self.assertIn("historical newspaper", actual)
        self.assertIn("1938", actual)
        self.assertGreater(result.confidence, 60.0)

    def test_arabic_ocr_on_archival_newspaper_sample(self) -> None:
        if not self.command or "ara" not in self.languages:
            self.skipTest("Tesseract Arabic language data is unavailable")
        sample_path = Path(__file__).resolve().parent / "tests" / "8.jpg"
        if not sample_path.is_file():
            self.skipTest("Arabic regression sample is unavailable")
        result = recognize(Image.open(sample_path), self.command, ["ara", "eng"], 3)
        self.assertIn("يساعد الناس", result.text)
        self.assertIn("Helping others", result.text)
        self.assertGreater(result.confidence, 60.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
