#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Arabic Historical Newspaper OCR Pro v2
=======================================

Recall-first OCR pipeline for difficult Arabic newspaper scans.

Main fixes versus v1
--------------------
1. NEVER build the final document only from PP-StructureV3's
   parsing_res_list text. That caused detected OCR lines that were not
   inside a layout block to disappear from TXT/DOCX.
2. Run an independent PP-OCRv5 full-page OCR pass to recover text that
   the document-layout pass misses.
3. Merge the two OCR passes by geometry, preserving one canonical line
   plus alternatives for audit.
4. Map every canonical OCR line back to a layout block using IoU,
   line containment, and an expanded-block fallback.
5. Rebuild every block from OCR lines when lines are available. The
   original block_content is used only as a last-resort fallback.
6. Explicitly account for orphan lines. They are NEVER silently
   discarded; they are placed into an "orphan" synthetic region and
   included in the final text.
7. Tesseract verification is triggered not only by low confidence but
   also by disagreement between OCR engines.
8. The final TXT/DOCX includes structured page boundaries and paragraph
   separation while preserving Arabic spelling exactly as recognized.
9. Coverage diagnostics are written to JSON so you can prove that every
   canonical OCR line was either mapped or included as an orphan.

This is transcription, not modernization. No linguistic normalization
or LLM guessing is performed.

Current PaddleOCR 3.x supports a standalone PaddleOCR OCR pipeline
(text detection + recognition) in addition to PP-StructureV3. The v2
pipeline intentionally uses both because layout parsing and text recall
are different failure modes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

import cv2
import pymupdf
import numpy as np


# ============================================================
# DEFAULT CONFIGURATION
# ============================================================

DEFAULT_DPI = 420
DEFAULT_DEVICE = "cpu"
DEFAULT_OUTPUT_DIR = "arabic_newspaper_ocr_output"

PADDLE_LANG = "ar"
TESSERACT_LANG = "ara"

# PP-StructureV3 / PaddleOCR confidence thresholds.
VERIFY_SCORE = 0.90
DISAGREEMENT_SIMILARITY = 0.72
TESSERACT_MARGIN = 0.08
MIN_TESSERACT_SCORE = 52.0
MIN_TEXT_LENGTH_FOR_VERIFICATION = 3

# Recall pass thresholds.
RECALL_MIN_SCORE = 0.35
LINE_MERGE_IOU = 0.45
LINE_MERGE_CENTER_FACTOR = 0.65

# Block assignment thresholds.
BLOCK_LINE_AREA_OVERLAP = 0.18
BLOCK_EXPANSION_FACTOR = 0.08


# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class OCRCandidate:
    text: str
    score: float
    bbox: list[int]
    source: str


@dataclass
class OCRLine:
    text: str
    paddle_score: float
    recall_score: float | None
    tesseract_text: str | None
    tesseract_score: float | None
    final_text: str
    used_tesseract: bool
    bbox: list[int]
    review_required: bool
    source: str
    alternatives: list[str] = field(default_factory=list)
    block_id: Any = None
    block_order: Any = None
    orphan: bool = False


@dataclass
class TextRegion:
    region_id: str
    order_hint: float
    bbox: list[int]
    label: str
    text: str
    lines: list[OCRLine] = field(default_factory=list)
    synthetic: bool = False


@dataclass
class OCRParagraph:
    """A paragraph reconstructed from neighbouring OCR lines."""

    text: str = ""
    bbox: list[int] = field(default_factory=list)
    lines: list[OCRLine] = field(default_factory=list)
    is_heading: bool = False
    heading_score: float = 0.0
    boundary_score: float = 0.0
    confidence: float = 0.0
    paragraph_id: str = ""


@dataclass
class OCRBlock:
    """A layout region after line-to-paragraph reconstruction."""

    block_id: str
    bbox: list[int] = field(default_factory=list)
    label: str = "text"
    text: str = ""
    lines: list[OCRLine] = field(default_factory=list)
    paragraphs: list[OCRParagraph] = field(default_factory=list)
    order_hint: float = 10_000.0
    column_index: int | None = None
    confidence: float = 0.0

    @property
    def region_id(self) -> str:
        return self.block_id


@dataclass
class OCRColumn:
    """A right-to-left newspaper column containing ordered OCR blocks."""

    column_id: int
    bbox: list[int] = field(default_factory=list)
    blocks: list[OCRBlock] = field(default_factory=list)
    paragraphs: list[OCRParagraph] = field(default_factory=list)
    text: str = ""
    confidence: float = 0.0

    @property
    def index(self) -> int:
        return self.column_id


@dataclass
class PageResult:
    page_number: int
    text: str
    blocks: list[dict[str, Any]]
    lines: list[dict[str, Any]]
    low_confidence_count: int
    tesseract_used_count: int
    detected_line_count: int
    included_line_count: int
    orphan_line_count: int
    regions: list[dict[str, Any]] = field(default_factory=list)
    columns: list[OCRColumn] = field(default_factory=list)
    paragraphs: list[OCRParagraph] = field(default_factory=list)
    image_width: int = 0
    image_height: int = 0


# ============================================================
# TEXT UTILITIES
# ============================================================

ARABIC_RANGES = (
    ("\u0600", "\u06ff"),
    ("\u0750", "\u077f"),
    ("\u08a0", "\u08ff"),
    ("\ufb50", "\ufdff"),
    ("\ufe70", "\ufeff"),
)


def has_arabic(text: str) -> bool:
    return any(start <= ch <= end for ch in text for start, end in ARABIC_RANGES)


def clean_text_conservative(text: str) -> str:
    """Formatting-only cleanup. No linguistic correction."""
    if not text:
        return ""

    text = str(text).replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\ufeff", "").replace("\u200b", "")
    # Keep ZWNJ/ZWJ rather than accidentally converting one into the other.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\s+([،؛؟,:.!])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_for_comparison(text: str) -> str:
    text = clean_text_conservative(text)
    return re.sub(r"\s+", "", text)


def text_similarity(a: str, b: str) -> float:
    aa = normalize_for_comparison(a)
    bb = normalize_for_comparison(b)
    if not aa and not bb:
        return 1.0
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


def plausible_ocr_text(text: str) -> bool:
    text = clean_text_conservative(text)
    if len(text) < 2:
        return False
    meaningful = sum(1 for ch in text if ch.isalnum() or has_arabic(ch))
    return meaningful / max(len(text), 1) >= 0.30


# ============================================================
# GEOMETRY
# ============================================================


def bbox_area(b: Iterable[float]) -> float:
    vals = list(b)
    if len(vals) < 4:
        return 0.0
    return max(0.0, vals[2] - vals[0]) * max(0.0, vals[3] - vals[1])


def bbox_iou(a: Iterable[float], b: Iterable[float]) -> float:
    ax1, ay1, ax2, ay2 = map(float, list(a)[:4])
    bx1, by1, bx2, by2 = map(float, list(b)[:4])
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    union = bbox_area([ax1, ay1, ax2, ay2]) + bbox_area([bx1, by1, bx2, by2]) - inter
    return inter / max(union, 1.0)


def intersection_over_line(a: Iterable[float], region: Iterable[float]) -> float:
    ax1, ay1, ax2, ay2 = map(float, list(a)[:4])
    bx1, by1, bx2, by2 = map(float, list(region)[:4])
    inter = max(0.0, min(ax2, bx2) - max(ax1, bx1)) * max(0.0, min(ay2, by2) - max(ay1, by1))
    return inter / max(bbox_area([ax1, ay1, ax2, ay2]), 1.0)


def point_in_expanded_bbox(point: tuple[float, float], box: Iterable[float], factor: float) -> bool:
    x, y = point
    x1, y1, x2, y2 = map(float, list(box)[:4])
    pad_x = max((x2 - x1) * factor, 8.0)
    pad_y = max((y2 - y1) * factor, 8.0)
    return (x1 - pad_x) <= x <= (x2 + pad_x) and (y1 - pad_y) <= y <= (y2 + pad_y)


def bbox_center(b: Iterable[float]) -> tuple[float, float]:
    x1, y1, x2, y2 = map(float, list(b)[:4])
    return ((x1 + x2) / 2.0, (y1 + y2) / 2.0)


def crop_bbox(image: np.ndarray, bbox: Iterable[float], padding: int = 14) -> np.ndarray:
    arr = np.asarray(bbox, dtype=float)
    if arr.ndim == 2:
        x1, y1 = np.floor(arr[:, 0].min()), np.floor(arr[:, 1].min())
        x2, y2 = np.ceil(arr[:, 0].max()), np.ceil(arr[:, 1].max())
    else:
        x1, y1, x2, y2 = arr[:4]

    h, w = image.shape[:2]
    x1 = max(0, int(math.floor(x1)) - padding)
    y1 = max(0, int(math.floor(y1)) - padding)
    x2 = min(w, int(math.ceil(x2)) + padding)
    y2 = min(h, int(math.ceil(y2)) + padding)
    if x2 <= x1 or y2 <= y1:
        return np.empty((0, 0, 3), dtype=np.uint8)
    return image[y1:y2, x1:x2]


# ============================================================
# TESSERACT
# ============================================================


def locate_tesseract() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def check_tesseract(tesseract_cmd: str | None) -> bool:
    if not tesseract_cmd:
        print("[INFO] Tesseract not found; verification disabled.")
        return False
    try:
        proc = subprocess.run(
            [tesseract_cmd, "--list-langs"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except Exception as exc:
        print(f"[WARNING] Could not query Tesseract: {exc}")
        return False
    langs = proc.stdout + proc.stderr
    if TESSERACT_LANG not in langs:
        print("[WARNING] Tesseract Arabic 'ara' language data not detected; verification disabled.")
        return False
    print(f"[OK] Tesseract Arabic language detected: {TESSERACT_LANG}")
    return True


def _tesseract_once(image: np.ndarray, tesseract_cmd: str, psm: int) -> tuple[str, float]:
    import pytesseract
    from PIL import Image

    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil = Image.fromarray(rgb)
    data = pytesseract.image_to_data(
        pil,
        lang=TESSERACT_LANG,
        config=f"--oem 1 --psm {psm} -c preserve_interword_spaces=1",
        output_type=pytesseract.Output.DICT,
    )

    words: list[str] = []
    scores: list[float] = []
    for txt, conf in zip(data.get("text", []), data.get("conf", [])):
        txt = str(txt).strip()
        try:
            conf_value = float(conf)
        except Exception:
            conf_value = -1.0
        if txt:
            words.append(txt)
            if conf_value >= 0:
                scores.append(conf_value)

    text = clean_text_conservative(" ".join(words))
    score = float(np.mean(scores)) if scores else 0.0
    return text, score


def tesseract_ocr(image: np.ndarray, tesseract_cmd: str) -> tuple[str, float]:
    """Run two Tesseract line configurations and keep the best plausible result."""
    if image.size == 0:
        return "", 0.0

    variants: list[np.ndarray] = [image]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    variants.append(cv2.cvtColor(clahe, cv2.COLOR_GRAY2BGR))
    try:
        otsu = cv2.threshold(clahe, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
        variants.append(cv2.cvtColor(otsu, cv2.COLOR_GRAY2BGR))
    except Exception:
        pass

    best_text = ""
    best_score = -1.0
    for variant in variants:
        for psm in (7, 13):
            try:
                text, score = _tesseract_once(variant, tesseract_cmd, psm)
            except Exception:
                continue
            if not plausible_ocr_text(text):
                continue
            if score > best_score or (abs(score - best_score) < 2 and len(text) > len(best_text)):
                best_text, best_score = text, score

    return best_text, max(0.0, best_score)


# ============================================================
# IMAGE PROCESSING
# ============================================================


def render_pdf_page(page: pymupdf.Page, dpi: int) -> np.ndarray:
    scale = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
    image = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(image, cv2.COLOR_RGB2BGR)


def mild_preprocess(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.fastNlMeansDenoising(gray, None, h=4, templateWindowSize=7, searchWindowSize=21)
    clahe = cv2.createCLAHE(clipLimit=1.8, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)
    return cv2.cvtColor(enhanced, cv2.COLOR_GRAY2BGR)


def upscale_line(image: np.ndarray) -> np.ndarray:
    if image.size == 0:
        return image
    h = image.shape[0]
    target_h = 96
    scale = min(4.0, target_h / max(h, 1)) if h < target_h else 1.5
    return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


# ============================================================
# PADDLE RESULT HELPERS
# ============================================================


def convert_numpy(obj: Any) -> Any:
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.generic):
        return obj.item()
    if isinstance(obj, dict):
        return {str(k): convert_numpy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [convert_numpy(v) for v in obj]
    return obj


def get_result_json(result: Any) -> dict[str, Any]:
    data = getattr(result, "json", None)
    if callable(data):
        data = data()
    if isinstance(data, dict):
        return convert_numpy(data)
    if hasattr(result, "to_json"):
        data = result.to_json()
        if isinstance(data, str):
            return json.loads(data)
        if isinstance(data, dict):
            return convert_numpy(data)
    raise RuntimeError("Could not read PaddleOCR result JSON.")


def get_res_root(data: dict[str, Any]) -> dict[str, Any]:
    return data.get("res") if isinstance(data.get("res"), dict) else data


def get_parsing_blocks(data: dict[str, Any]) -> list[dict[str, Any]]:
    blocks = get_res_root(data).get("parsing_res_list", [])
    return blocks if isinstance(blocks, list) else []


def get_overall_ocr(data: dict[str, Any]) -> dict[str, Any]:
    value = get_res_root(data).get("overall_ocr_res", {})
    return value if isinstance(value, dict) else {}


def normalize_bbox(raw_bbox: Any) -> list[int]:
    arr = np.asarray(raw_bbox, dtype=float)
    if arr.ndim == 2:
        return [
            int(math.floor(arr[:, 0].min())),
            int(math.floor(arr[:, 1].min())),
            int(math.ceil(arr[:, 0].max())),
            int(math.ceil(arr[:, 1].max())),
        ]
    vals = list(arr[:4])
    return [int(round(v)) for v in vals]


def extract_ocr_candidates_from_dict(ocr: dict[str, Any], source: str) -> list[OCRCandidate]:
    texts = ocr.get("rec_texts", ocr.get("texts", [])) or []
    scores = ocr.get("rec_scores", ocr.get("scores", [])) or []
    boxes = ocr.get("rec_boxes")
    if boxes is None:
        boxes = ocr.get("rec_polys")
    if boxes is None:
        boxes = ocr.get("dt_polys", [])

    count = min(len(texts), len(scores), len(boxes))
    out: list[OCRCandidate] = []
    for i in range(count):
        text = clean_text_conservative(str(texts[i]))
        if not text:
            continue
        try:
            score = float(scores[i])
        except Exception:
            score = 0.0
        bbox = normalize_bbox(boxes[i])
        out.append(OCRCandidate(text=text, score=score, bbox=bbox, source=source))
    return out


def extract_paddle_lines_from_structure(data: dict[str, Any]) -> list[OCRCandidate]:
    return extract_ocr_candidates_from_dict(get_overall_ocr(data), "structure")


# ============================================================
# RECALL OCR PASS
# ============================================================


def create_recall_ocr(device: str):
    from paddleocr import PaddleOCR

    return PaddleOCR(
        lang=PADDLE_LANG,
        use_doc_orientation_classify=False,
        use_doc_unwarping=False,
        use_textline_orientation=True,
        # Keep the recall pass on the same CPU-safe execution path as
        # PP-StructureV3; PaddlePaddle 3.3.x can fail in oneDNN/PIR.
        enable_mkldnn=False,
        device=device,
    )


def run_recall_ocr(recall_pipeline, image: np.ndarray) -> list[OCRCandidate]:
    results = list(recall_pipeline.predict(input=image))
    candidates: list[OCRCandidate] = []
    for result in results:
        data = get_result_json(result)
        candidates.extend(extract_ocr_candidates_from_dict(get_res_root(data), "recall"))
    return [c for c in candidates if c.score >= RECALL_MIN_SCORE and plausible_ocr_text(c.text)]


# ============================================================
# CANDIDATE MERGING
# ============================================================


def same_line_geometry(a: OCRCandidate, b: OCRCandidate) -> bool:
    iou = bbox_iou(a.bbox, b.bbox)
    if iou >= LINE_MERGE_IOU:
        return True

    ax, ay = bbox_center(a.bbox)
    bx, by = bbox_center(b.bbox)
    ah = max(1.0, a.bbox[3] - a.bbox[1])
    bh = max(1.0, b.bbox[3] - b.bbox[1])
    h = max(ah, bh)
    vertical_ok = abs(ay - by) <= LINE_MERGE_CENTER_FACTOR * h
    horizontal_overlap = max(0.0, min(a.bbox[2], b.bbox[2]) - max(a.bbox[0], b.bbox[0]))
    horizontal_ok = horizontal_overlap / max(min(a.bbox[2] - a.bbox[0], b.bbox[2] - b.bbox[0]), 1.0) >= 0.35
    return vertical_ok and horizontal_ok


def merge_ocr_candidates(structure: list[OCRCandidate], recall: list[OCRCandidate]) -> list[dict[str, Any]]:
    """Merge two OCR detections while preserving alternatives instead of dropping them."""
    merged: list[dict[str, Any]] = []

    # Structure first because it also participates in layout mapping.
    for cand in structure:
        merged.append({
            "bbox": cand.bbox,
            "text": cand.text,
            "score": cand.score,
            "structure_score": cand.score,
            "recall_score": None,
            "source": "structure",
            "alternatives": [],
            "candidates": [cand],
        })

    for cand in recall:
        matched = None
        best_overlap = 0.0
        for item in merged:
            existing = OCRCandidate(item["text"], float(item["score"]), item["bbox"], item["source"])
            if not same_line_geometry(existing, cand):
                continue
            overlap = bbox_iou(existing.bbox, cand.bbox)
            if overlap > best_overlap:
                best_overlap = overlap
                matched = item

        if matched is None:
            merged.append({
                "bbox": cand.bbox,
                "text": cand.text,
                "score": cand.score,
                "structure_score": None,
                "recall_score": cand.score,
                "source": "recall",
                "alternatives": [],
                "candidates": [cand],
            })
            continue

        matched["candidates"].append(cand)
        matched["recall_score"] = cand.score
        if clean_text_conservative(cand.text) != clean_text_conservative(matched["text"]):
            matched["alternatives"].append(cand.text)

        # Prefer the higher-confidence candidate, but on near ties prefer the longer plausible text.
        if cand.score > float(matched["score"]) + 0.015:
            matched["text"] = cand.text
            matched["score"] = cand.score
            matched["source"] = "recall+structure"
        elif abs(cand.score - float(matched["score"])) <= 0.015 and len(cand.text) > len(matched["text"]) and plausible_ocr_text(cand.text):
            matched["text"] = cand.text
            matched["score"] = cand.score
            matched["source"] = "recall+structure"

        x1 = min(int(matched["bbox"][0]), int(cand.bbox[0]))
        y1 = min(int(matched["bbox"][1]), int(cand.bbox[1]))
        x2 = max(int(matched["bbox"][2]), int(cand.bbox[2]))
        y2 = max(int(matched["bbox"][3]), int(cand.bbox[3]))
        matched["bbox"] = [x1, y1, x2, y2]

    # Stable page ordering for fallback/orphan handling.
    merged.sort(key=lambda x: (x["bbox"][1], -x["bbox"][0]))
    return merged


# ============================================================
# TESSERACT ARBITRATION
# ============================================================


def choose_final_text(
    image: np.ndarray,
    item: dict[str, Any],
    tesseract_cmd: str | None,
    tesseract_enabled: bool,
) -> OCRLine:
    paddle_text = clean_text_conservative(item["text"])
    paddle_score = float(item.get("score") or 0.0)
    recall_score = item.get("recall_score")
    alternatives = [clean_text_conservative(x) for x in item.get("alternatives", []) if clean_text_conservative(x)]

    structure_score = item.get("structure_score")
    if structure_score is not None:
        structure_score = float(structure_score)

    should_verify = (
        tesseract_enabled
        and len(paddle_text) >= MIN_TEXT_LENGTH_FOR_VERIFICATION
        and (
            paddle_score < VERIFY_SCORE
            or bool(alternatives)
            or (structure_score is not None and abs(structure_score - paddle_score) >= 0.12)
        )
    )

    tess_text = None
    tess_score = None
    used_tess = False

    if should_verify:
        crop = upscale_line(crop_bbox(image, item["bbox"], padding=18))
        if crop.size:
            try:
                tess_text, tess_score = tesseract_ocr(crop, tesseract_cmd) if tesseract_cmd else ("", 0.0)
            except Exception as exc:
                print(f"[WARNING] Tesseract verification failed: {exc}")

    final_text = paddle_text
    if tess_text and tess_score is not None and tess_score >= MIN_TESSERACT_SCORE and plausible_ocr_text(tess_text):
        similarity = text_similarity(paddle_text, tess_text)
        normalized_tess = tess_score / 100.0

        replace = False
        if paddle_score < VERIFY_SCORE and normalized_tess >= paddle_score + TESSERACT_MARGIN:
            replace = True
        elif alternatives:
            best_alt_similarity = max((text_similarity(alt, tess_text) for alt in alternatives), default=0.0)
            if normalized_tess >= 0.58 and best_alt_similarity >= 0.55 and normalized_tess >= paddle_score - 0.04:
                replace = True
        elif similarity >= DISAGREEMENT_SIMILARITY and normalized_tess > paddle_score + 0.03:
            replace = True

        if replace:
            final_text = tess_text
            used_tess = True

    review_required = (
        paddle_score < VERIFY_SCORE and not used_tess
    ) or (
        bool(alternatives) and not used_tess and len(alternatives) > 0
    )

    return OCRLine(
        text=paddle_text,
        paddle_score=paddle_score,
        recall_score=float(recall_score) if recall_score is not None else None,
        tesseract_text=tess_text,
        tesseract_score=float(tess_score) if tess_score is not None else None,
        final_text=clean_text_conservative(final_text),
        used_tesseract=used_tess,
        bbox=[int(v) for v in item["bbox"]],
        review_required=review_required,
        source=str(item.get("source", "unknown")),
        alternatives=alternatives,
    )


# ============================================================
# LAYOUT / READING ORDER
# ============================================================


def block_is_text(block: dict[str, Any]) -> bool:
    label = str(block.get("block_label", "")).lower().strip()
    text_labels = {
        "text",
        "paragraph",
        "paragraph_text",
        "paragraph_title",
        "doc_title",
        "title",
        "header",
        "footer",
        "figure_title",
        "figure_table title",
        "sidebar text",
        "abstract",
        "footnote",
        "references",
        "caption",
    }
    bbox = block.get("block_bbox", [])
    return label in text_labels and (
        bool(str(block.get("block_content", "")).strip())
        or (isinstance(bbox, (list, tuple, np.ndarray)) and len(bbox) >= 4)
    )


def normalize_block(block: dict[str, Any]) -> dict[str, Any]:
    bbox = block.get("block_bbox", [])
    try:
        bbox = normalize_bbox(bbox)
    except Exception:
        bbox = []
    return {
        "block_id": block.get("block_id"),
        "block_order": block.get("block_order", block.get("index")),
        "label": block.get("block_label"),
        "bbox": bbox,
        "text": clean_text_conservative(str(block.get("block_content", ""))),
    }


def best_block_for_line(line: OCRLine, blocks: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, float]:
    if not blocks:
        return None, 0.0

    center = bbox_center(line.bbox)
    best: dict[str, Any] | None = None
    best_score = 0.0

    for block in blocks:
        bbox = block.get("bbox", [])
        if len(bbox) < 4:
            continue
        overlap = intersection_over_line(line.bbox, bbox)
        iou = bbox_iou(line.bbox, bbox)
        center_inside = point_in_expanded_bbox(center, bbox, BLOCK_EXPANSION_FACTOR)
        score = max(overlap, iou * 1.25)
        if center_inside:
            score = max(score, 0.20)
        if score > best_score:
            best_score = score
            best = block

    if best_score >= BLOCK_LINE_AREA_OVERLAP:
        return best, best_score
    return None, best_score


def sort_lines_inside_region(lines: list[OCRLine]) -> list[OCRLine]:
    # Text lines are already recognized in word order. Arabic newspaper reading
    # order inside a paragraph is therefore simply top-to-bottom.
    return sorted(lines, key=lambda ln: (ln.bbox[1], -ln.bbox[0]))


def create_regions(blocks: list[dict[str, Any]], lines: list[OCRLine], page_shape: tuple[int, int]) -> tuple[list[TextRegion], int]:
    regions: dict[str, TextRegion] = {}
    orphan_lines: list[OCRLine] = []

    for block in blocks:
        order = block.get("block_order")
        try:
            order_hint = float(order)
        except Exception:
            order_hint = 10_000.0
        bid = str(block.get("block_id") if block.get("block_id") is not None else f"block_{len(regions):04d}")
        regions[bid] = TextRegion(
            region_id=bid,
            order_hint=order_hint,
            bbox=block.get("bbox", []),
            label=str(block.get("label") or "text"),
            text=block.get("text", ""),
            synthetic=False,
        )

    for line in lines:
        block, _score = best_block_for_line(line, blocks)
        if block is None:
            line.orphan = True
            orphan_lines.append(line)
            continue

        bid = str(block.get("block_id") if block.get("block_id") is not None else f"block_{blocks.index(block):04d}")
        if bid not in regions:
            regions[bid] = TextRegion(
                region_id=bid,
                order_hint=float(block.get("block_order") or 10_000),
                bbox=block.get("bbox", []),
                label=str(block.get("label") or "text"),
                text=block.get("text", ""),
            )
        line.block_id = block.get("block_id")
        line.block_order = block.get("block_order")
        regions[bid].lines.append(line)

    # Synthetic orphan region(s). We group nearby orphan lines so they are not
    # emitted as one paragraph per line. No orphan is discarded.
    if orphan_lines:
        page_h, page_w = page_shape
        if regions:
            # Attach an orphan region an order hint near the closest layout block.
            sorted_blocks = list(regions.values())
            for idx, line in enumerate(sort_lines_inside_region(orphan_lines), start=1):
                cx, cy = bbox_center(line.bbox)
                best_region = min(
                    sorted_blocks,
                    key=lambda r: math.hypot(
                        cx - bbox_center(r.bbox)[0],
                        cy - bbox_center(r.bbox)[1],
                    ) / max(page_w + page_h, 1),
                )
                synthetic_id = f"orphan_{idx:04d}"
                region = TextRegion(
                    region_id=synthetic_id,
                    order_hint=best_region.order_hint + 0.0001 * idx,
                    bbox=line.bbox,
                    label="orphan_text",
                    text="",
                    synthetic=True,
                    lines=[line],
                )
                regions[synthetic_id] = region
        else:
            ordered = sort_lines_inside_region(orphan_lines)
            for idx, line in enumerate(ordered, start=1):
                regions[f"orphan_{idx:04d}"] = TextRegion(
                    region_id=f"orphan_{idx:04d}",
                    order_hint=float(idx),
                    bbox=line.bbox,
                    label="orphan_text",
                    text="",
                    synthetic=True,
                    lines=[line],
                )

    return list(regions.values()), len(orphan_lines)


def _object_bbox(value: Any) -> list[int]:
    if isinstance(value, dict):
        value = value.get("bbox", [])
    bbox = getattr(value, "bbox", value)
    try:
        return [int(v) for v in list(bbox)[:4]]
    except Exception:
        return []


def _object_text(value: Any) -> str:
    if isinstance(value, dict):
        return clean_text_conservative(str(value.get("text", "")))
    return clean_text_conservative(str(getattr(value, "text", "")))


def _object_label(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("label", "") or "").lower().strip()
    return str(getattr(value, "label", "") or "").lower().strip()


def bbox_union(boxes: Iterable[Iterable[float]]) -> list[int]:
    valid: list[list[float]] = []
    for box in boxes:
        values = list(box)[:4]
        if len(values) >= 4:
            valid.append(values)
    if not valid:
        return []
    return [
        int(math.floor(min(float(box[0]) for box in valid))),
        int(math.floor(min(float(box[1]) for box in valid))),
        int(math.ceil(max(float(box[2]) for box in valid))),
        int(math.ceil(max(float(box[3]) for box in valid))),
    ]


def horizontal_overlap(a: Iterable[float], b: Iterable[float]) -> float:
    aa, bb = list(a)[:4], list(b)[:4]
    if len(aa) < 4 or len(bb) < 4:
        return 0.0
    overlap = max(0.0, min(float(aa[2]), float(bb[2])) - max(float(aa[0]), float(bb[0])))
    return overlap / max(min(float(aa[2]) - float(aa[0]), float(bb[2]) - float(bb[0])), 1.0)


def vertical_gap(upper: Iterable[float], lower: Iterable[float]) -> float:
    aa, bb = list(upper)[:4], list(lower)[:4]
    if len(aa) < 4 or len(bb) < 4:
        return float("inf")
    return max(0.0, float(bb[1]) - float(aa[3]))


def _line_height(line: OCRLine) -> float:
    bbox = _object_bbox(line)
    return max(1.0, float(bbox[3] - bbox[1])) if len(bbox) >= 4 else 1.0


def score_heading(
    value: Any,
    nearby_lines: Iterable[OCRLine] | None = None,
    label: str | None = None,
) -> float:
    """Estimate whether a line/region is a heading using layout-only evidence."""
    bbox = _object_bbox(value)
    text = _object_text(value)
    label_text = (label or _object_label(value)).lower()
    score = 0.0
    if any(token in label_text for token in ("title", "header", "heading", "caption")):
        score += 0.65
    if text and len(text) <= 90:
        score += 0.12
    if text and "\n" not in text:
        score += 0.05
    peers = list(nearby_lines or [])
    if bbox and peers:
        heights = [_line_height(line) for line in peers if _object_bbox(line)]
        if heights:
            median_height = float(np.median(heights))
            if (bbox[3] - bbox[1]) >= median_height * 1.25:
                score += 0.18
        previous = [line for line in peers if _object_bbox(line) and _object_bbox(line)[1] < bbox[1]]
        if previous:
            nearest = max(previous, key=lambda line: _object_bbox(line)[3])
            gap = vertical_gap(_object_bbox(nearest), bbox)
            if gap > _line_height(nearest) * 1.35:
                score += 0.15
    return min(1.0, score)


def heading_score(
    value: Any,
    nearby_lines: Iterable[OCRLine] | None = None,
    label: str | None = None,
) -> float:
    """Compatibility spelling for :func:`score_heading`."""
    return score_heading(value, nearby_lines, label)


def score_paragraph_boundary(
    previous: OCRLine | None,
    current: OCRLine | None,
    median_line_height: float | None = None,
) -> float:
    """Return a 0..1 score for a paragraph break between two lines."""
    if previous is None or current is None:
        return 1.0
    previous_box, current_box = _object_bbox(previous), _object_bbox(current)
    if len(previous_box) < 4 or len(current_box) < 4:
        return 0.5
    height = median_line_height or float(np.median([_line_height(previous), _line_height(current)]))
    gap_score = min(1.0, vertical_gap(previous_box, current_box) / max(height * 1.8, 1.0))
    left_shift = abs(current_box[0] - previous_box[0]) / max(height * 2.0, 1.0)
    indent_score = min(1.0, left_shift)
    return min(1.0, 0.72 * gap_score + 0.18 * indent_score + 0.10 * (
        1.0 if not has_arabic(_object_text(previous)) or not has_arabic(_object_text(current)) else 0.0
    ))


def paragraph_boundary_score(
    previous: OCRLine | None,
    current: OCRLine | None,
    median_line_height: float | None = None,
) -> float:
    """Compatibility spelling for :func:`score_paragraph_boundary`."""
    return score_paragraph_boundary(previous, current, median_line_height)


def build_region_text(region: TextRegion | OCRBlock | OCRParagraph | dict[str, Any]) -> str:
    """Build text without losing the region's OCR-line fallback."""
    lines = region.get("lines", []) if isinstance(region, dict) else getattr(region, "lines", [])
    if lines:
        ordered = sort_lines_inside_region(list(lines))
        return clean_text_conservative(" ".join(line.final_text for line in ordered if line.final_text))
    paragraphs = region.get("paragraphs", []) if isinstance(region, dict) else getattr(region, "paragraphs", [])
    if paragraphs:
        return clean_text_conservative("\n\n".join(_object_text(paragraph) for paragraph in paragraphs if _object_text(paragraph)))
    if isinstance(region, dict):
        return clean_text_conservative(str(region.get("text", "")))
    return clean_text_conservative(str(getattr(region, "text", "")))


def _paragraphs_from_lines(lines: list[OCRLine], label: str, block_id: str) -> list[OCRParagraph]:
    if not lines:
        return []
    ordered = sort_lines_inside_region(lines)
    heights = [_line_height(line) for line in ordered]
    median_height = float(np.median(heights)) if heights else 1.0
    paragraphs: list[OCRParagraph] = []
    current: list[OCRLine] = []
    current_boundary = 0.0

    def emit(items: list[OCRLine], boundary: float) -> None:
        if not items:
            return
        text = clean_text_conservative(" ".join(line.final_text for line in items if line.final_text))
        if not text:
            return
        paragraph_number = len(paragraphs) + 1
        boxes = [_object_bbox(line) for line in items]
        heading = max(score_heading(line, ordered, label) for line in items)
        confidence = float(np.mean([line.paddle_score for line in items])) if items else 0.0
        paragraphs.append(OCRParagraph(
            paragraph_id=f"{block_id}_paragraph_{paragraph_number:04d}",
            text=text,
            bbox=bbox_union(boxes),
            lines=list(items),
            is_heading=heading >= 0.68 or "title" in label or "header" in label,
            heading_score=heading,
            boundary_score=boundary,
            confidence=confidence,
        ))

    for line in ordered:
        if current:
            boundary = score_paragraph_boundary(current[-1], line, median_height)
            line_heading = score_heading(line, ordered, label)
            if boundary >= 0.52 or line_heading >= 0.68:
                emit(current, current_boundary)
                current = []
            current_boundary = boundary
        current.append(line)
    emit(current, current_boundary)
    return paragraphs


def create_ocr_blocks(
    regions: list[TextRegion] | list[dict[str, Any]],
    image_width: int = 0,
    image_height: int = 0,
) -> list[OCRBlock]:
    """Convert mapped regions into semantic blocks and reconstructed paragraphs."""
    blocks: list[OCRBlock] = []
    for index, region in enumerate(regions):
        if isinstance(region, dict):
            region_id = str(region.get("region_id", f"block_{index:04d}"))
            bbox = _object_bbox(region)
            label = str(region.get("label", "text") or "text")
            try:
                order_hint = float(region.get("order_hint", 10_000.0))
            except (TypeError, ValueError):
                order_hint = 10_000.0
            text = clean_text_conservative(str(region.get("text", "")))
            lines = list(region.get("lines", []))
        else:
            region_id = str(region.region_id)
            bbox = list(region.bbox)
            label = str(region.label or "text")
            order_hint = float(region.order_hint)
            text = clean_text_conservative(region.text)
            lines = list(region.lines)
        paragraphs = _paragraphs_from_lines(lines, label.lower(), region_id)
        if not paragraphs and text:
            fallback_heading = score_heading({"text": text, "bbox": bbox, "label": label})
            paragraphs = [OCRParagraph(
                paragraph_id=f"{region_id}_paragraph_0001",
                text=text,
                bbox=bbox,
                is_heading=fallback_heading >= 0.68,
                heading_score=fallback_heading,
            )]
        block_text = clean_text_conservative("\n\n".join(paragraph.text for paragraph in paragraphs))
        confidence = float(np.mean([paragraph.confidence for paragraph in paragraphs])) if paragraphs else 0.0
        blocks.append(OCRBlock(
            block_id=region_id,
            bbox=bbox,
            label=label,
            text=block_text or text,
            lines=lines,
            paragraphs=paragraphs,
            order_hint=order_hint,
            confidence=confidence,
        ))
    return blocks


def detect_columns(
    blocks: list[OCRBlock],
    image_width: int = 0,
    image_height: int = 0,
    rtl: bool = True,
) -> list[OCRColumn]:
    """Cluster blocks into newspaper columns, ordered right-to-left."""
    if not blocks:
        return []
    page_width = max(float(image_width), max((b.bbox[2] for b in blocks if len(b.bbox) >= 4), default=1.0))
    widths = [b.bbox[2] - b.bbox[0] for b in blocks if len(b.bbox) >= 4]
    median_width = float(np.median(widths)) if widths else page_width
    tolerance = max(10.0, median_width * 0.45, page_width * 0.012)
    clusters: list[list[OCRBlock]] = []

    for block in sorted(blocks, key=lambda item: bbox_center(item.bbox)[0] if len(item.bbox) >= 4 else 0.0, reverse=True):
        if len(block.bbox) < 4:
            target = clusters[0] if clusters else []
            if not clusters:
                clusters.append(target)
            target.append(block)
            continue
        best_cluster = None
        best_distance = float("inf")
        center_x = bbox_center(block.bbox)[0]
        for cluster in clusters:
            cluster_box = bbox_union(item.bbox for item in cluster if len(item.bbox) >= 4)
            if len(cluster_box) < 4:
                continue
            distance = max(0.0, max(cluster_box[0], block.bbox[0]) - min(cluster_box[2], block.bbox[2]))
            if horizontal_overlap(cluster_box, block.bbox) > 0.08 or distance <= tolerance:
                center_distance = abs(center_x - bbox_center(cluster_box)[0])
                if center_distance < best_distance:
                    best_cluster, best_distance = cluster, center_distance
        if best_cluster is None:
            clusters.append([block])
        else:
            best_cluster.append(block)

    def cluster_center_x(cluster: list[OCRBlock]) -> float:
        cluster_box = bbox_union(item.bbox for item in cluster if len(item.bbox) >= 4)
        return bbox_center(cluster_box)[0] if len(cluster_box) >= 4 else 0.0

    clusters.sort(key=cluster_center_x, reverse=rtl)
    columns: list[OCRColumn] = []
    for column_index, cluster in enumerate(clusters):
        ordered = sorted(cluster, key=lambda item: (
            bbox_center(item.bbox)[1] if len(item.bbox) >= 4 else 10**9,
            -bbox_center(item.bbox)[0] if len(item.bbox) >= 4 else 0.0,
        ))
        for block in ordered:
            block.column_index = column_index
        paragraphs = [paragraph for block in ordered for paragraph in block.paragraphs]
        column_text = clean_text_conservative("\n\n".join(paragraph.text for paragraph in paragraphs))
        confidence = float(np.mean([block.confidence for block in ordered])) if ordered else 0.0
        columns.append(OCRColumn(
            column_id=column_index,
            bbox=bbox_union(item.bbox for item in ordered),
            blocks=ordered,
            paragraphs=paragraphs,
            text=column_text,
            confidence=confidence,
        ))
    return columns


def sort_blocks(
    blocks: list[OCRBlock],
    rtl: bool = True,
    columns: list[OCRColumn] | None = None,
) -> list[OCRBlock]:
    """Sort blocks top-to-bottom within right-to-left (or left-to-right) columns."""
    if columns:
        order = {id(block): index for index, column in enumerate(columns) for block in column.blocks}
        return sorted(blocks, key=lambda block: (
            order.get(id(block), 10**9),
            bbox_center(block.bbox)[1] if len(block.bbox) >= 4 else 10**9,
        ))
    if any(block.column_index is not None for block in blocks):
        return sorted(blocks, key=lambda block: (
            block.column_index if block.column_index is not None else 10**9,
            bbox_center(block.bbox)[1] if len(block.bbox) >= 4 else 10**9,
        ))
    return sorted(blocks, key=lambda block: (
        bbox_center(block.bbox)[0] * (-1 if rtl else 1) if len(block.bbox) >= 4 else 0.0,
        bbox_center(block.bbox)[1] if len(block.bbox) >= 4 else 10**9,
    ))


def reconstruct_document(
    regions: list[TextRegion] | list[dict[str, Any]],
    image_width: int = 0,
    image_height: int = 0,
    rtl: bool = True,
) -> tuple[list[OCRColumn], list[OCRParagraph], str]:
    """Reconstruct columns, paragraphs, and page text exactly once."""
    blocks = create_ocr_blocks(regions, image_width, image_height)
    columns = detect_columns(blocks, image_width, image_height, rtl=rtl)
    paragraphs = [paragraph for column in columns for paragraph in column.paragraphs]
    page_text = clean_text_conservative("\n\n".join(paragraph.text for paragraph in paragraphs))
    return columns, paragraphs, page_text


def build_page_text(
    regions: list[TextRegion] | list[OCRColumn] | list[OCRBlock] | list[dict[str, Any]],
) -> str:
    """Build page text from reconstructed columns, with the legacy fallback."""
    if not regions:
        return ""
    first = regions[0]
    if isinstance(first, OCRColumn) or (isinstance(first, dict) and "blocks" in first):
        paragraphs: list[str] = []
        for column in regions:
            items = column.get("paragraphs", []) if isinstance(column, dict) else column.paragraphs
            if items:
                paragraphs.extend(_object_text(item) for item in items if _object_text(item))
            else:
                text = _object_text(column)
                if text:
                    paragraphs.append(text)
        return clean_text_conservative("\n\n".join(paragraphs))
    if isinstance(first, OCRBlock):
        return clean_text_conservative("\n\n".join(
            build_region_text(block) for block in sort_blocks(list(regions)) if build_region_text(block)
        ))
    paragraphs = []
    def legacy_order(region: Any) -> tuple[float, float]:
        raw_order = region.get("order_hint", 10_000.0) if isinstance(region, dict) else getattr(region, "order_hint", 10_000.0)
        try:
            order = float(raw_order)
        except (TypeError, ValueError):
            order = 10_000.0
        bbox = _object_bbox(region)
        return order, bbox_center(bbox)[1] if len(bbox) >= 4 else 10**9

    legacy_regions = sorted(regions, key=legacy_order)
    for region in legacy_regions:
        text = build_region_text(region)
        if text:
            paragraphs.append(text)
    return clean_text_conservative("\n\n".join(paragraphs))


# ============================================================
# DEBUG VISUALIZATION
# ============================================================


def draw_debug_page(
    image: np.ndarray,
    blocks: list[dict[str, Any]],
    lines: list[OCRLine],
) -> np.ndarray:
    canvas = image.copy()

    # Layout blocks: blue.
    for block in blocks:
        bbox = block.get("bbox", [])
        if len(bbox) < 4:
            continue
        x1, y1, x2, y2 = map(int, bbox[:4])
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (255, 0, 0), 2)
        label = str(block.get("label", ""))
        if label:
            cv2.putText(canvas, label, (x1, max(20, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 0, 0), 2, cv2.LINE_AA)

    # Every canonical line gets a box. Orphans are magenta, verified lines green,
    # unresolved review lines red.
    for line in lines:
        x1, y1, x2, y2 = map(int, line.bbox)
        if line.orphan:
            color = (255, 0, 255)
        elif line.used_tesseract:
            color = (0, 150, 0)
        elif line.review_required:
            color = (0, 0, 255)
        else:
            color = (0, 180, 180)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)

    return canvas


# ============================================================
# SAVE
# ============================================================


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(convert_numpy(data), f, ensure_ascii=False, indent=2)


def save_txt(path: Path, pages: list[PageResult]) -> None:
    with open(path, "w", encoding="utf-8-sig") as f:
        for page_index, page in enumerate(pages):
            if page_index:
                f.write("\n\f\n")
            f.write("=" * 100 + "\n")
            f.write(f"صفحة {page.page_number}\n")
            f.write("=" * 100 + "\n\n")
            text = build_page_text(page.columns) if page.columns else page.text
            f.write(text.rstrip())
            f.write("\n")


def set_rtl_paragraph(paragraph) -> None:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    paragraph.alignment = 2  # WD_ALIGN_PARAGRAPH.RIGHT
    ppr = paragraph._p.get_or_add_pPr()
    bidi = ppr.find(qn("w:bidi"))
    if bidi is None:
        bidi = OxmlElement("w:bidi")
        ppr.append(bidi)


def save_docx(path: Path, pages: list[PageResult]) -> None:
    from docx import Document
    from docx.enum.text import WD_BREAK
    from docx.shared import Pt

    document = Document()
    styles = document.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(12)

    # Metadata is explicit and neutral.
    document.core_properties.title = "Arabic Historical Newspaper OCR"
    document.core_properties.subject = "OCR transcription"

    for page_index, page in enumerate(pages):
        heading = document.add_heading(f"صفحة {page.page_number}", level=1)
        set_rtl_paragraph(heading)

        if page.columns:
            for column in page.columns:
                if isinstance(column, dict):
                    paragraphs = column.get("paragraphs", [])
                    column_text = str(column.get("text", "") or "")
                    column_id = column.get("column_id", 0)
                else:
                    paragraphs = column.paragraphs
                    column_text = column.text
                    column_id = column.column_id
                if not paragraphs and column_text:
                    paragraphs = [OCRParagraph(
                        paragraph_id=f"column_{column_id}_fallback",
                        text=column_text,
                    )]
                for paragraph in paragraphs:
                    paragraph_text = _object_text(paragraph).strip()
                    if not paragraph_text:
                        continue
                    p = document.add_paragraph()
                    set_rtl_paragraph(p)
                    p.paragraph_format.space_after = Pt(7)
                    p.paragraph_format.line_spacing = 1.05
                    run = p.add_run(paragraph_text)
                    run.bold = bool(getattr(paragraph, "is_heading", False))
                    run.font.name = "Arial"
                    run.font.size = Pt(12 if run.bold else 11.5)
        else:
            for paragraph_text in page.text.split("\n\n"):
                paragraph_text = paragraph_text.strip()
                if not paragraph_text:
                    continue
                p = document.add_paragraph()
                set_rtl_paragraph(p)
                p.paragraph_format.space_after = Pt(7)
                p.paragraph_format.line_spacing = 1.05
                run = p.add_run(paragraph_text)
                run.font.name = "Arial"
                run.font.size = Pt(11.5)

        if page_index < len(pages) - 1:
            document.add_page_break()

    document.save(path)


# ============================================================
# PIPELINES
# ============================================================


def create_structure_pipeline(device: str):
    try:
        from paddleocr import PPStructureV3
    except ImportError as exc:
        raise RuntimeError(
            "PaddleOCR is not installed. Install PaddleOCR/PaddlePaddle 3.x first."
        ) from exc

    print("\nInitializing PP-StructureV3...")
    pipeline = PPStructureV3(
        lang=PADDLE_LANG,
        use_doc_orientation_classify=True,
        use_doc_unwarping=True,
        use_textline_orientation=True,
        use_table_recognition=False,
        use_formula_recognition=False,
        use_seal_recognition=False,
        use_chart_recognition=False,
        enable_mkldnn=False,
        device=device,
    )
    print("PP-StructureV3 ready.")
    return pipeline


# ============================================================
# ONE PAGE
# ============================================================


def process_page(
    structure_pipeline,
    recall_pipeline,
    page: pymupdf.Page,
    page_number: int,
    dpi: int,
    tesseract_cmd: str | None,
    tesseract_enabled: bool,
    output_dir: Path,
) -> PageResult:
    print(f"\n[PAGE {page_number}] Rendering...")
    original = render_pdf_page(page, dpi)
    print(f"  Resolution: {original.shape[1]} x {original.shape[0]}")

    enhanced = mild_preprocess(original)
    debug_dir = output_dir / "debug"
    debug_dir.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(debug_dir / f"page_{page_number:04d}_enhanced.png"), enhanced)

    # Pass 1: structure + layout.
    print("  Pass 1/2: PP-StructureV3 layout + OCR...")
    results = list(structure_pipeline.predict(input=enhanced))
    if not results:
        raise RuntimeError(f"PP-StructureV3 returned no result for page {page_number}.")
    structure_result = results[0]
    structure_data = get_result_json(structure_result)
    save_json(output_dir / "json" / f"page_{page_number:04d}_structure.json", structure_data)

    raw_blocks = get_parsing_blocks(structure_data)
    blocks = [normalize_block(block) for block in raw_blocks if block_is_text(block)]
    blocks.sort(key=lambda b: float(b.get("block_order")) if b.get("block_order") is not None else 10**9)

    structure_candidates = extract_paddle_lines_from_structure(structure_data)
    print(f"  Structure OCR lines: {len(structure_candidates)}")
    print(f"  Layout text blocks: {len(blocks)}")

    # Pass 2: independent full-page OCR for recall.
    print("  Pass 2/2: independent PP-OCR recall pass...")
    recall_candidates = run_recall_ocr(recall_pipeline, original)
    print(f"  Recall OCR lines: {len(recall_candidates)}")

    merged_items = merge_ocr_candidates(structure_candidates, recall_candidates)
    print(f"  Merged canonical lines: {len(merged_items)}")

    verified_lines = [
        choose_final_text(original, item, tesseract_cmd, tesseract_enabled)
        for item in merged_items
    ]

    regions, orphan_count = create_regions(blocks, verified_lines, original.shape[:2])
    image_height, image_width = original.shape[:2]
    columns, paragraphs, page_text = reconstruct_document(
        regions,
        image_width=image_width,
        image_height=image_height,
    )

    # Coverage invariant: every canonical line must be in a region.
    included_line_count = sum(len(r.lines) for r in regions)
    if included_line_count != len(verified_lines):
        raise RuntimeError(
            f"Coverage invariant failed on page {page_number}: "
            f"{included_line_count} included / {len(verified_lines)} detected."
        )

    low_confidence = sum(1 for line in verified_lines if line.review_required)
    tess_used = sum(1 for line in verified_lines if line.used_tesseract)

    # Save review/debug images.
    debug = draw_debug_page(original, blocks, verified_lines)
    cv2.imwrite(str(debug_dir / f"page_{page_number:04d}_review.png"), debug)

    review_dir = output_dir / "review" / f"page_{page_number:04d}"
    review_dir.mkdir(parents=True, exist_ok=True)
    for idx, line in enumerate(verified_lines, start=1):
        if not line.review_required and not line.orphan:
            continue
        crop = crop_bbox(original, line.bbox, padding=28)
        if crop.size:
            status = "orphan" if line.orphan else "review"
            cv2.imwrite(str(review_dir / f"{status}_{idx:04d}.png"), crop)

    serial_lines = [asdict(line) for line in verified_lines]
    serial_regions = []
    for region in sorted(regions, key=lambda r: (r.order_hint, r.region_id)):
        serial_regions.append({
            "region_id": region.region_id,
            "order_hint": region.order_hint,
            "bbox": region.bbox,
            "label": region.label,
            "synthetic": region.synthetic,
            "text": build_region_text(region),
            "line_count": len(region.lines),
            "line_indices": [verified_lines.index(line) for line in region.lines],
        })
    serial_columns = [asdict(column) for column in columns]
    serial_paragraphs = [asdict(paragraph) for paragraph in paragraphs]

    save_json(
        output_dir / "json" / f"page_{page_number:04d}_audit.json",
        {
            "page": page_number,
            "dpi": dpi,
            "structure_line_count": len(structure_candidates),
            "recall_line_count": len(recall_candidates),
            "canonical_line_count": len(verified_lines),
            "included_line_count": included_line_count,
            "orphan_line_count": orphan_count,
            "coverage_complete": included_line_count == len(verified_lines),
            "low_confidence_count": low_confidence,
            "tesseract_used_count": tess_used,
            "regions": serial_regions,
            "columns": serial_columns,
            "paragraphs": serial_paragraphs,
            "image_width": image_width,
            "image_height": image_height,
            "lines": serial_lines,
        },
    )

    print(f"  Low-confidence / review lines: {low_confidence}")
    print(f"  Tesseract replacements: {tess_used}")
    print(f"  Orphan lines included: {orphan_count}")
    print(f"  Coverage: {included_line_count}/{len(verified_lines)} = 100%")

    return PageResult(
        page_number=page_number,
        text=page_text,
        blocks=blocks,
        lines=serial_lines,
        low_confidence_count=low_confidence,
        tesseract_used_count=tess_used,
        detected_line_count=len(verified_lines),
        included_line_count=included_line_count,
        orphan_line_count=orphan_count,
        regions=serial_regions,
        columns=columns,
        paragraphs=paragraphs,
        image_width=image_width,
        image_height=image_height,
    )


# ============================================================
# CLI
# ============================================================


def parse_args():
    parser = argparse.ArgumentParser(description="High-recall Arabic historical newspaper OCR.")
    parser.add_argument("pdf", nargs="?", default=None, help="Input PDF file.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_DIR, help="Output directory.")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI, help="PDF rendering DPI. Default: 420.")
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="Paddle device: cpu or gpu:0.")
    parser.add_argument("--no-tesseract", action="store_true", help="Disable Tesseract verification.")
    parser.add_argument("--tesseract", default=None, help="Explicit path to tesseract.exe.")
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.pdf:
        print('Usage: python arabic_newspaper_ocr_pro_v2.py "your_file.pdf"')
        sys.exit(1)

    pdf_path = Path(args.pdf).expanduser().resolve()
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found:\n{pdf_path}")

    output_dir = Path(args.output).expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 100)
    print("ARABIC HISTORICAL NEWSPAPER OCR PRO v2")
    print("=" * 100)
    print(f"Input : {pdf_path}")
    print(f"Output: {output_dir}")
    print(f"DPI   : {args.dpi}")
    print(f"Device: {args.device}")

    tesseract_cmd = args.tesseract or locate_tesseract()
    tesseract_enabled = not args.no_tesseract and check_tesseract(tesseract_cmd)
    if tesseract_enabled:
        print(f"[OK] Tesseract: {tesseract_cmd}")

    structure_pipeline = create_structure_pipeline(args.device)
    print("Initializing independent recall OCR...")
    recall_pipeline = create_recall_ocr(args.device)
    print("Independent recall OCR ready.")

    document = pymupdf.open(str(pdf_path))
    print(f"Pages: {len(document)}")

    pages: list[PageResult] = []
    try:
        for index in range(len(document)):
            pages.append(
                process_page(
                    structure_pipeline=structure_pipeline,
                    recall_pipeline=recall_pipeline,
                    page=document[index],
                    page_number=index + 1,
                    dpi=args.dpi,
                    tesseract_cmd=tesseract_cmd,
                    tesseract_enabled=tesseract_enabled,
                    output_dir=output_dir,
                )
            )
    finally:
        document.close()

    txt_path = output_dir / f"{pdf_path.stem}_OCR.txt"
    save_txt(txt_path, pages)

    docx_path = output_dir / f"{pdf_path.stem}_OCR.docx"
    save_docx(docx_path, pages)

    total_detected = sum(p.detected_line_count for p in pages)
    total_included = sum(p.included_line_count for p in pages)
    summary = {
        "input": str(pdf_path),
        "dpi": args.dpi,
        "device": args.device,
        "pages": len(pages),
        "canonical_lines_detected": total_detected,
        "canonical_lines_included": total_included,
        "coverage_complete": total_detected == total_included,
        "orphan_lines_included": sum(p.orphan_line_count for p in pages),
        "low_confidence_lines": sum(p.low_confidence_count for p in pages),
        "tesseract_replacements": sum(p.tesseract_used_count for p in pages),
        "tesseract_enabled": tesseract_enabled,
        "outputs": {
            "txt": str(txt_path),
            "docx": str(docx_path),
            "json": str(output_dir / "json"),
            "debug": str(output_dir / "debug"),
            "review": str(output_dir / "review"),
        },
        "design": [
            "PP-StructureV3 layout/read-order pass",
            "Independent PP-OCR full-page recall pass",
            "Geometry-based OCR line fusion",
            "Expanded-block mapping with orphan preservation",
            "Tesseract disagreement verification",
            "Coverage invariant: every canonical OCR line is emitted",
        ],
    }
    save_json(output_dir / "summary.json", summary)

    print("\n" + "=" * 100)
    print("OCR COMPLETE")
    print("=" * 100)
    print(f"TXT  : {txt_path}")
    print(f"DOCX : {docx_path}")
    print(f"JSON : {output_dir / 'json'}")
    print(f"DEBUG: {output_dir / 'debug'}")
    print(f"REVIEW: {output_dir / 'review'}")
    print(f"COVERAGE: {total_included}/{total_detected}")


if __name__ == "__main__":
    main()
