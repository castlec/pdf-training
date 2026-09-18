#!/usr/bin/env python3
"""Extract a lossless-enough, source-independent PDF intermediate representation."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from typing import Any

import pikepdf

SCHEMA = "pdf-training-pdf-ir-v1"


def name_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return repr(value).removeprefix("pikepdf.Name(").removesuffix(")").strip("'\"")


def scalar(value: Any) -> Any:
    ref = ref_for(value)
    if ref is not None:
        return {"ref": ref}
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, pikepdf.Name):
        return {"type": "name", "value": name_value(value)}
    if isinstance(value, pikepdf.String):
        return {"type": "string", "value": str(value)}
    if isinstance(value, (pikepdf.Array, list, tuple)):
        return [scalar(item) for item in value]
    if isinstance(value, (pikepdf.Dictionary, dict)):
        return {name_value(key): scalar(item) for key, item in value.items()}
    try:
        return float(value)
    except (TypeError, ValueError):
        return {"type": type(value).__name__, "repr": repr(value)}


def ref_for(value: Any) -> str | None:
    if not isinstance(value, (pikepdf.Stream, pikepdf.Dictionary, pikepdf.Array)):
        return None
    objgen = getattr(value, "objgen", (0, 0))
    if objgen != (0, 0):
        return f"{objgen[0]} {objgen[1]}"
    return None


class ObjectGraph:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, Any]] = {}

    def add(self, value: Any) -> str | None:
        ref = ref_for(value)
        if ref is None:
            if isinstance(value, (pikepdf.Stream, pikepdf.Dictionary, dict)):
                for _, item in value.items():
                    self.add(item)
            elif isinstance(value, (pikepdf.Array, list, tuple)):
                for item in value:
                    self.add(item)
            return None
        if ref in self.objects:
            return ref
        record: dict[str, Any] = {"ref": ref}
        if isinstance(value, pikepdf.Stream):
            record["kind"] = "stream"
            record["dictionary"] = {name_value(key): scalar(item) for key, item in value.items()}
            record["raw_bytes_b64"] = base64.b64encode(value.read_raw_bytes()).decode("ascii")
            record["decoded_bytes_b64"] = base64.b64encode(value.read_bytes()).decode("ascii")
            record["filter"] = scalar(value.get("/Filter"))
        elif isinstance(value, (pikepdf.Dictionary, dict)):
            record["kind"] = "dictionary"
            record["values"] = {}
        elif isinstance(value, pikepdf.Array):
            record["kind"] = "array"
            record["values"] = [scalar(item) for item in value]
        else:
            record["kind"] = "value"
            try:
                record["value"] = int(value) if int(value) == float(value) else float(value)
            except (TypeError, ValueError):
                record["value"] = scalar(value)
        self.objects[ref] = record
        if record["kind"] in {"stream", "dictionary"}:
            for key, item in value.items():
                item_ref = self.add(item)
                if record["kind"] == "dictionary":
                    record["values"][name_value(key)] = {"ref": item_ref} if item_ref else scalar(item)
        elif record["kind"] == "array":
            for item in value:
                self.add(item)
        return ref


def typed_operation(instruction: Any, ordinal: int) -> dict[str, Any]:
    operands = []
    for operand in instruction.operands:
        ref = ref_for(operand)
        operands.append({"ref": ref} if ref else scalar(operand))
    return {"ordinal": ordinal, "operator": str(instruction.operator), "operands": operands}


_SCOPE_OPENERS = {"q": ("graphics_state", "Q"), "BT": ("text_state", "ET")}


def _matrix_product(left: list[float], right: list[float]) -> list[float]:
    return [
        left[0] * right[0] + left[2] * right[1],
        left[1] * right[0] + left[3] * right[1],
        left[0] * right[2] + left[2] * right[3],
        left[1] * right[2] + left[3] * right[3],
        left[0] * right[4] + left[2] * right[5] + left[4],
        left[1] * right[4] + left[3] * right[5] + left[5],
    ]


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or isinstance(value, (dict, list)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _state_snapshot(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "graphics": {
            "ctm": list(state["graphics"]["ctm"]),
            "line_width": state["graphics"]["line_width"],
            "stroke_gray": state["graphics"]["stroke_gray"],
            "fill_gray": state["graphics"]["fill_gray"],
            "stroke_color": list(state["graphics"]["stroke_color"]),
            "fill_color": list(state["graphics"]["fill_color"]),
        },
        "text": {
            "in_text": state["text"]["in_text"],
            "font": state["text"]["font"],
            "font_size": state["text"]["font_size"],
            "text_matrix": list(state["text"]["text_matrix"]),
            "text_line_matrix": list(state["text"]["text_line_matrix"]),
            "leading": state["text"]["leading"],
        },
    }


def _initial_state() -> dict[str, Any]:
    identity = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    return {
        "graphics": {"ctm": identity, "line_width": 1.0, "stroke_gray": 0.0, "fill_gray": 0.0,
                     "stroke_color": [0.0, 0.0, 0.0], "fill_color": [0.0, 0.0, 0.0]},
        "text": {"in_text": False, "font": None, "font_size": None, "text_matrix": list(identity),
                 "text_line_matrix": list(identity), "leading": 0.0},
        "graphics_stack": [],
    }


def _apply_state_operator(state: dict[str, Any], item: dict[str, Any]) -> None:
    operator = str(item["operator"])
    operands = item.get("operands") or []
    graphics = state["graphics"]
    text = state["text"]
    values = [_number(value) for value in operands]
    if operator == "q":
        state["graphics_stack"].append(_state_snapshot({"graphics": graphics, "text": text}))
    elif operator == "Q" and state["graphics_stack"]:
        saved = state["graphics_stack"].pop()["graphics"]
        state["graphics"] = saved
    elif operator == "cm" and len(values) >= 6 and all(value is not None for value in values[:6]):
        graphics["ctm"] = _matrix_product([float(value) for value in values[:6]], graphics["ctm"])
    elif operator == "BT":
        text["in_text"] = True
        text["text_matrix"] = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        text["text_line_matrix"] = list(text["text_matrix"])
    elif operator == "ET":
        text["in_text"] = False
    elif operator == "Tf" and len(operands) >= 2:
        text["font"] = operands[0]
        text["font_size"] = values[1]
    elif operator == "Tm" and len(values) >= 6 and all(value is not None for value in values[:6]):
        matrix = [float(value) for value in values[:6]]
        text["text_matrix"] = matrix
        text["text_line_matrix"] = list(matrix)
    elif operator in {"Td", "TD"} and len(values) >= 2 and all(value is not None for value in values[:2]):
        tx, ty = float(values[0]), float(values[1])
        if operator == "TD":
            text["leading"] = -ty
        translated = _matrix_product(text["text_line_matrix"], [1.0, 0.0, 0.0, 1.0, tx, ty])
        text["text_line_matrix"] = translated
        text["text_matrix"] = list(translated)
    elif operator == "T*":
        translated = _matrix_product(text["text_line_matrix"], [1.0, 0.0, 0.0, 1.0, 0.0, -float(text["leading"])])
        text["text_line_matrix"] = translated
        text["text_matrix"] = list(translated)
    elif operator == "TL" and values and values[0] is not None:
        text["leading"] = float(values[0])
    elif operator == "w" and values and values[0] is not None:
        graphics["line_width"] = float(values[0])
    elif operator == "G" and values and values[0] is not None:
        graphics["stroke_gray"] = float(values[0])
    elif operator == "g" and values and values[0] is not None:
        graphics["fill_gray"] = float(values[0])
    elif operator == "RG" and len(values) >= 3 and all(value is not None for value in values[:3]):
        graphics["stroke_color"] = [float(value) for value in values[:3]]
    elif operator == "rg" and len(values) >= 3 and all(value is not None for value in values[:3]):
        graphics["fill_color"] = [float(value) for value in values[:3]]


def structured_operations(operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a lossless syntactic tree over raw operations without semantic classification."""
    root = {"type": "operation_sequence", "children": []}
    stack: list[dict[str, Any]] = [root]
    diagnostics: list[dict[str, Any]] = []
    state = _initial_state()

    for item in operations:
        ordinal = int(item["ordinal"])
        operator = str(item["operator"])
        if operator in _SCOPE_OPENERS:
            scope_name, close_operator = _SCOPE_OPENERS[operator]
            node = {
                "type": "operation_scope",
                "scope": scope_name,
                "open_operator": operator,
                "open_ordinal": ordinal,
                "close_operator": close_operator,
                "state_before": _state_snapshot(state),
                "children": [],
            }
            stack[-1].setdefault("children", []).append(node)
            stack.append(node)
            _apply_state_operator(state, item)
            continue
        if operator in {pair[1] for pair in _SCOPE_OPENERS.values()}:
            if len(stack) > 1 and stack[-1].get("close_operator") == operator:
                scope = stack.pop()
                _apply_state_operator(state, item)
                scope["close_ordinal"] = ordinal
                scope["state_after"] = _state_snapshot(state)
                continue
            diagnostics.append({"kind": "unmatched_scope_close", "operator": operator, "ordinal": ordinal})
        stack[-1].setdefault("children", []).append({
            "type": "operation",
            "source_ordinal": ordinal,
            "operator": operator,
            "operands": item.get("operands") or [],
            "effective_state": _state_snapshot(state),
        })
        _apply_state_operator(state, item)

    for scope in stack[1:]:
        diagnostics.append({
            "kind": "unclosed_scope",
            "scope": scope["scope"],
            "open_operator": scope["open_operator"],
            "open_ordinal": scope["open_ordinal"],
        })
    return {
        "schema": "pdf-training-structured-operations-v1",
        "children": root["children"],
        "diagnostics": diagnostics,
    }


def flatten_structured_operations(tree: dict[str, Any], operations: list[dict[str, Any]]) -> list[int]:
    """Return raw ordinals in the exact order represented by a structured tree."""
    by_ordinal = {int(item["ordinal"]): item for item in operations}
    flattened: list[int] = []

    def visit(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if node.get("type") == "operation_scope":
                flattened.append(int(node["open_ordinal"]))
                visit(node.get("children") or [])
                if "close_ordinal" in node:
                    flattened.append(int(node["close_ordinal"]))
            elif node.get("type") == "operation":
                flattened.append(int(node["source_ordinal"]))

    visit(tree.get("children") or [])
    return [ordinal for ordinal in flattened if ordinal in by_ordinal]


def validate_operation_parity(tree: dict[str, Any], operations: list[dict[str, Any]]) -> list[str]:
    """Report whether structured operations preserve raw ordinal order exactly."""
    expected = [int(item["ordinal"]) for item in operations]
    actual = flatten_structured_operations(tree, operations)
    if actual == expected:
        return []
    errors: list[str] = []
    if len(actual) != len(expected):
        errors.append(f"operation count mismatch: structured={len(actual)} raw={len(expected)}")
    for index, (structured, raw) in enumerate(zip(actual, expected)):
        if structured != raw:
            errors.append(f"operation order mismatch at index {index}: structured={structured} raw={raw}")
            break
    if len(set(actual)) != len(actual):
        errors.append("structured operations contain duplicate ordinals")
    if set(actual) != set(expected):
        errors.append("structured operations do not contain the same ordinals as raw operations")
    return errors


def extract(pdf_path: Path, output: Path) -> dict[str, Any]:
    graph = ObjectGraph()
    pages = []
    digest = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    with pikepdf.Pdf.open(pdf_path) as pdf:
        for index, page in enumerate(pdf.pages):
            page_ref = graph.add(page.obj)
            resources = page.get("/Resources")
            resources_ref = graph.add(resources)
            contents = page.get("/Contents")
            contents_ref = graph.add(contents)
            instructions = pikepdf.parse_content_stream(page)
            pages.append({
                "id": f"page-{index + 1:03d}",
                "type": "page",
                "role": "page_container",
                "index": index,
                "page_ref": page_ref,
                "media_box": scalar(page.get("/MediaBox")),
                "crop_box": scalar(page.get("/CropBox")),
                "rotate": scalar(page.get("/Rotate", 0)),
                "user_unit": scalar(page.get("/UserUnit", 1)),
                "resources_ref": resources_ref,
                "contents_ref": contents_ref,
                "operations": [typed_operation(item, ordinal) for ordinal, item in enumerate(instructions)],
                "structured_operations": structured_operations([typed_operation(item, ordinal) for ordinal, item in enumerate(instructions)]),
            })
    result = {
        "schema": SCHEMA,
        "type": "document",
        "role": "document_container",
        "source": {"path": str(pdf_path.resolve()), "sha256": digest},
        "pages": pages,
        "objects": graph.objects,
        "summary": {
            "pages": len(pages),
            "objects": len(graph.objects),
            "operations": sum(len(page["operations"]) for page in pages),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = extract(args.pdf, args.out)
    print(json.dumps({"schema": result["schema"], **result["summary"], "out": str(args.out.resolve())}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
