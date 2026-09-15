# PDF Realization Grammar

The document grammar has two layers. Structural nodes describe the document
that grouping and translation operate on. Realization describes the exact
ordered PDF material needed to render those nodes without consulting the
source PDF again.

## Structural Layer

```text
Document -> Page*
Page -> Section*
Section -> Content*
Content -> TextNode | VectorArtwork | RasterImage | Rule | Container
Container -> Content* | Table
Table -> Cell+
Cell -> Content*
```

## Realization Layer

```text
RealizationPage -> MediaBox Resources Operation* Node*
Resources -> Fonts XObjects ExtGStates ColorSpaces Patterns Shadings Properties
Operation -> Save | Restore | BeginText | EndText | SetTransform
           | SetFont | SetGraphicsState | MoveTo | LineTo | CurveTo
           | ClosePath | Clip | PaintPath | DrawImage | DrawForm
           | MarkedContent | OtherTypedOperation
Node -> StructuralNode RealizationScope?
RealizationScope -> LocalTransform Operation*
```

Every operation has a unique ascending `ordinal`. Regeneration must emit
operations in ordinal order, not by node type or visual classification.

Resource names are local to the page or realization scope. Operations that
reference a font, XObject, graphics state, color space, or pattern must point
to
a declared entry in that scope's resource dictionary.

`save`/`restore` and `begin_text`/`end_text` are balanced lexical scopes. A
container's `local_transform` maps its operation coordinates into its parent
coordinate system. It is not a replacement for operation order or absolute
page geometry.

## JSON Shape

The executable supplement is in `tools/pdf_realization.py` and uses schema
`pdf-training-realization-page-v1`:

```json
{
  "schema": "pdf-training-realization-page-v1",
  "id": "page-001",
  "media_box": {"x": 0, "y": 0, "w": 595, "h": 842},
  "resources": {
    "fonts": {}, "xobjects": {}, "extgstates": {},
    "color_spaces": {}, "patterns": {}, "shadings": {}, "properties": {}
  },
  "operations": [
    {"ordinal": 0, "kind": "save"},
    {"ordinal": 1, "kind": "draw_form", "xobject": "Meta16"},
    {"ordinal": 2, "kind": "restore"}
  ],
  "nodes": []
}
```

This is additive to `pdf-training-renderable-page-v1` and existing grouping
rules. A later adapter can copy extracted operation/resource data into this
shape; translation should modify only eligible `TextNode` values and leave the
realization layer intact.
