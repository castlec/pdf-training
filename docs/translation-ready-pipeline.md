# Translation-Ready Pipeline

`pipelines/translation-ready-pre-gemma-v1.json` is the deterministic handoff
between PDF extraction/layout analysis and the language model. It does not
invoke Gemma, Unsloth, or any network service.

## Lifecycle

The fixed runner remains:

1. Extract the PDF into the faithful PDF IR.
2. Execute the manifest transforms in order.
3. Render the transformed IR to PDF.

The manifest declares transforms only. Input and rendering remain fixed in
`tools/run_render_pipeline.py`.

The manifest stages are:

1. `font.catalog.v1`: record source font resources and usage; do not replace fonts.
2. `structure.materialize-native-tables.v1`: replace tagged PDF table structure with recursive table/row/cell containers and assign direct marked-content ownership.
3. `group.horizontal-rules.v1`: create rule-delimited structural sections.
4. `geometry.operations-into-groups.v1`: create geometry groups and attach their operations to the enclosing section.
5. `layout.associate-text-operations.v1`: associate nearby text operation spans with geometry groups using the persisted collision span.
6. `layout.expand-associated-content.v1`: transfer associated text ownership into the active geometry group.
7. `translation.project.v1`: decode source text from embedded `ToUnicode` maps and create a text-only translation projection without changing active rendering nodes.
8. `translation.validate.v1`: verify unique IDs, source operation ownership references, page references, and recursive context paths.
9. `geometry.localize-operation-groups.v1`: materialize operation-local coordinate data.
10. `layout.relative-coordinates.v1`: materialize the current relative layout policy.
11. `diagnostics.unprocessed-items.v1`: retain residual diagnostics for review.

## Model Request

The projection is stored in transformed IR metadata under
`metadata.translation_projection`. Export it with:

```bash
PYTHONPATH=. python3 tools/export_translation_projection.py \
  transformed.json translation.request.json
```

The export is `pdf-training-translation-request-v1`. It contains, for every
text operation:

- a stable source ID and source text;
- page ID and recursive group path;
- geometry group IDs when the text is associated with a geometric construct;
- table path when the source has native recursive table structure.

It intentionally excludes images, coordinates, bounding boxes, raw operator
ordinals, and the source PDF. It does not filter ASCII-looking text as
“already English”; every decoded source text unit is included.

The primary prompt is formal mathematical Czech-to-English translation. It
requires one output object per ID, exact preservation of numbers, units,
variables, symbols, superscript relationships, signs, and quantities, and
forbids solving, explaining, merging, splitting, reordering, or normalization.
The terminology prompt is a separate later pass over approved original/English
pairs. Neither prompt is executed by this repository.

## Layout Policy

The projection records the target profile for the later apply/reflow stage:

- Arial, with Liberation Sans fallback;
- preserve nominal source size, with 12 pt as the body baseline;
- reflow and container growth before scaling;
- prefer 95% automatic reduction, with 90% as the lower automatic limit;
- never scale geometry to compensate for prose length.

This pre-Gemma pipeline does not apply the target font or rewrite text
operators. That belongs after model output validation, when translated text
length is known.

## Coverage Boundary

Native PDF table tags are converted recursively and are visible in the
projection's `table_path`. Geometry-only constructs are represented as
geometry groups and associated text, not guessed as tables. A future geometric
table-inference transform may add table semantics, but the current pipeline
will not invent them. This distinction is reported by the request data rather
than hidden from the model handoff.

## Validation Gate

The pipeline is ready for a model run only when all of these are true:

- the full test suite passes;
- extraction and all manifest transforms complete;
- `translation.validate.v1` reports `passed`;
- projection diagnostics contain no undecodable text;
- the request reports `model_invoked: false` before the model is deliberately enabled;
- the source-preserving render completes and has the expected page count.
