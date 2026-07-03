# PDF Training

Canonical, reusable tooling and promoted OCR assets for scanned technical PDFs.

## Repository Policy

Keep:

- reusable page-annotation and mask-proposal tools
- reusable OCR dataset export and Tesseract training tools
- portable, reviewed OCR crops with ground truth
- promoted OCR model releases with checksums and model cards

Do not keep:

- full source books or page images
- book-specific masks, layout graphs, renders, or extraction outputs
- exploratory checkpoints and rejected models
- scripts whose behavior is tied to one book
- absolute paths to a local workspace

Book repositories own source-specific collection and annotation state. Promote
only reviewed OCR samples and generally useful tooling here.

## Layout

```text
datasets/                         Public, rights-cleared datasets only
models/                           Promoted Tesseract model releases
tools/                            Generic collection, annotation, and training tools
docs/                             Dataset and model conventions
```

## Dataset Export

```bash
python3 tools/export_ocr_dataset.py \
  --name technical-equations-v1 \
  --output private/datasets/technical-equations-v1 \
  /path/to/manifest-a.json /path/to/manifest-b.json
```

Input manifests use `samples[]` entries with `id`, `image`, `split`, `lines`,
and optional `tags`. Exported manifests contain relative paths and omit
book-specific source geometry. Exporting does not grant redistribution rights;
private output is the default workflow.

Before publishing:

```bash
python3 tools/check_publishable.py
```

## Training

```bash
python3 tools/tesseract_finetune.py \
  --manifest datasets/technical-equations-v1/manifest.json \
  --output-dir runs/equation-model \
  --base-lang eng \
  --model-name equation
```

Training runs belong under ignored `runs/`. Promote only selected
`.traineddata`, reports, checksums, and model cards into `models/`.
