# Pipeline Chains

This document describes reusable reconstruction chains that private book
projects can compose. It is intentionally not an orchestrator contract.
Different books need different combinations of layout detection, review,
metadata correction, and rendering.

Keep source PDFs, page images, OCR transcripts, model responses, coordinates,
and generated datasets in the private project workspace unless redistribution
rights are documented.

## Common Stages

- Prepare clean page images with deskew/crop tooling.
- Detect layout primitives with `tools/layout_detect.py`.
- Compose renderable metadata with `tools/render_compose.py`.
- Apply document structure rules with `tools/document_structure.py` when
  outline, header, footer, or page-number data is useful.
- Review equation crops with `tools/equation_review.py`.
- Review text segment crops with `tools/text_segment_review.py`.
- Render fixed-layout HTML with `tools/render_page_html.py`.
- Validate candidates with `tools/quality_check.py`.
- Promote only datasets that pass validation or have documented accepted review
  findings.

## Minimal OCR Dataset Chain

Use this when the goal is to train or evaluate OCR models rather than recreate
pages.

1. Annotate or identify useful crops in the private workspace.
2. Normalize crops with `tools/extract_cell_dataset.py` or project-specific
   crop collection.
3. Export rights-cleared OCR samples with `tools/export_ocr_dataset.py`.
4. Train or evaluate with `tools/tesseract_finetune.py`.
5. Promote only reviewed, rights-cleared datasets or model releases.

## Layout-Only Chain

Use this when testing whether page structure can be identified before spending
model time on text or equations.

1. Run `tools/layout_detect.py` on clean private page images.
2. Compose private annotations with `tools/render_compose.py` if equation/image
   masks exist.
3. Run `tools/quality_check.py --density` to flag geometry problems.
4. Render overlays or fixed-layout HTML for review.
5. Adjust algorithms from the accepted baseline; discard candidates that lose
   content.

## Equation-Heavy Technical Book Chain

Use this when equation fidelity is the main risk.

1. Detect layout bands with `tools/layout_detect.py`.
2. Compose accepted equation/image annotations with `tools/render_compose.py`.
3. Build equation review crops with `tools/equation_review.py build`.
4. Correct LaTeX in private correction files where needed.
5. Merge reviewed LaTeX with `tools/equation_review.py merge`.
6. Classify render policy so simple expressions render as text and structural
   expressions render as MathML.
7. Run `tools/quality_check.py` against the prior accepted baseline.
8. Render comparison HTML with `tools/render_page_html.py`.

## Full Reconstruction Chain

Use this when the target is a renderable replacement page or book.

1. Prepare cleaned page images.
2. Detect layout primitives.
3. Compose renderable metadata with accepted equation/image annotations.
4. Build or supply private page map, outline, and header/footer templates.
5. Apply document structure rules.
6. Build equation review and merge reviewed LaTeX.
7. Build text segment review and merge reviewed text.
8. Render fixed-layout HTML comparisons.
9. Run `tools/quality_check.py` with baseline comparison and density checks.
10. Promote the candidate only if it preserves valuable content and remaining
    findings are understood.

## Translation Or Rerender Chain

Use this when source text is transformed but source geometry remains useful.

1. Complete a reviewed source-language renderable dataset first.
2. Translate text-bearing nodes in the private workspace.
3. Preserve equation LaTeX, image nodes, rule nodes, page map, and document
   structure unless the target edition intentionally changes them.
4. Re-run render policy classification if translated inline text changes
   equation adjacency or wrapping.
5. Render side-by-side source and target comparisons.
6. Run `tools/quality_check.py`; treat overflow, missing nodes, and unexpected
   class changes as candidate failures.

## Decision Points

- If the book has reliable existing equation/image annotations, use them as
  source metadata. Do not spend effort rediscovering them unless future projects
  need that capability.
- If a page has no equation or image complexity, text segment review may be
  enough after layout detection.
- If headers and footers follow a fixed pattern, generate them from private
  templates and suppress OCR-derived source nodes.
- If TOC or outline data is available, use it to constrain heading
  classification and font choice.
- If a candidate loses content, reject that candidate rather than layering fixes
  on top of a known-bad state.
- If a model output reproduces copyrighted source text, keep it in the private
  workspace.

## Promotion Gate

Before a private project promotes a candidate baseline, run the relevant checks:

```bash
python3 tools/quality_check.py \
  --candidate private/book/renderable-candidate/page-001.json \
  --baseline private/book/renderable-baseline/page-001.json \
  --source-base private/book \
  --density \
  --out private/book/validation/page-001-quality.json \
  --review-html private/book/validation/page-001-quality.html
```

The exact chain can vary. The gate should not: every promoted candidate should
have a reviewable quality report tied to the immediate accepted baseline.
