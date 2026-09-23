"""Reusable, testable OCR and image-preprocessing pipeline.

The desktop UI intentionally delegates OCR to this module so preprocessing,
language selection, confidence scoring, and tests do not depend on Qt.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    psm: int = 3
    score: float = 0.0
    candidates_evaluated: int = 1


@dataclass(frozen=True)
class _OCRCandidate:
    text: str
    confidence: float
    variant: str
    psm: int
    score: float


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
    """Create lossless-source and enhanced candidates without committing to one filter."""
    original = image.convert("RGB")
    bgr = _pil_to_bgr(image)
    height, width = bgr.shape[:2]
    longest = max(height, width)
    if longest < 1800:
        scale = min(3.0, 1800.0 / max(longest, 1))
        bgr = cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)

    corrected, angle = deskew(bgr)
    gray = cv2.cvtColor(corrected, cv2.COLOR_BGR2GRAY)
    # Denoising is applied only to derived candidates. The untouched original is
    # always evaluated as well, so fine Arabic dots cannot be lost globally.
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
    variants = [("original", original)]
    if abs(angle) >= 0.15 or corrected.shape[1] != image.width or corrected.shape[0] != image.height:
        variants.append(("scaled-deskewed", _bgr_to_pil(corrected)))
    variants.extend([
        ("contrast", _bgr_to_pil(contrast)),
        ("adaptive", _bgr_to_pil(binary)),
    ])
    return variants


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


def _script_compatibility(text: str, languages: tuple[str, ...]) -> float:
    letters = [character for character in text if character.isalpha()]
    if not letters:
        return 0.0
    accepts_arabic = any(language in {"ara", "fas", "urd"} for language in languages)
    accepts_latin = "eng" in languages or any(
        language in {"fra", "deu", "spa", "tur"} for language in languages
    )
    if not accepts_arabic and not accepts_latin:
        return 1.0
    compatible = 0
    for character in letters:
        is_arabic = "\u0600" <= character <= "\u06ff" or "\u0750" <= character <= "\u08ff"
        is_latin = "A" <= character <= "Z" or "a" <= character <= "z"
        if (is_arabic and accepts_arabic) or (is_latin and accepts_latin):
            compatible += 1
    return compatible / len(letters)


def _candidate_from_data(
    data: dict,
    variant: str,
    psm: int,
    languages: tuple[str, ...],
) -> _OCRCandidate:
    text, mean_confidence = _text_from_data(data)
    weighted_total = 0.0
    character_total = 0
    for word, raw_confidence in zip(data.get("text", []), data.get("conf", [])):
        word = str(word).strip()
        if not word:
            continue
        try:
            confidence = float(raw_confidence)
        except (TypeError, ValueError):
            continue
        if confidence < 0:
            continue
        weight = max(1, sum(character.isalnum() for character in word))
        weighted_total += confidence * weight
        character_total += weight
    confidence = weighted_total / character_total if character_total else mean_confidence
    evidence = sum(character.isalnum() for character in text)
    compatibility = _script_compatibility(text, languages)
    # Tesseract's mean confidence alone rewards short partial results. Weighting
    # it by recognized evidence makes a complete block beat a high-confidence
    # fragment, while script compatibility suppresses wrong-language gibberish.
    # Confidence is intentionally nonlinear. Sparse segmentation modes can emit
    # more characters by accepting noise; requiring strong per-character evidence
    # prevents those longer but less reliable candidates from winning.
    source_prior = 1.03 if variant == "original" else 1.0
    score = (
        ((confidence / 100.0) ** 4)
        * evidence
        * (0.55 + 0.45 * compatibility)
        * source_prior
    )
    return _OCRCandidate(text, confidence, variant, psm, score)


def _run_candidate(
    item: tuple[str, Image.Image, int],
    command: str,
    languages: tuple[str, ...],
) -> _OCRCandidate:
    name, variant, psm = item
    data = pytesseract.image_to_data(
        variant,
        lang="+".join(languages),
        config=f"--oem 1 --psm {psm} -c preserve_interword_spaces=1 -c user_defined_dpi=300",
        output_type=pytesseract.Output.DICT,
    )
    return _candidate_from_data(data, name, psm, languages)


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
    primary_jobs = [(name, variant, psm) for name, variant in variants]
    workers = min(3, len(primary_jobs))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        candidates = list(
            executor.map(lambda item: _run_candidate(item, command, selected), primary_jobs)
        )

    if preprocess and candidates:
        ranked_names = []
        for candidate in sorted(candidates, key=lambda value: value.score, reverse=True):
            if candidate.variant not in ranked_names:
                ranked_names.append(candidate.variant)
        variant_lookup = dict(variants)
        alternate_jobs: list[tuple[str, Image.Image, int]] = []
        alternate_psm = 6 if psm != 6 else 3
        for name in ranked_names[:2]:
            alternate_jobs.append((name, variant_lookup[name], alternate_psm))
        with ThreadPoolExecutor(max_workers=min(3, len(alternate_jobs))) as executor:
            candidates.extend(
                executor.map(lambda item: _run_candidate(item, command, selected), alternate_jobs)
            )

    best = max(candidates, key=lambda value: value.score, default=_OCRCandidate("", 0.0, "original", psm, 0.0))
    return OCRResult(
        best.text,
        best.confidence,
        best.variant,
        selected,
        psm=best.psm,
        score=best.score,
        candidates_evaluated=len(candidates),
    )
