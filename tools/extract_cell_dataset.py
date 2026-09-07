#!/usr/bin/env python3
"""Extract labeled OCR samples from configured rectangular page cells."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def normalize_contrast(image: np.ndarray, scale: int = 2, border_white: int = 0) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    lo, hi = np.percentile(gray, (1.0, 99.5))
    if hi > lo:
        gray = np.clip((gray.astype(np.float32) - lo) * 255.0 / (hi - lo), 0, 255).astype(np.uint8)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    border = min(max(0, border_white), gray.shape[0] // 3, gray.shape[1] // 3)
    if border:
        gray[:border, :] = 255
        gray[-border:, :] = 255
        gray[:, :border] = 255
        gray[:, -border:] = 255
    if scale > 1:
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return gray


def extract(config_path: Path, output_root: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    page_path = Path(config["source_image"])
    image = cv2.imread(str(page_path), cv2.IMREAD_COLOR)
    if image is None:
        raise FileNotFoundError(f"Could not read source image: {page_path}")

    images_dir = output_root / "images"
    ground_truth_dir = output_root / "ground-truth"
    images_dir.mkdir(parents=True, exist_ok=True)
    ground_truth_dir.mkdir(parents=True, exist_ok=True)
    preprocess = config.get("preprocess", {})
    scale = int(preprocess.get("scale", 2))
    border_white = int(preprocess.get("border_white", 0))
    samples = []
    for raw in config["samples"]:
        sample_id = str(raw["id"])
        x, y, w, h = (int(raw["bbox"][key]) for key in ("x", "y", "w", "h"))
        crop = image[y : y + h, x : x + w]
        if crop.size == 0:
            raise ValueError(f"Empty crop for {sample_id}: {(x, y, w, h)}")
        cleaned = normalize_contrast(crop, scale=scale, border_white=border_white)
        image_rel = Path("images") / f"{sample_id}.png"
        gt_rel = Path("ground-truth") / f"{sample_id}.gt.txt"
        if not cv2.imwrite(str(output_root / image_rel), cleaned):
            raise RuntimeError(f"Could not write {output_root / image_rel}")
        lines = [str(line) for line in raw["lines"]]
        (output_root / gt_rel).write_text("\n".join(lines) + "\n", encoding="utf-8")
        samples.append(
            {
                "id": sample_id,
                "image": str(image_rel),
                "ground_truth": str(gt_rel),
                "lines": lines,
                "split": str(raw.get("split", "train")),
                "psm": int(raw.get("psm", 7)),
                "tags": sorted({str(tag) for tag in raw.get("tags", [])}),
            }
        )

    manifest = {
        "schema": "pdf-training-ocr-dataset-v1",
        "name": str(config["name"]),
        "private_source": True,
        "samples": samples,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise FileExistsError(f"Output is not empty: {args.output}")
    manifest = extract(args.config, args.output)
    print(f"Extracted {len(manifest['samples'])} samples to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
