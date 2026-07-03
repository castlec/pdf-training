#!/usr/bin/env python3
"""Build a Tesseract charset starter by extending an existing traineddata."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path


def run(command: list[str], cwd: Path | None = None) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-traineddata", required=True, type=Path)
    parser.add_argument("--box", required=True, type=Path, help="Box file containing characters to add.")
    parser.add_argument("--script-dir", required=True, type=Path, help="Tesseract langdata directory.")
    parser.add_argument("--lang", required=True, help="Language name for the generated starter.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise FileExistsError(f"Output already exists: {args.output}")
    args.output.mkdir(parents=True)
    extracted = args.output / "base" / "model"
    extracted.parent.mkdir(parents=True)
    run(["combine_tessdata", "-u", str(args.base_traineddata), str(extracted) + "."])
    base_unicharset = extracted.with_suffix(".lstm-unicharset")
    if not base_unicharset.exists():
        raise FileNotFoundError(f"Base model has no LSTM unicharset: {base_unicharset}")

    with tempfile.TemporaryDirectory(prefix="pdf-training-charset-") as temp:
        temp_dir = Path(temp)
        run(["unicharset_extractor", "--norm_mode", "2", str(args.box.resolve())], cwd=temp_dir)
        additions = temp_dir / "unicharset"
        merged = args.output / f"{args.lang}.unicharset"
        run(["merge_unicharsets", str(base_unicharset), str(additions), str(merged)])
        run(
            [
                "combine_lang_model",
                "--input_unicharset",
                str(merged),
                "--script_dir",
                str(args.script_dir),
                "--output_dir",
                str(args.output),
                "--lang",
                args.lang,
                "--version_str",
                args.version,
            ]
        )

    starter = args.output / args.lang / f"{args.lang}.traineddata"
    if not starter.exists():
        raise FileNotFoundError(f"Starter was not created: {starter}")
    shutil.copy2(args.box, args.output / args.box.name)
    print(starter)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
