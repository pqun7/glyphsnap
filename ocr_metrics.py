"""OCR evaluation metrics shared by regression tests and the benchmark CLI."""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass
import re
import unicodedata


ARABIC_DIACRITICS = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")
WORD_PATTERN = re.compile(r"[\w\u0600-\u06ff]+", re.UNICODE)
PUNCTUATION_PATTERN = re.compile(r"[^\w\s\u0600-\u06ff]", re.UNICODE)


@dataclass(frozen=True)
class OCRMetrics:
    character_similarity: float
    word_similarity: float
    token_coverage: float
    punctuation_recall: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def normalize_text(value: str, *, keep_punctuation: bool = True) -> str:
    value = unicodedata.normalize("NFKC", value).lower().replace("ـ", "")
    value = ARABIC_DIACRITICS.sub("", value)
    if not keep_punctuation:
        value = PUNCTUATION_PATTERN.sub(" ", value)
    return " ".join(value.split())


def levenshtein(reference, hypothesis) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def _similarity(reference, hypothesis) -> float:
    return max(0.0, 1.0 - levenshtein(reference, hypothesis) / max(1, len(reference)))


def evaluate_text(reference: str, hypothesis: str) -> OCRMetrics:
    reference_chars = normalize_text(reference)
    hypothesis_chars = normalize_text(hypothesis)
    reference_words = WORD_PATTERN.findall(normalize_text(reference, keep_punctuation=False))
    hypothesis_words = WORD_PATTERN.findall(normalize_text(hypothesis, keep_punctuation=False))
    expected_counts = Counter(reference_words)
    actual_counts = Counter(hypothesis_words)
    matched = sum(min(count, actual_counts[token]) for token, count in expected_counts.items())
    reference_punctuation = Counter(PUNCTUATION_PATTERN.findall(reference_chars))
    hypothesis_punctuation = Counter(PUNCTUATION_PATTERN.findall(hypothesis_chars))
    matched_punctuation = sum(
        min(count, hypothesis_punctuation[token]) for token, count in reference_punctuation.items()
    )
    return OCRMetrics(
        character_similarity=_similarity(reference_chars, hypothesis_chars),
        word_similarity=_similarity(reference_words, hypothesis_words),
        token_coverage=matched / max(1, sum(expected_counts.values())),
        punctuation_recall=(
            matched_punctuation / sum(reference_punctuation.values())
            if reference_punctuation
            else 1.0
        ),
    )
