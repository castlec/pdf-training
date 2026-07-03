# OCR Dataset Format

Each dataset contains:

```text
images/<sample-id>.png
ground-truth/<sample-id>.gt.txt
manifest.json
```

`manifest.json` contains:

- `schema`: `pdf-training-ocr-dataset-v1`
- `name`: stable dataset release name
- `samples[].id`: unique portable identifier
- `samples[].image`: path relative to the manifest
- `samples[].ground_truth`: path relative to the manifest
- `samples[].lines`: normalized UTF-8 transcription
- `samples[].split`: `train` or `eval`
- `samples[].psm`: expected Tesseract segmentation mode
- `samples[].tags`: notation and coverage labels

Source-book paths and page coordinates are deliberately excluded. Provenance
that cannot be published without coupling the dataset to a book stays in the
source project.
