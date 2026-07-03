# equation-latin 1.0.0

Tesseract LSTM model for compact Latin-script technical equations, force and
moment notation, subscripts, numeric values, and normalized sigma notation.

- promoted artifact: `equation-latin.traineddata`
- source training data distributed: no
- intended input: tightly cropped equation lines or tokens
- full-page prose OCR: not recommended
- aggregate validation CER: `0.2052`
- held-out validation CER: `0.3865`

Training used private source-derived crops that are intentionally excluded from
this repository. The model package contains no page images, transcriptions, or
sample-level reports.
