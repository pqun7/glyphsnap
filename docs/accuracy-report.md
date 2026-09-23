# OCR accuracy report

Evaluation date: 24 September 2026

## Method

- Dataset: the nine images in `tests/` and the supplied
  `ocr_image_text_ground_truth.json` file.
- OCR engine: the locally installed Tesseract Arabic and English models.
- Metrics: normalized character similarity, word similarity, reference-token
  coverage, and punctuation recall. Arabic diacritics and Unicode presentation
  differences are normalized; expected text is never changed during scoring.
- The same metric implementation in `ocr_metrics.py` was used for every run.

## Results

| Pipeline | Character similarity | Word similarity | Token coverage | Punctuation recall |
| --- | ---: | ---: | ---: | ---: |
| Previous default (`ara`) | 39.45% | 28.16% | 33.42% | 77.94% |
| Previous pipeline, manually set to `ara+eng` | 87.83% | 83.45% | 84.48% | 88.30% |
| GlyphSnap adaptive pipeline, automatic `ara+eng` | **92.65%** | **87.34%** | **88.22%** | **91.83%** |

The important user-facing comparison is the previous default versus the new
default: English and mixed pages no longer enter an Arabic-only OCR path. Even
against the previous manually selected mixed-language mode, the adaptive
pipeline improved all four metrics.

## Regression policy

`tests/test_dataset_regression.py` evaluates the full dataset and enforces both
aggregate and per-image minimums. The golden JSON remains unchanged. Known
reference ambiguities are recorded in `tests/REFERENCE_NOTES.md`, rather than
being edited to improve the score.

Run the report locally with:

```powershell
python benchmark_ocr.py --dataset tests/ocr_image_text_ground_truth.json --output test-results/accuracy.json
```

Results depend on the installed Tesseract version and language-model files, so
small cross-machine variation is expected.
