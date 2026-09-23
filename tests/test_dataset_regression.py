from __future__ import annotations

import unittest
from pathlib import Path

from benchmark_ocr import evaluate_dataset


class GoldenDatasetRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dataset = Path(__file__).with_name("ocr_image_text_ground_truth.json")
        cls.report = evaluate_dataset(dataset)

    def test_aggregate_character_similarity(self) -> None:
        self.assertGreaterEqual(self.report["metrics"]["character_similarity"], 0.90)

    def test_aggregate_word_similarity(self) -> None:
        self.assertGreaterEqual(self.report["metrics"]["word_similarity"], 0.84)

    def test_missing_text_coverage(self) -> None:
        self.assertGreaterEqual(self.report["metrics"]["token_coverage"], 0.86)

    def test_no_catastrophic_per_image_failure(self) -> None:
        for row in self.report["images"]:
            with self.subTest(image=row["image"]):
                self.assertGreaterEqual(row["character_similarity"], 0.75)


if __name__ == "__main__":
    unittest.main(verbosity=2)
