# equation-vector 1.0.0

Tesseract LSTM model extending compact equation OCR with a vector-force class.

- promoted artifact: `equation-vector.traineddata`
- source training data distributed: no
- intended input: tightly cropped equation lines or force tokens
- internal vector-force token: `U+E000`
- output normalization: replace `U+E000` with `F` followed by `U+20D7`
- held-out vector-token recall: `5/6`
- vector validation CER: `0.1587`
- scalar-anchor validation CER: `0.1185`

Use `tools/normalize_ocr_tokens.py --replace '=F⃗'` to normalize output.
Private source-derived crops and sample-level reports are not distributed.
