#!/usr/bin/env python3
"""Export one or more crop manifests as a portable canonical OCR dataset."""

from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any


def safe_id(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    if not result:
        raise ValueError(f"Sample id has no portable characters: {value!r}")
    return result


def load_samples(paths: list[Path]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for manifest_path in paths:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
        for raw in data["samples"]:
            sample_id = safe_id(str(raw["id"]))
            lines = [str(line) for line in raw["lines"]]
            image = Path(raw["image"])
            if not image.exists():
                raise FileNotFoundError(f"Missing image for {sample_id}: {image}")
            candidate = {
                "id": sample_id,
                "source_image": image,
                "lines": lines,
                "split": str(raw.get("split", "train")),
                "psm": int(raw.get("psm", 7)),
                "tags": sorted({str(tag) for tag in raw.get("tags", [])}),
            }
            previous = merged.get(sample_id)
            if previous is None:
                merged[sample_id] = candidate
                continue
            if previous["lines"] != lines:
                raise ValueError(f"Conflicting ground truth for sample {sample_id}")
            if previous["source_image"].read_bytes() != image.read_bytes():
                raise ValueError(f"Conflicting images for sample {sample_id}")
            previous["tags"] = sorted(set(previous["tags"]) | set(candidate["tags"]))
            if candidate["split"] == "eval":
                previous["split"] = "eval"
    return [merged[key] for key in sorted(merged)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifests", nargs="+", type=Path)
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(f"Output already exists: {args.output}")
    samples = load_samples(args.manifests)
    images_dir = args.output / "images"
    ground_truth_dir = args.output / "ground-truth"
    images_dir.mkdir(parents=True)
    ground_truth_dir.mkdir(parents=True)

    exported = []
    for sample in samples:
        image_relative = Path("images") / f"{sample['id']}.png"
        gt_relative = Path("ground-truth") / f"{sample['id']}.gt.txt"
        shutil.copy2(sample["source_image"], args.output / image_relative)
        (args.output / gt_relative).write_text("\n".join(sample["lines"]) + "\n", encoding="utf-8")
        exported.append(
            {
                "id": sample["id"],
                "image": str(image_relative),
                "ground_truth": str(gt_relative),
                "lines": sample["lines"],
                "split": sample["split"],
                "psm": sample["psm"],
                "tags": sample["tags"],
            }
        )

    manifest = {
        "schema": "pdf-training-ocr-dataset-v1",
        "name": args.name,
        "samples": exported,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    train = sum(sample["split"] == "train" for sample in exported)
    evaluation = len(exported) - train
    print(f"Exported {len(exported)} samples ({train} train, {evaluation} eval)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
