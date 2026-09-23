# Ground-truth notes

The JSON text is treated as reference data and is not modified to match OCR output.

- Line wrapping and repeated whitespace in the source artwork are presentation details;
  evaluation normalizes whitespace and Arabic diacritics but does not replace words.
- Image `2.jpg` appears to show `7:00` in the Arabic time sentence while the supplied
  reference omits the digits. This remains unchanged and is counted by the metrics.
- Image `9.png` is a dense, low-resolution page. Its long reference should be reviewed
  against a higher-resolution original before being used as publication-grade truth.
