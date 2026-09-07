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

## Page Geometry

The reusable frame annotation, automatic deskew, crop comparison, per-page
origin, and final crop tools are documented in
[`docs/deskew-and-crop-workflow.md`](docs/deskew-and-crop-workflow.md).
Keep book-specific PDFs, rendered pages, and annotation state in the private
book workspace.

## Layout And Rendering

The reusable layout extraction and rendering rules are documented in
[`docs/layout-rendering-workflow.md`](docs/layout-rendering-workflow.md).
Those rules capture the current project baseline: detect layout geometrically,
compose text/equation/image nodes without deleting source content, validate each
candidate against the previous accepted baseline, and keep book-specific
coordinates in private workspaces.

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

For symbols arranged in known page cells, keep the source-specific coordinates
under ignored `private/` and build normalized crops with:

```bash
python3 tools/extract_cell_dataset.py \
  --config private/book-symbol-cells.json \
  --output private/book-symbols-v1
```

The extractor supports contrast stretching, scaling, and border whitening to
remove table rules without embedding book-specific assumptions in the tool.

Before publishing:

```bash
python3 tools/check_publishable.py --tracked-only
```

Run synthetic web-application tests:

```bash
python3 -m unittest discover -s tests -v
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
