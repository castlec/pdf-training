#!/usr/bin/env python3
"""Fine-tune and evaluate Tesseract LSTM models from a portable crop manifest."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps


DEFAULT_TESSDATA = Path("/usr/share/tesseract-ocr/4.00/tessdata")
DEFAULT_MANIFEST = Path("dataset/manifest.json")
DEFAULT_OUTPUT = Path("runs/tesseract-finetune")
DEFAULT_WHITELIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_+=-*/().,:;[]^ "
TESSDATA_BEST_BASE = "https://github.com/tesseract-ocr/tessdata_best/raw/main"
LANCZOS = getattr(Image, "Resampling", Image).LANCZOS


@dataclass
class Sample:
    sample_id: str
    image_path: Path
    lines: list[str]
    psm: int
    split: str
    tags: list[str]


def run(cmd: list[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd is not None else None,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "Command failed:\n"
            + " ".join(cmd)
            + "\n\nstdout:\n"
            + proc.stdout
            + "\n\nstderr:\n"
            + proc.stderr
        )
    return proc


def load_manifest(path: Path) -> tuple[list[Sample], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    manifest_dir = path.resolve().parent
    samples = []
    for entry in data["samples"]:
        image_path = Path(entry["image"])
        if not image_path.is_absolute():
            image_path = manifest_dir / image_path
        samples.append(
            Sample(
                sample_id=str(entry["id"]),
                image_path=image_path,
                lines=[str(line) for line in entry["lines"]],
                psm=int(entry.get("psm", 6)),
                split=str(entry.get("split", "train")),
                tags=[str(tag) for tag in entry.get("tags", [])],
            )
        )
    return samples, data


def flatten_to_white(image_path: Path) -> Image.Image:
    src = Image.open(image_path).convert("RGBA")
    base = Image.new("RGBA", src.size, (255, 255, 255, 255))
    base.alpha_composite(src)
    return base.convert("L")


def otsu_threshold(image: Image.Image) -> int:
    histogram = image.histogram()
    total = sum(histogram)
    weighted_sum = sum(index * count for index, count in enumerate(histogram))
    background_weight = 0
    background_sum = 0
    best_variance = -1.0
    threshold = 127

    for index, count in enumerate(histogram):
        background_weight += count
        if background_weight == 0:
            continue
        foreground_weight = total - background_weight
        if foreground_weight == 0:
            break
        background_sum += index * count
        background_mean = background_sum / background_weight
        foreground_mean = (weighted_sum - background_sum) / foreground_weight
        between_variance = background_weight * foreground_weight * (background_mean - foreground_mean) ** 2
        if between_variance > best_variance:
            best_variance = between_variance
            threshold = index
    return threshold


def preprocess_image(image_path: Path, mode: str) -> Image.Image:
    gray = flatten_to_white(image_path)
    if mode == "none":
        return gray

    enhanced = ImageOps.autocontrast(gray, cutoff=1)
    enhanced = enhanced.resize((enhanced.width * 2, enhanced.height * 2), LANCZOS)
    if mode == "contrast2x":
        return enhanced
    if mode == "binary2x":
        threshold = otsu_threshold(enhanced)
        return enhanced.point(lambda value: 255 if value > threshold else 0)
    raise ValueError(f"Unknown preprocessing mode: {mode}")


def content_bbox(gray: np.ndarray, threshold: int = 220) -> tuple[int, int, int, int]:
    dark = gray < threshold
    ys, xs = np.where(dark)
    if len(xs) == 0 or len(ys) == 0:
        h, w = gray.shape
        return 0, 0, w, h
    x1 = int(xs.min())
    x2 = int(xs.max()) + 1
    y1 = int(ys.min())
    y2 = int(ys.max()) + 1
    return x1, y1, x2, y2


def row_runs(gray: np.ndarray, threshold: int = 180, min_dark_pixels: int = 2, min_run_height: int = 5) -> list[tuple[int, int]]:
    dark = gray < threshold
    counts = dark.sum(axis=1)
    runs: list[tuple[int, int]] = []
    start: int | None = None
    for i, count in enumerate(counts):
        if count >= min_dark_pixels and start is None:
            start = i
        elif count < min_dark_pixels and start is not None:
            if i - start >= min_run_height:
                runs.append((start, i))
            start = None
    if start is not None and len(counts) - start >= min_run_height:
        runs.append((start, len(counts)))
    return runs


def merge_runs_to_count(runs: list[tuple[int, int]], target_count: int) -> list[tuple[int, int]]:
    if not runs:
        return []
    merged = list(runs)
    while len(merged) > target_count:
        best_index = 0
        best_gap = None
        for i in range(len(merged) - 1):
            gap = merged[i + 1][0] - merged[i][1]
            if best_gap is None or gap < best_gap:
                best_gap = gap
                best_index = i
        merged[best_index : best_index + 2] = [(merged[best_index][0], merged[best_index + 1][1])]
    return merged


def split_run_evenly(run: tuple[int, int], parts: int) -> list[tuple[int, int]]:
    start, end = run
    height = max(1, end - start)
    piece = height / parts
    out: list[tuple[int, int]] = []
    for idx in range(parts):
        a = int(round(start + idx * piece))
        b = int(round(start + (idx + 1) * piece))
        if b <= a:
            b = a + 1
        out.append((a, b))
    return out


def expand_runs_to_count(runs: list[tuple[int, int]], target_count: int) -> list[tuple[int, int]]:
    if not runs:
        return []
    if len(runs) >= target_count:
        return runs
    out = list(runs)
    while len(out) < target_count:
        largest_index = max(range(len(out)), key=lambda i: out[i][1] - out[i][0])
        current = out.pop(largest_index)
        pieces = split_run_evenly(current, 2)
        out[largest_index:largest_index] = pieces
    return sorted(out)


def detect_line_bboxes(gray: np.ndarray, expected_lines: int, padding: int = 4) -> list[tuple[int, int, int, int]]:
    if expected_lines <= 1:
        x1, y1, x2, y2 = content_bbox(gray)
        h, w = gray.shape
        return [(
            max(0, x1 - padding),
            max(0, y1 - padding),
            min(w, x2 + padding),
            min(h, y2 + padding),
        )]

    runs = row_runs(gray)
    runs = merge_runs_to_count(runs, expected_lines)
    runs = expand_runs_to_count(runs, expected_lines)
    h, w = gray.shape
    boxes: list[tuple[int, int, int, int]] = []
    for y1, y2 in runs[:expected_lines]:
        strip = gray[y1:y2, :]
        sx1, sy1, sx2, sy2 = content_bbox(strip)
        boxes.append(
            (
                max(0, sx1 - padding),
                max(0, y1 + sy1 - padding),
                min(w, sx2 + padding),
                min(h, y1 + sy2 + padding),
            )
        )
    return boxes


def write_gt_and_box(base_path: Path, lines: list[str], boxes: list[tuple[int, int, int, int]], image_height: int) -> None:
    gt_text = "\n".join(lines) + "\n"
    base_path.with_suffix(".gt.txt").write_text(gt_text, encoding="utf-8")
    with base_path.with_suffix(".box").open("w", encoding="utf-8") as handle:
        for text, (x1, y1, x2, y2) in zip(lines, boxes, strict=True):
            by1 = image_height - y2
            by2 = image_height - y1
            for ch in text:
                handle.write(f"{ch} {x1} {by1} {x2} {by2} 0\n")
            handle.write(f"\t {x1} {by1} {x2} {by2} 0\n")


def prepare_samples(
    samples: list[Sample],
    out_dir: Path,
    preprocess: str,
    lstmf_tessdata_dir: Path | None = None,
    lstmf_lang: str | None = None,
) -> list[dict[str, Any]]:
    prepared_dir = out_dir / "prepared"
    prepared_dir.mkdir(parents=True, exist_ok=True)
    prepared: list[dict[str, Any]] = []
    for sample in samples:
        gray = preprocess_image(sample.image_path, preprocess)
        base = prepared_dir / sample.sample_id
        gray.save(base.with_suffix(".png"))
        arr = np.array(gray)
        boxes = detect_line_bboxes(arr, expected_lines=len(sample.lines))
        if len(boxes) != len(sample.lines):
            raise RuntimeError(f"{sample.sample_id}: expected {len(sample.lines)} boxes, got {len(boxes)}")
        write_gt_and_box(base, sample.lines, boxes, gray.height)
        command = ["tesseract", str(base.with_suffix(".png")), str(base)]
        if lstmf_tessdata_dir is not None:
            command.extend(["--tessdata-dir", str(lstmf_tessdata_dir)])
        if lstmf_lang is not None:
            command.extend(["-l", lstmf_lang])
        command.extend(["--psm", str(sample.psm), "--dpi", "300", "lstm.train"])
        run(command)
        prepared.append(
            {
                "id": sample.sample_id,
                "image": str(base.with_suffix(".png")),
                "gt_lines": sample.lines,
                "psm": sample.psm,
                "split": sample.split,
                "tags": sample.tags,
                "preprocess": preprocess,
                "line_bboxes": [
                    {"x": int(x1), "y": int(y1), "w": int(x2 - x1), "h": int(y2 - y1)}
                    for x1, y1, x2, y2 in boxes
                ],
                "lstmf": str(base.with_suffix(".lstmf")),
            }
        )
    return prepared


def write_listfiles(prepared: list[dict[str, Any]], out_dir: Path) -> tuple[Path, Path]:
    train = [item["lstmf"] for item in prepared if item["split"] == "train"]
    eval_items = [item["lstmf"] for item in prepared if item["split"] == "eval"]
    if not train:
        raise RuntimeError("No training samples in manifest.")
    if not eval_items:
        raise RuntimeError("No eval samples in manifest.")
    train_list = out_dir / "train.list"
    eval_list = out_dir / "eval.list"
    train_list.write_text("\n".join(train) + "\n", encoding="utf-8")
    eval_list.write_text("\n".join(eval_items) + "\n", encoding="utf-8")
    return train_list, eval_list


def download_file(url: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url) as response, out_path.open("wb") as handle:
        shutil.copyfileobj(response, handle)


def ensure_best_traineddata(out_dir: Path, base_lang: str, explicit_tessdata_dir: Path | None) -> Path:
    if explicit_tessdata_dir is not None:
        traineddata = explicit_tessdata_dir / f"{base_lang}.traineddata"
        if not traineddata.exists():
            raise FileNotFoundError(f"Missing base traineddata: {traineddata}")
        return traineddata

    best_dir = out_dir / "base_tessdata"
    traineddata = best_dir / f"{base_lang}.traineddata"
    if not traineddata.exists():
        download_file(f"{TESSDATA_BEST_BASE}/{base_lang}.traineddata", traineddata)
    return traineddata


def extract_base_lstm(traineddata: Path, out_dir: Path, base_lang: str) -> tuple[Path, Path]:
    extracted_prefix = out_dir / "base" / base_lang
    extracted_prefix.parent.mkdir(parents=True, exist_ok=True)
    lstm_path = extracted_prefix.with_suffix(".lstm")
    if not lstm_path.exists():
        run(["combine_tessdata", "-u", str(traineddata), str(extracted_prefix)])
    if not lstm_path.exists():
        raise FileNotFoundError(f"Expected extracted LSTM at {lstm_path}")
    return traineddata, lstm_path


def finetune_model(
    *,
    traineddata: Path,
    continue_from: Path,
    train_list: Path,
    eval_list: Path,
    output_dir: Path,
    model_name: str,
    max_iterations: int,
    old_traineddata: Path | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    model_prefix = output_dir / model_name
    command = [
        "lstmtraining",
        "--continue_from",
        str(continue_from),
        "--traineddata",
        str(traineddata),
    ]
    if old_traineddata is not None:
        command.extend(["--old_traineddata", str(old_traineddata)])
    command.extend(
        [
            "--train_listfile",
            str(train_list),
            "--eval_listfile",
            str(eval_list),
            "--max_iterations",
            str(max_iterations),
            "--debug_interval",
            "0",
            "--model_output",
            str(model_prefix),
        ]
    )
    run(command)
    checkpoint = model_prefix.with_name(model_prefix.name + "_checkpoint")
    if not checkpoint.exists():
        raise FileNotFoundError(f"Missing checkpoint after training: {checkpoint}")
    final_traineddata = output_dir / f"{model_name}.traineddata"
    run(
        [
            "lstmtraining",
            "--stop_training",
            "--continue_from",
            str(checkpoint),
            "--traineddata",
            str(traineddata),
            "--model_output",
            str(final_traineddata),
        ]
    )
    return final_traineddata


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur.append(min(cur[-1] + 1, prev[j] + 1, prev[j - 1] + cost))
        prev = cur
    return prev[-1]


def normalize_ocr_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [" ".join(line.strip().split()) for line in text.split("\n")]
    return "\n".join(line for line in lines if line)


def ocr_with_model(
    image_path: Path,
    *,
    tessdata_dir: Path,
    lang: str,
    psm: int,
    whitelist: str | None,
) -> str:
    cmd = [
        "tesseract",
        str(image_path),
        "stdout",
        "--tessdata-dir",
        str(tessdata_dir),
        "-l",
        lang,
        "--psm",
        str(psm),
        "--dpi",
        "300",
    ]
    if whitelist:
        cmd.extend(["-c", f"tessedit_char_whitelist={whitelist}"])
    proc = run(cmd)
    return normalize_ocr_text(proc.stdout)


def evaluate(
    prepared: list[dict[str, Any]],
    *,
    baseline_tessdata_dir: Path,
    baseline_lang: str,
    tuned_tessdata_dir: Path,
    tuned_lang: str,
    whitelist: str | None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    totals = {
        "baseline_char_edits": 0,
        "tuned_char_edits": 0,
        "char_total": 0,
    }
    grouped: dict[str, dict[str, int]] = {}
    for item in prepared:
        gt = "\n".join(item["gt_lines"])
        image_path = Path(item["image"])
        psm = int(item["psm"])
        baseline_text = ocr_with_model(
            image_path,
            tessdata_dir=baseline_tessdata_dir,
            lang=baseline_lang,
            psm=psm,
            whitelist=whitelist,
        )
        tuned_text = ocr_with_model(
            image_path,
            tessdata_dir=tuned_tessdata_dir,
            lang=tuned_lang,
            psm=psm,
            whitelist=whitelist,
        )
        baseline_edits = levenshtein(gt, baseline_text)
        tuned_edits = levenshtein(gt, tuned_text)
        char_total = max(1, len(gt))
        totals["baseline_char_edits"] += baseline_edits
        totals["tuned_char_edits"] += tuned_edits
        totals["char_total"] += char_total
        group_names = [f"split:{item['split']}", *[f"tag:{tag}" for tag in item.get("tags", [])]]
        for group_name in group_names:
            group = grouped.setdefault(
                group_name,
                {"baseline_char_edits": 0, "tuned_char_edits": 0, "char_total": 0, "samples": 0},
            )
            group["baseline_char_edits"] += baseline_edits
            group["tuned_char_edits"] += tuned_edits
            group["char_total"] += char_total
            group["samples"] += 1
        results.append(
            {
                "id": item["id"],
                "split": item["split"],
                "tags": item.get("tags", []),
                "ground_truth": gt,
                "baseline_text": baseline_text,
                "tuned_text": tuned_text,
                "baseline_char_edits": baseline_edits,
                "tuned_char_edits": tuned_edits,
                "baseline_cer": baseline_edits / char_total,
                "tuned_cer": tuned_edits / char_total,
            }
        )
    group_summary = {
        name: {
            **values,
            "baseline_cer": values["baseline_char_edits"] / max(1, values["char_total"]),
            "tuned_cer": values["tuned_char_edits"] / max(1, values["char_total"]),
        }
        for name, values in sorted(grouped.items())
    }
    return {
        "samples": results,
        "summary": {
            "baseline_cer": totals["baseline_char_edits"] / max(1, totals["char_total"]),
            "tuned_cer": totals["tuned_char_edits"] / max(1, totals["char_total"]),
            "char_total": totals["char_total"],
            "baseline_char_edits": totals["baseline_char_edits"],
            "tuned_char_edits": totals["tuned_char_edits"],
        },
        "groups": group_summary,
    }


def ensure_eval_split(samples: list[Sample]) -> None:
    splits = {sample.split for sample in samples}
    if "train" not in splits or "eval" not in splits:
        raise RuntimeError("Manifest must include both train and eval samples.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--tessdata-dir",
        type=Path,
        default=None,
        help="Optional tessdata dir containing a float trainable base model. If omitted, the script downloads base_lang from the official tessdata_best repository.",
    )
    parser.add_argument("--baseline-tessdata-dir", type=Path, default=DEFAULT_TESSDATA)
    parser.add_argument(
        "--baseline-lang",
        default=None,
        help="Language used for baseline evaluation. Defaults to base-lang.",
    )
    parser.add_argument("--base-lang", default="eng")
    parser.add_argument("--continue-from", type=Path, default=None)
    parser.add_argument(
        "--old-traineddata",
        type=Path,
        default=None,
        help="Previous traineddata used to map an existing network to a changed charset.",
    )
    parser.add_argument("--lstmf-tessdata-dir", type=Path, default=None)
    parser.add_argument("--lstmf-lang", default=None)
    parser.add_argument("--model-name", default="equation")
    parser.add_argument("--max-iterations", type=int, default=800)
    parser.add_argument("--whitelist", default=DEFAULT_WHITELIST)
    parser.add_argument(
        "--preprocess",
        choices=("none", "contrast2x", "binary2x"),
        default="none",
        help="Image preprocessing applied consistently before training and evaluation.",
    )
    parser.add_argument("--keep-output", action="store_true", help="Do not wipe the output dir before running.")
    args = parser.parse_args()

    samples, manifest_meta = load_manifest(args.manifest)
    ensure_eval_split(samples)

    if args.output_dir.exists() and not args.keep_output:
        shutil.rmtree(args.output_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    prepared = prepare_samples(
        samples,
        args.output_dir,
        args.preprocess,
        lstmf_tessdata_dir=args.lstmf_tessdata_dir,
        lstmf_lang=args.lstmf_lang,
    )
    (args.output_dir / "prepared" / "prepared.json").write_text(json.dumps(prepared, indent=2, ensure_ascii=False), encoding="utf-8")
    train_list, eval_list = write_listfiles(prepared, args.output_dir)
    traineddata = ensure_best_traineddata(args.output_dir, args.base_lang, args.tessdata_dir)
    if args.continue_from is not None:
        if not args.continue_from.exists():
            raise FileNotFoundError(f"Missing continuation model: {args.continue_from}")
        base_lstm = args.continue_from
    else:
        _, base_lstm = extract_base_lstm(traineddata, args.output_dir, args.base_lang)
    final_traineddata = finetune_model(
        traineddata=traineddata,
        continue_from=base_lstm,
        train_list=train_list,
        eval_list=eval_list,
        output_dir=args.output_dir / "trained",
        model_name=args.model_name,
        max_iterations=args.max_iterations,
        old_traineddata=args.old_traineddata,
    )

    runtime_tessdata = args.output_dir / "tessdata"
    runtime_tessdata.mkdir(parents=True, exist_ok=True)
    shutil.copy2(final_traineddata, runtime_tessdata / f"{args.model_name}.traineddata")
    if args.base_lang != args.model_name:
        shutil.copy2(traineddata, runtime_tessdata / f"{args.base_lang}.traineddata")

    report = evaluate(
        prepared,
        baseline_tessdata_dir=args.baseline_tessdata_dir,
        baseline_lang=args.baseline_lang or args.base_lang,
        tuned_tessdata_dir=runtime_tessdata,
        tuned_lang=args.model_name,
        whitelist=args.whitelist,
    )
    report["manifest"] = str(args.manifest)
    report["output_dir"] = str(args.output_dir)
    report["base_lang"] = args.base_lang
    report["baseline_lang"] = args.baseline_lang or args.base_lang
    report["model_name"] = args.model_name
    report["max_iterations"] = args.max_iterations
    report["whitelist"] = args.whitelist
    report["preprocess"] = args.preprocess
    report["continue_from"] = str(base_lstm)
    report["old_traineddata"] = str(args.old_traineddata) if args.old_traineddata else None
    report["manifest_meta"] = manifest_meta

    report_path = args.output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"Prepared samples: {len(prepared)}")
    print(f"Train list: {train_list}")
    print(f"Eval list: {eval_list}")
    print(f"Trained model: {final_traineddata}")
    print(f"Report: {report_path}")
    print(
        "CER baseline -> tuned:",
        f"{report['summary']['baseline_cer']:.4f}",
        "->",
        f"{report['summary']['tuned_cer']:.4f}",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
