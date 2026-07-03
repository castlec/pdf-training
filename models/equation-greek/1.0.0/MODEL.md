# equation-greek 1.0.0

Tesseract LSTM model extending compact equation OCR with literal `α`, literal
`β`, and the internal vector-force token.

- promoted artifact: `equation-greek.traineddata`
- source training data distributed: no
- intended input: glyph or compact equation crops
- isolated Greek token validation: `7/7` exact
- Greek validation CER: `0.1681`
- vector validation CER: `0.1164`
- scalar-anchor validation CER: `0.1212`

Broad equation lines still require segmentation into compact crops. Private
source-derived crops and sample-level reports are not distributed.
