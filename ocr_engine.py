"""Reusable, testable OCR and image-preprocessing pipeline.

The desktop UI intentionally delegates OCR to this module so preprocessing,
language selection, confidence scoring, and tests do not depend on Qt.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess

import cv2
import numpy as np
import pytesseract
from PIL import Image


LANGUAGE_LABELS = {
    "ara": "العربية — Arabic",
    "eng": "English — الإنجليزية",
    "fra": "Français — الفرنسية",
    "deu": "Deutsch — الألمانية",
    "spa": "Español — الإسبانية",
    "tur": "Türkçe — التركية",
    "fas": "فارسی — Persian",
    "urd": "اردو — Urdu",
    "heb": "עברית — Hebrew",
}


@dataclass(frozen=True)
class OCRResult:
    text: str
    confidence: float
    variant: str
    languages: tuple[str, ...]


def locate_tesseract() -> str | None:
    candidates = (
        shutil.which("tesseract"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    )
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(candidate)
    return None


def installed_languages(command: str | None) -> list[str]:
    if not command:
        return []
    try:
        completed = subprocess.run(
            [command, "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    languages = []
    for line in completed.stdout.splitlines():
        value = line.strip()
        if value and not value.lower().startswith("list of available") and "\\" not in value:
            languages.append(value)
    return sorted(set(languages))


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([،؛؟,:.!])", r"\1", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    rgb = np.asarray(image.convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def _bgr_to_pil(image: np.ndarray) -> Image.Image:
    if image.ndim == 2:
        return Image.fromarray(image)
    return Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))


def estimate_skew(gray: np.ndarray) -> float:
    """Estimate a conservative document skew angle in degrees."""
    inverted = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 3))
    joined = cv2.morphologyEx(inverted, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(joined, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    angles: list[float] = []
    min_area = max(80.0, gray.shape[0] * gray.shape[1] * 0.00002)
    for contour in contours:
        if cv2.contourArea(contour) < min_area:
            continue
        _, (width, height), angle = cv2.minAreaRect(contour)
        if width < height:
            angle += 90.0
        while angle > 45.0:
            angle -= 90.0
        while angle < -45.0:
            angle += 90.0
        if abs(angle) <= 15.0:
            angles.append(angle)
    return float(np.median(angles)) if angles else 0.0


def deskew(image: np.ndarray) -> tuple[np.ndarray, float]:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    angle = estimate_skew(gray)
    if abs(angle) < 0.15:
        return image, 0.0
    height, width = gray.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        image,
        matrix,
        (width, height),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255) if image.ndim == 3 else 255,
    )
    return rotated, angle


def preprocess_variants(image: Image.Image) -> list[tuple[str, Image.Image]]:
    """Create restrained variants suited to clean and degraded documents."""
    bgr = _pil_to_bgr(image)
    height, width = bgr.shape[:2]
    longest = max(height, width)
    if longest < 1800:
        scale = min(3.0, 1800.0 / max(longest, 1))
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    corrected, _ = deskew(bgr)
    gray = cv2.cvtColor(corrected, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, h=7, templateWindowSize=7, searchWindowSize=21)
    contrast = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8)).apply(gray)
    binary = cv2.adaptiveThreshold(
        contrast,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        35,
        13,
    )
    return [
        ("deskewed", _bgr_to_pil(corrected)),
        ("contrast", _bgr_to_pil(contrast)),
        ("adaptive", _bgr_to_pil(binary)),
    ]


def _text_from_data(data: dict) -> tuple[str, float]:
    lines: list[str] = []
    current_key = None
    current_words: list[str] = []
    confidences: list[float] = []

    count = len(data.get("text", []))
    for index in range(count):
        word = str(data["text"][index]).strip()
        key = (
            data.get("page_num", [0] * count)[index],
            data.get("block_num", [0] * count)[index],
            data.get("par_num", [0] * count)[index],
            data.get("line_num", [0] * count)[index],
        )
        if current_key is not None and key != current_key and current_words:
            lines.append(" ".join(current_words))
            current_words = []
        current_key = key
        if not word:
            continue
        current_words.append(word)
        try:
            confidence = float(data.get("conf", [-1] * count)[index])
        except (TypeError, ValueError):
            confidence = -1
        if confidence >= 0:
            confidences.append(confidence)
    if current_words:
        lines.append(" ".join(current_words))
    average = float(np.mean(confidences)) if confidences else 0.0
    return clean_text("\n".join(lines)), average


def recognize(
    image: Image.Image,
    command: str,
    languages: list[str] | tuple[str, ...],
    psm: int,
    *,
    preprocess: bool = True,
) -> OCRResult:
    selected = tuple(dict.fromkeys(language for language in languages if language))
    if not selected:
        raise ValueError("Select at least one installed OCR language.")
    pytesseract.pytesseract.tesseract_cmd = command
    variants = preprocess_variants(image) if preprocess else [("original", image.convert("RGB"))]
    best = OCRResult("", 0.0, variants[0][0], selected)
    config = f"--oem 1 --psm {psm} -c preserve_interword_spaces=1"
    for name, variant in variants:
        data = pytesseract.image_to_data(
            variant,
            lang="+".join(selected),
            config=config,
            output_type=pytesseract.Output.DICT,
        )
        text, confidence = _text_from_data(data)
        candidate = OCRResult(text, confidence, name, selected)
        # Confidence leads; text coverage breaks near-ties caused by sparse scans.
        if (
            candidate.confidence > best.confidence + 1.0
            or (abs(candidate.confidence - best.confidence) <= 1.0 and len(candidate.text) > len(best.text))
        ):
            best = candidate
    return best
