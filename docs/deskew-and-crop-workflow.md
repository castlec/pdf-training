# Deskew and Crop Workflow

This workflow preserves the small, manually reviewed state needed to rebuild
page geometry without retaining source books or generated page caches.

## Persistent State

Keep these directories in the private book workspace:

- `crop-annotations/`: representative frame annotations used to infer source
  dimensions, margins, and page skew.
- `crop-origin-annotations/`: the shared crop model and one origin, side, and
  optional deskew exception per page.

Source PDFs and rendered pages may be copyrighted. Do not copy them into this
repository or publish them unless redistribution rights are documented.

## Process

1. Annotate representative page frames:

   ```bash
   python3 tools/crop_annotation_server.py \
     --pdf-dir /private/book/pdfs \
     --source-root /private/book/pages \
     --crop-root /private/book/crop-annotations
   ```

2. Render PDFs, estimate skew from horizontal structures, rotate pages, and
   initialize blank text/image masks:

   ```bash
   python3 tools/initialize_pages.py /private/book/pdfs/*.pdf \
     --model-root /private/book/crop-annotations \
     --output /private/book/deskewed-pages
   ```

3. Apply the representative crop model and inspect parity alignment:

   ```bash
   python3 tools/apply_crop_annotations.py \
     --source-root /private/book/deskewed-pages \
     --crop-root /private/book/crop-annotations \
     --output-root /private/book/cropped-pages

   python3 tools/crop_compare_server.py \
     --root /private/book/deskewed-pages \
     --model-root /private/book/crop-annotations
   ```

   Use `--x-bias`, `--outer-trim`, `--scale-x`, and `--scale-y` only when a
   scan needs explicit calibration. Canonical defaults are neutral.

4. Seed and review every page origin. Per-page deskew settings are rendered
   around the saved deskew origin:

   ```bash
   python3 tools/crop_origin_server.py \
     --source-root /private/book/deskewed-pages \
     --seed-root /private/book/cropped-pages \
     --dataset-root /private/book/crop-origin-annotations

   python3 tools/apply_crop_origins.py \
     --source-root /private/book/deskewed-pages \
     --dataset-root /private/book/crop-origin-annotations \
     --output-root /private/book/origin-cropped-pages
   ```

5. Repaint the declared safe margins after origin cropping. This removes
   scanner shadows without altering page content or annotation coordinates:

   ```bash
   python3 tools/repaint_page_margins.py \
     --source-root /private/book/origin-cropped-pages \
     --state-root /private/book/crop-origin-annotations \
     --output-root /private/book/clean-pages
   ```

The generated `pages/`, `deskewed-pages/`, `cropped-pages/`, and
`origin-cropped-pages/`, and `clean-pages/` trees can be rebuilt from the
private PDFs plus the two annotation-state directories.
