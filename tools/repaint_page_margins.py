#!/usr/bin/env python3
"""Copy cropped pages while repainting their declared outer margins white."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import cv2
import numpy as np


def margin_widths(state: dict, page_width: int, page_height: int) -> tuple[int, int, int, int]:
    margins = state.get("margins") or {}
    model_margins = (state.get("model") or {}).get("margins") or {}
    side = str(state.get("page_side") or state.get("side") or "")

    top = int(margins.get("top", model_margins.get("top", 0)) or 0)
    bottom = int(margins.get("bottom", model_margins.get("bottom", 0)) or 0)
    inner = int(margins.get("inner", model_margins.get("inner", 0)) or 0)
    outer = int(margins.get("outer", model_margins.get("outer", 0)) or 0)
    left = margins.get("left")
    right = margins.get("right")
    if left is None:
        left = inner if side == "right" else outer
    if right is None:
        right = outer if side == "right" else inner

    return (
        min(max(0, int(left or 0)), page_width),
        min(max(0, int(right or 0)), page_width),
        min(max(0, top), page_height),
        min(max(0, bottom), page_height),
    )


def repaint(image: np.ndarray, state: dict) -> np.ndarray:
    result = image.copy()
    height, width = result.shape[:2]
    left, right, top, bottom = margin_widths(state, width, height)
    white = 255 if result.ndim == 2 else (255,) * result.shape[2]
    if left:
        result[:, :left] = white
    if right:
        result[:, width - right :] = white
    if top:
        result[:top, :] = white
    if bottom:
        result[height - bottom :, :] = white
    return result


def process_document(source_root: Path, state_root: Path, output_root: Path, document: str) -> int:
    source_pages = source_root / document / "pages"
    source_state = state_root / document / "state"
    output_pages = output_root / document / "pages"
    output_state = output_root / document / "state"
    output_pages.mkdir(parents=True, exist_ok=True)
    output_state.mkdir(parents=True, exist_ok=True)

    count = 0
    for page_path in sorted(source_pages.glob(f"{document}-*.png")):
        page = int(page_path.stem.rsplit("-", 1)[-1])
        candidates = [
            source_state / f"{document}-{page:02d}.json",
            source_state / f"page-{page:03d}.json",
        ]
        state_path = next((path for path in candidates if path.exists()), None)
        if state_path is None:
            raise FileNotFoundError(f"Missing crop state for {document} page {page}")
        state = json.loads(state_path.read_text(encoding="utf-8"))
        image = cv2.imread(str(page_path), cv2.IMREAD_UNCHANGED)
        if image is None:
            raise RuntimeError(f"Could not read {page_path}")
        cleaned = repaint(image, state)
        output_path = output_pages / page_path.name
        if not cv2.imwrite(str(output_path), cleaned):
            raise RuntimeError(f"Could not write {output_path}")
        shutil.copy2(state_path, output_state / state_path.name)
        count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--document", action="append", dest="documents")
    args = parser.parse_args()

    state_root = args.state_root or args.source_root
    documents = args.documents or sorted(path.name for path in args.source_root.iterdir() if path.is_dir())
    if args.output_root.exists() and any(args.output_root.iterdir()):
        raise FileExistsError(f"Output root is not empty: {args.output_root}")

    total = 0
    for document in documents:
        total += process_document(args.source_root, state_root, args.output_root, document)
    print(f"Repainted {total} pages in {args.output_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
