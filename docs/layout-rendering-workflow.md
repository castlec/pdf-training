# Layout Rendering Workflow

This document captures the reusable layout and rendering rules learned from
technical scanned-PDF reconstruction work. Keep book-specific coordinates,
source pages, crops, masks, OCR output, and generated renders outside this
publishable repository unless redistribution rights are documented.

## Goal

Produce a renderable page dataset where layout geometry, text banding, text
fragments, equation placement, image placement, headers, footers, and typography
can be rebuilt consistently from private page images plus private source
annotations.

Human-authored equation and image annotations may be accepted as source metadata
when the current project cannot reliably rediscover them. Text layout, line
banding, composition, and classification should be algorithmic. LaTeX content
correction may remain a practical manual exception.

## Inputs

- Clean page image.
- Accepted equation and image annotations, when available.
- Reviewed equation LaTeX metadata, when available.
- Optional table-of-contents or document-outline data for heading validation.
- OCR and vision-model outputs as content evidence, not as geometric authority.

## Output Model

Renderable data should be represented as nodes with explicit provenance:

- `page`: source document id, source page index, output page index, dimensions.
- `text_band`: one physical text row or a deliberately merged text paragraph
  band.
- `text_fragment`: a text span inside a band, including fragments split by
  equations or images.
- `equation`: an accepted equation bbox plus LaTeX and render policy.
- `image`: a diagram/photo/table image bbox or a promoted decomposed component.
- `header` and `footer`: generated from document rules where patterns are
  fixed.
- `rule`: horizontal/vertical printed rules that should be regenerated rather
  than OCRed.

Each node should retain:

- source page and page image identity
- source bbox in page pixels
- classification
- detector/pass that produced it
- related equation/image ids when applicable
- suppression, split, merge, or duplicate reason when applicable

## Core Constraints

- Never throw away content of value.
- Every visible text line should be represented by a detected band node.
- Equation and image boxes are content obstacles and layout anchors, not
  deletion instructions.
- A text band that extends into an equation or image should be split or
  classified around that content. It should not pass through the obstacle.
- Text before, after, above, or below an equation/image must be preserved.
- Text on both sides of an equation/image should be represented as separate
  fragments with an explicit relationship to the shared row.
- Whole-node deletion is invalid merely because a candidate overlaps an
  equation/image.
- Duplicate handling must state the survivor or suppression reason.
- All content must fit known geometric and typographic ranges for its class.

## Layout Detection

Layout detection is geometry-first:

- Use grayscale/threshold/density operations to find visible ink bands.
- Detect physical lines inside candidate bands before OCR is considered.
- Use top and bottom edge expansion to capture superscripts, vector marks, and
  subscripts that sit outside the dense body of a text line.
- Use known class height ranges to flag bands that are too tall, too short, or
  internally inconsistent.
- Split oversized bands when density valleys or multi-line evidence show that
  one detected bbox contains multiple physical lines.
- Recombine adjacent bands only when spacing, alignment, density, and class
  ranges support the merge.
- Treat horizontal rules as independent layout features.

OCR text values must not decide whether a band exists, is split, is removed, or
is classified. OCR and vision models populate or check content after layout is
defined.

## Text Segment Review

Text transcription should operate on exact text-bearing crop images, not full
pages. The reusable workflow is:

- Use existing `text_fragment` nodes when available.
- If only `text_band` nodes exist, split them around equation/image obstacles to
  create temporary text segment crops.
- Do not pass known equation/image pixels as the target text crop.
- Preserve equation/image intersections as `mask_refs` so later rendering can
  place inline anchors.
- Treat OCR and vision output as content evidence only; corrections merge back
  into matched text nodes with provenance.

## Equation And Image Composition

Equation and image annotations should be intersected with text bands only after
text bands are detected.

Rules:

- Intersections may split text fragments, but must not enlarge equation/image
  boxes.
- Inline equations should participate in normal left-to-right flow inside the
  text row when their vertical overlap matches a text band.
- Display equations may occupy their own bands or blocks.
- Diagrams should render below generated text where text and diagram geometry
  overlap due to rectangular convenience masks.
- Diagram decomposition may create auxiliary components for layout, but full
  image preservation remains the default unless a component is explicitly
  promoted.
- If text intersects an image bbox but appears outside the diagram's actual ink
  shape, preserve the text and erase duplicate source pixels from the image
  render where necessary.

## Text Rendering Policy

Prefer text rendering when a mathematical expression can be represented cleanly
as encoded text with inline symbols. Use LaTeX/MathML rendering for structures
that require it, including fractions, roots, stacked constructs, multi-line
equations, large operators, and cases where faithful symbol placement is not
practical as plain text.

Equation metadata should include both:

- authoritative LaTeX or MathML source
- render policy: `text`, `inline_math`, `display_math`, or `image_fallback`

The original equation crop is review evidence, not the preferred final render
source.

The reusable HTML renderer follows this policy directly:

- `text` equations are rendered with text-like HTML, preserving simple
  subscripts, superscripts, vectors, and common technical symbols.
- `inline_math` and `display_math` equations are rendered as MathML.
- Equation crops are not rendered as final equation content unless a private
  project deliberately uses diagnostic fallback output.
- MathML and text fragments use the same configured body font family by
  default; individual projects can override fonts through dataset style
  metadata.

## Header, Footer, And Outline Rules

Fixed headers, footers, page numbers, and decorative rules should be generated
from document rules rather than OCRed as body content.

Heading classification should be checked against outline or table-of-contents
structure. A candidate heading should match the known outline text exactly or
approximately before it is promoted. Text inside a table of contents should not
be promoted as a body heading.

Heading font selection should be derived from heading level and document style,
not from ad-hoc OCR styling.

Use `tools/document_structure.py` for the reusable portion of this workflow:

- `page-map` extracts printed page labels from footer text nodes or applies a
  private offset rule.
- `apply` uses private outline entries and private header/footer templates to
  classify headings, suppress OCR header/footer source nodes, and append
  generated recurring structure.
- Concrete outline titles, page maps, and header/footer coordinates are
  document-specific private metadata.

## Validation

Every algorithmic change must start from the current accepted baseline.

Every candidate run must go through the full validation bundle before it is
described as a proposal:

- layout/page overlay review
- changed-node comparison against the immediate baseline
- removed-content audit
- density and size audit
- band geometry audit
- equation/image intersection audit
- header/footer/outline audit when applicable

Use `tools/quality_check.py` as the reusable validation entry point. It can run
against one renderable page or a multi-document renderable dataset, and it can
include `tools/validate_render_dataset.py` baseline comparison as part of the
same report.

If a candidate creates a regression, discard that candidate and make a different
change from the accepted baseline. Do not stack fixes on top of a known-bad
candidate unless the bad candidate is explicitly promoted as the new baseline.

Manual patching of layout or text into generated output is not an algorithmic
fix. Manual corrections may be stored as review evidence, but reusable tooling
should continue to target zero manual layout corrections.

## Private Data Boundary

Keep these outside the publishable repository:

- source PDFs and scanned pages
- book-specific page images and crops
- book-specific masks and coordinates
- OCR transcripts derived from copyrighted pages
- generated layout/render datasets derived from copyrighted pages
- vision-model responses that reproduce source text

Promote only reusable code, schemas, synthetic fixtures, aggregate metrics, and
reviewed model releases that pass provenance checks.
