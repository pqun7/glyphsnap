"""Evaluate GlyphSnap against an image/ground-truth JSON dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time

from PIL import Image

from ocr_engine import installed_languages, locate_tesseract, recognize
from ocr_metrics import evaluate_text


def evaluate_dataset(dataset_path: Path) -> dict:
    command = locate_tesseract()
    available = installed_languages(command)
    required = [language for language in ("ara", "eng") if language in available]
    if not command or len(required) != 2:
        raise RuntimeError("Tesseract Arabic and English language data are required.")
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows = []
    started = time.perf_counter()
    for expected in dataset["images"]:
        image_path = dataset_path.parent / expected["image_name"]
        with Image.open(image_path) as source:
            result = recognize(source.convert("RGB"), command, required, 3, preprocess=True)
        metrics = evaluate_text(expected["text"], result.text)
        rows.append(
            {
                "image": expected["image_name"],
                **metrics.to_dict(),
                "confidence": round(result.confidence, 4),
                "variant": result.variant,
                "psm": result.psm,
                "characters": len(result.text),
            }
        )
    metric_names = (
        "character_similarity",
        "word_similarity",
        "token_coverage",
        "punctuation_recall",
    )
    return {
        "dataset": dataset.get("dataset", dataset_path.stem),
        "image_count": len(rows),
        "languages": required,
        "metrics": {
            name: statistics.fmean(row[name] for row in rows) for name in metric_names
        },
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "images": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("tests/ocr_image_text_ground_truth.json"),
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate_dataset(args.dataset)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
