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
