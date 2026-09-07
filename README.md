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

Generic geometry-first primitive detection is available as:

```bash
python3 tools/layout_detect.py \
  --image private/book/pages/page-001.png \
  --mask equation=private/book/masks/page-001-equations.png \
  --out private/book/layout/page-001.json
```

The detector emits text bands, internal line metrics, horizontal rules, optional
mask intersections, edge-expansion data, and audited cleanup actions. It does
not run OCR and does not use OCR text to decide layout.

Compose layout primitives and private annotations into renderable page metadata:

```bash
python3 tools/render_compose.py \
  --layout private/book/layout/page-001.json \
  --annotations private/book/annotations/page-001.json \
  --out private/book/renderable/page-001.json \
  --review-html private/book/renderable/page-001.html
```

Validate a candidate renderable page or dataset against its immediate baseline:

```bash
python3 tools/validate_render_dataset.py \
  --baseline private/book/renderable-baseline/page-001.json \
  --candidate private/book/renderable-candidate/page-001.json \
  --out private/book/validation/page-001.json \
  --review-html private/book/validation/page-001.html
```

Render a composed page as fixed-layout HTML, optionally with a side-by-side
comparison against the private source page image:

```bash
python3 tools/render_page_html.py \
  --page private/book/renderable/page-001.json \
  --out private/book/rendered/page-001.html \
  --comparison-html private/book/rendered/page-001-comparison.html \
  --source-image private/book/pages/page-001.png \
  --show-boxes
```

The HTML renderer uses fixed page dimensions, background image layers,
CSS-positioned content nodes, text-style rendering for simple inline equations,
and MathML for structural equations. Private images and text remain outside the
repository unless explicitly rights-cleared.

Build a generic equation-review dataset from renderable metadata:

```bash
python3 tools/equation_review.py build \
  --input private/book/renderable/page-001.json \
  --source-base private/book \
  --report-root private/book/equation-review/page-001 \
  --provider ollama-chat \
  --host http://127.0.0.1:11434 \
  --model gemma3
```

After human or automated correction files are written under
`corrections/<equation-id>.json`, merge accepted LaTeX back into renderable
metadata:

```bash
python3 tools/equation_review.py merge \
  --metadata private/book/renderable/page-001.json \
  --summary private/book/equation-review/page-001/summary.json \
  --corrections-root private/book/equation-review/page-001/corrections \
  --out private/book/renderable-reviewed/page-001.json
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
