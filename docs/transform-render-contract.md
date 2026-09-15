# Transform and Render Contract

The PDF IR has two distinct layers:

- `operations` and `structured_operations` preserve extracted source data and provenance.
- `operation_groups` and their recursive children are the active transformed render structure.

A transform returns a replacement document. Unless a transform explicitly declares an additive debug/provenance result, the previous active structure is no longer used for rendering.

## CFG Rules

Active nodes are recursive groups, realizations, or operations. A table is a group whose children are cells. A cell is a group that may contain content, nested groups, and operation realizations. The grammar describes structure; it does not by itself assign ownership.

## Ownership Rules

1. Every active operation has exactly one owner.
2. An operation may be owned by a page stream or one recursive group, but never both.
3. `associated_text` nodes are references and do not claim ownership merely by carrying source ordinals.
4. Ownership is represented structurally by operation-bearing groups, not by renderer-only metadata.
5. Sparse operation ownership is valid. A group must not be treated as owning the interval between its first and last ordinal.

## Realization Closure

A node is renderable from the transformed document alone. Its owned operations, graphics state requirements, resources, geometry, and coordinate context must be available through the active structure. Provenance may identify the source operation, but provenance cannot substitute for an active realization.

## Renderer Rules

1. The renderer consumes the active transformed structure.
2. It may consult the canonical source operation store only to resolve an operation explicitly owned by the active structure.
3. It must not emit unowned source operations through a second parallel stream.
4. Recursive rendering must preserve page-level operations in gaps between sparse group-owned operations.
5. Layout changes such as expandable borders are transforms or explicit node realizations, never hidden renderer heuristics.

## Validation

`apply_transform` validates every transform result. Validation checks the PDF IR schema, recursive CFG shape, operation ordinal validity, duplicate active ownership, and render closure. Pipeline execution therefore fails at the transform that first violates the contract rather than producing a misleading final PDF.
