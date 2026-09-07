# Migration Inventory

This repository is the canonical home for reusable PDF OCR, annotation, layout,
and rendering tooling. Source-book workspaces remain the home for copyrighted
inputs, private annotations, generated datasets, and exploratory reports.

## Already Present

- Page annotation web applications.
- Deskew, crop-origin, and clean-page workflow.
- OCR dataset export.
- Tesseract finetuning workflow.
- Promoted equation OCR models:
  - `equation-greek`
  - `equation-latin`
  - `equation-vector`
- Publishability/provenance guard.

## Newly Consolidated

- Generic layout primitive detection:
  - `tools/layout_detect.py`
- Generic renderable metadata composition:
  - `tools/render_compose.py`
- Generic baseline/candidate render dataset validation:
  - `tools/validate_render_dataset.py`
- Generic fixed-layout HTML/MathML render preview and comparison viewer:
  - `tools/render_page_html.py`
- Generic equation crop review and reviewed-LaTeX merge workflow:
  - `tools/equation_review.py`
- Generic cell-based OCR sample extraction:
  - `tools/extract_cell_dataset.py`
- Generic margin repainting after origin crop:
  - `tools/repaint_page_margins.py`
- Reusable layout/rendering rules:
  - `docs/layout-rendering-workflow.md`
- Renderable dataset schema contract:
  - `docs/renderable-dataset-format.md`

## Still Statika-Specific

Keep these in the private source-book workspace unless they are rewritten as
generic tools:

- page-specific mask annotations
- page-specific layout graphs
- rendered page comparisons
- OCR/Gemma transcripts derived from source pages
- book-specific typography reports
- table-of-contents extraction outputs
- generated line-content and region-content datasets
- equation crop review pages

## Candidate Future Migrations

These behaviors should be generalized before they are promoted here:

- header/footer rule generation
- outline-assisted heading classification
- line/segment text transcription workflow
- vision-model review prompt templates that do not contain book text

## Migration Rule

Do not copy a source-book script here unchanged when it contains hard-coded
paths, source document names, page ids, generated dataset names, or copyrighted
text. Extract the reusable algorithm and add synthetic tests.
