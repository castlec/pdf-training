#!/usr/bin/env python3
"""Propose equation and figure masks for scanned page images.

This is a proposal-only tool. It does not edit masks or write annotations.
It sends page images to the OpenAI Responses API and asks for structured JSON
region suggestions that can be reviewed in the existing annotation editor.
"""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


API_URL = "https://api.openai.com/v1/responses"
DEFAULT_MODEL = "gpt-5.5"
DEFAULT_DETAIL = "original"


SYSTEM_PROMPT = """You are a visual mask proposal assistant for scanned technical-book pages.

Goal:
- Identify regions that should be masked as equations or figures/diagrams.
- Return only structured JSON.
- Do not transcribe the page.
- Do not invent page layout beyond what is visible in the image.

Label guidance:
- Use kind="equation" for mathematical expressions, equation blocks, and formula snippets.
- Use kind="figure" for vector diagrams, illustrations, charts, and any non-text diagram.
- Use kind="uncertain" only when you can see a region of interest but cannot classify it confidently.

Output schema:
{
  "source_image": "<filename>",
  "regions": [
    {
      "kind": "equation" | "figure" | "uncertain",
      "bbox": {"x": 0, "y": 0, "w": 0, "h": 0},
      "confidence": 0.0,
      "notes": "short reason"
    }
  ],
  "summary": "one short sentence"
}

Rules:
- Use pixel coordinates with origin at the top-left of the page image.
- Prefer over-marking slightly rather than missing a region.
- If a region is split across the page, return multiple boxes.
- Keep notes short and practical.
- Return JSON only, with no markdown fences.
"""


def image_to_data_url(path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type is None:
        mime_type = "image/png"
    data = path.read_bytes()
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def extract_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    chunks: list[str] = []
    for item in payload.get("output", []):
        if not isinstance(item, dict):
            continue
        content = item.get("content", [])
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            for key in ("text", "output_text"):
                value = part.get(key)
                if isinstance(value, str) and value.strip():
                    chunks.append(value)
    text = "".join(chunks).strip()
    if text:
        return text

    return json.dumps(payload, ensure_ascii=False, indent=2)


def extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.removeprefix("```json").removeprefix("```").strip()
        stripped = stripped.removesuffix("```").strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("Response did not contain JSON object text")
    return json.loads(stripped[start : end + 1])


def build_request(image_path: Path, detail: str, model: str, max_output_tokens: int) -> dict[str, Any]:
    prompt_text = f"{SYSTEM_PROMPT}\nSource image: {image_path.name}"
    return {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": prompt_text},
                    {
                        "type": "input_image",
                        "image_url": image_to_data_url(image_path),
                        "detail": detail,
                    },
                ],
            }
        ],
        "max_output_tokens": max_output_tokens,
    }


def call_responses_api(api_key: str, request_body: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("images", nargs="+", type=Path, help="One or more page image paths")
    parser.add_argument("--output-dir", type=Path, help="Directory to write one JSON file per image")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="OpenAI model to use")
    parser.add_argument("--detail", default=DEFAULT_DETAIL, choices=["low", "high", "original", "auto"])
    parser.add_argument("--max-output-tokens", type=int, default=1200)
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--dry-run", action="store_true", help="Print the request JSON and exit")
    args = parser.parse_args()

    api_key = os.environ.get(args.api_key_env, "").strip()
    if not api_key and not args.dry_run:
        raise SystemExit(f"Missing API key in ${args.api_key_env}")

    if args.output_dir is not None:
        args.output_dir.mkdir(parents=True, exist_ok=True)

    exit_code = 0
    for image_path in args.images:
        if not image_path.exists():
            print(f"Missing image: {image_path}", file=sys.stderr)
            exit_code = 1
            continue

        request_body = build_request(image_path, args.detail, args.model, args.max_output_tokens)
        if args.dry_run:
            print(json.dumps(request_body, ensure_ascii=False, indent=2))
            continue

        try:
            payload = call_responses_api(api_key, request_body)
            text = extract_text(payload)
            result = extract_json(text)
        except (urllib.error.HTTPError, urllib.error.URLError) as exc:
            print(f"{image_path}: API error: {exc}", file=sys.stderr)
            exit_code = 1
            continue
        except Exception as exc:
            print(f"{image_path}: could not parse response: {exc}", file=sys.stderr)
            if args.output_dir is None:
                print(text if "text" in locals() else "", file=sys.stderr)
            exit_code = 1
            continue

        if "source_image" not in result:
            result["source_image"] = image_path.name

        if args.output_dir is not None:
            output_path = args.output_dir / f"{image_path.stem}.mask-proposals.json"
            output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(output_path)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
