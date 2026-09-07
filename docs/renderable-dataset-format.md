# Renderable Dataset Format

This is a minimal schema contract for page reconstruction pipelines. Projects
may add private extension fields, but reusable tools should preserve these
concepts and provenance fields.

## Dataset

```json
{
  "schema": "pdf-training-renderable-dataset-v1",
  "name": "book-private-render-v1",
  "private_source": true,
  "documents": []
}
```

## Document

```json
{
  "id": "document-id",
  "title": "Optional title",
  "pages": [],
  "style": {
    "fonts": {},
    "class_ranges": {}
  },
  "outline": []
}
```

## Page

```json
{
  "id": "document-id-p001",
  "document_id": "document-id",
  "source_page_index": 0,
  "output_page_index": 0,
  "width": 1234,
  "height": 1600,
  "nodes": []
}
```

## Node

All nodes share the same base fields:

```json
{
  "id": "document-id-p001-line-0001",
  "type": "text_band",
  "bbox": {"x": 100, "y": 200, "w": 900, "h": 42},
  "class": "body",
  "producer": "line-density-v1",
  "confidence": 0.97,
  "source": {
    "page_image": "private/path/or/id",
    "bbox": {"x": 100, "y": 200, "w": 900, "h": 42}
  },
  "relations": []
}
```

Recommended node types:

- `text_band`
- `text_fragment`
- `equation`
- `image`
- `diagram_component`
- `caption`
- `heading`
- `header`
- `footer`
- `page_number`
- `rule`
- `table`

## Text Band

```json
{
  "id": "document-id-p001-line-0001",
  "type": "text_band",
  "class": "body",
  "bbox": {"x": 100, "y": 200, "w": 900, "h": 42},
  "line_metrics": {
    "line_count": 1,
    "first_indent": 0,
    "following_indent": 0,
    "same_indent": true,
    "density": 0.14
  },
  "fragments": ["document-id-p001-frag-0001"]
}
```

## Text Fragment

```json
{
  "id": "document-id-p001-frag-0001",
  "type": "text_fragment",
  "class": "body",
  "bbox": {"x": 100, "y": 200, "w": 420, "h": 42},
  "text": "Detected text",
  "content_source": "ocr|vision|manual_review",
  "parent_band": "document-id-p001-line-0001"
}
```

## Equation

```json
{
  "id": "document-id-p001-eq-0001",
  "type": "equation",
  "bbox": {"x": 540, "y": 198, "w": 180, "h": 48},
  "latex": "x_T",
  "render_policy": "text|inline_math|display_math|image_fallback",
  "crop_asset": "private/review/equation.png",
  "content_source": "vision|manual_review",
  "relations": [
    {"type": "inline_with", "target": "document-id-p001-line-0001"}
  ]
}
```

`image_fallback` is for diagnostics only unless redistribution rights allow the
crop to be rendered in a final output.

## Image

```json
{
  "id": "document-id-p001-img-0001",
  "type": "image",
  "bbox": {"x": 180, "y": 520, "w": 650, "h": 420},
  "render_layer": "background",
  "transparent_background": true,
  "component_of": null,
  "relations": []
}
```

Images should support alternate composition components when rectangular source
masks conflict with text flow.

## Validation Report

Candidate pipelines should emit reviewable validation reports:

```json
{
  "schema": "pdf-training-validation-report-v1",
  "baseline": "accepted-dataset-id",
  "candidate": "candidate-dataset-id",
  "changed_pages": [],
  "removed_nodes": [],
  "suspect_nodes": [],
  "regressions": []
}
```

Do not promote a candidate dataset when `regressions` contains unresolved
content loss.

## Quality Report

`tools/quality_check.py` emits a broader report intended to gate candidate
promotion:

```json
{
  "schema": "pdf-training-quality-report-v1",
  "candidate": "document-p001",
  "created_at": "2026-01-01T00:00:00+00:00",
  "summary": {
    "status": "pass|review|fail",
    "pages": 1,
    "findings": 0,
    "errors": 0,
    "review": 0,
    "checks": {}
  },
  "baseline_report": null,
  "findings": []
}
```

Findings use `severity: "error"` for content loss or invalid geometry and
`severity: "review"` for suspicious-but-not-proven conditions. Current generic
checks include:

- bbox presence, positive size, and page bounds
- class height ranges
- actual or literal newline contamination in text-like fields
- text nodes intersecting equation/image/table nodes without explicit relations
- same-type near-duplicate bboxes
- optional source-image density and multi-row band checks
- optional baseline regression checks

## HTML Render Review

Renderable pages can be reviewed with the generic fixed-layout HTML renderer:

```bash
python3 tools/render_page_html.py \
  --page private/book/renderable/page-001.json \
  --out private/book/rendered/page-001.html \
  --comparison-html private/book/rendered/page-001-comparison.html \
  --source-image private/book/pages/page-001.png \
  --show-boxes
```

The renderer expects page and node coordinates in source-page pixels. It does
not infer content; it only renders the metadata it receives. Simple equations
with `render_policy: "text"` are rendered as text-like HTML fragments so inline
subscripts, superscripts, vectors, and common symbols match the body text more
closely. Structural equations use browser-native MathML for constructs such as
fractions and roots.

Images, diagrams, and tables are rendered on a background layer with transparent
composition enabled by default. This allows text/equation nodes to sit above
diagram geometry when a private project decomposes a source image into layout
components.

## Equation Review Dataset

Equation review output is private source-derived data. The reusable tool writes
one JSON file per crop plus a summary and static HTML review page:

```json
{
  "schema": "pdf-training-equation-review-v1",
  "total": 1,
  "processed": 1,
  "items": [
    {
      "id": "document-p001-eq-0001",
      "page_id": "document-p001",
      "bbox": {"x": 120, "y": 300, "w": 180, "h": 44},
      "assets": {
        "equation_crop": "assets/equations/document-p001-eq-0001.png",
        "context_crop": "assets/contexts/document-p001-eq-0001.png"
      },
      "ocr_text": "",
      "vision_latex": "x_T = 12",
      "source_latex": "",
      "correction": null,
      "item_json": "items/document-p001-eq-0001.json"
    }
  ]
}
```

Corrections are intentionally separate files so private review applications can
write them without mutating source metadata:

```json
{
  "id": "document-p001-eq-0001",
  "correction": "x_T = 12"
}
```

`tools/equation_review.py merge` applies corrections first, then vision LaTeX,
then any existing source LaTeX. It matches equations by stable id first and bbox
IoU second, which allows private projects to recover reviewed values after minor
metadata regeneration.

## Text Segment Review Dataset

Text segment review output is private source-derived data. It is structurally
similar to equation review, but targets text-bearing nodes:

```json
{
  "schema": "pdf-training-text-segment-review-v1",
  "total": 1,
  "processed": 1,
  "items": [
    {
      "id": "document-p001-frag-0001",
      "page_id": "document-p001",
      "target_node": "document-p001-frag-0001",
      "bbox": {"x": 120, "y": 220, "w": 460, "h": 36},
      "assets": {
        "segment_crop": "assets/segments/document-p001-frag-0001.png"
      },
      "source_text": "",
      "ocr_text": "recognized text",
      "vision_text": "reviewed text",
      "vision_text_with_refs": "reviewed text [[REF:equation-id]]",
      "mask_refs": [],
      "item_json": "items/document-p001-frag-0001.json"
    }
  ]
}
```

The builder prefers existing `text_fragment` nodes. If only `text_band` nodes
exist, it creates temporary segment boxes by subtracting equation/image
obstacles. Known equations and images are never sent as the target text crop;
their intersections are recorded as `mask_refs` for downstream inline-anchor
placement.

Corrections use separate files:

```json
{
  "id": "document-p001-frag-0001",
  "text": "corrected visible text"
}
```

`tools/text_segment_review.py merge` applies correction files first, then vision
text, then OCR text, then existing source text. It updates only matched
text-bearing nodes and records merge provenance.
