#!/usr/bin/env python3
"""Build and validate a text-only translation projection from PDF IR."""

from __future__ import annotations

import base64
import copy
import re
from collections.abc import Mapping
from typing import Any

PROJECT_TRANSFORM_ID = "translation.project.v1"
VALIDATE_TRANSFORM_ID = "translation.validate.v1"
PROJECTION_SCHEMA = "pdf-training-translation-projection-v1"


class TranslationProjectionError(ValueError):
    """Raised when the source cannot produce a complete translation projection."""


def _ref(value: Any) -> str | None:
    return value.get("ref") if isinstance(value, Mapping) and isinstance(value.get("ref"), str) else None


def _resolve(objects: Mapping[str, Any], value: Any) -> Any:
    reference = _ref(value)
    if reference is None and isinstance(value, str) and value in objects:
        reference = value
    return objects.get(reference, value) if reference else value


def _values(objects: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
    resolved = _resolve(objects, value)
    if not isinstance(resolved, Mapping):
        return {}
    candidate = resolved.get("values")
    if isinstance(candidate, Mapping):
        return candidate
    candidate = resolved.get("dictionary")
    return candidate if isinstance(candidate, Mapping) else resolved


def _name(value: Any) -> str | None:
    if isinstance(value, Mapping) and value.get("type") == "name":
        value = value.get("value")
    return value.removeprefix("/") if isinstance(value, str) else None


def _hex_text(value: str) -> str:
    if len(value) % 2:
        value = "0" + value
    raw = bytes.fromhex(value)
    if len(raw) >= 2 and len(raw) % 2 == 0:
        try:
            decoded = raw.decode("utf-16-be")
            if decoded:
                return decoded
        except UnicodeDecodeError:
            pass
    return chr(int(value, 16)) if value else ""


def _parse_cmap(data: bytes) -> tuple[dict[int, str], int]:
    text = data.decode("latin1", errors="replace")
    mapping: dict[int, str] = {}
    code_bytes = 1
    in_codespace = False
    for line in text.splitlines():
        line = line.split("%", 1)[0].strip()
        if line.endswith("begincodespacerange"):
            in_codespace = True
            continue
        if line == "endcodespacerange":
            in_codespace = False
            continue
        if in_codespace:
            match = re.search(r"<([0-9A-Fa-f]+)>\s+<([0-9A-Fa-f]+)>", line)
            if match:
                code_bytes = max(code_bytes, len(match.group(1)) // 2)
    active_block = False
    for line in text.splitlines():
        line = line.split("%", 1)[0].strip()
        if line.endswith("beginbfchar") or line.endswith("beginbfrange"):
            active_block = True
            continue
        if line == "endbfchar" or line == "endbfrange":
            active_block = False
            continue
        if not active_block or not line or not line.startswith("<"):
            continue
        tokens = re.findall(r"\[|\]|<[0-9A-Fa-f]+>", line)
        if len(tokens) < 2 or tokens[0] == "[":
            continue
        try:
            start = int(tokens[0][1:-1], 16)
            if len(tokens) == 2:
                mapping[start] = _hex_text(tokens[1][1:-1])
            elif len(tokens) >= 3 and tokens[2] != "[":
                end = int(tokens[1][1:-1], 16)
                first = int(tokens[2][1:-1], 16)
                for offset, source in enumerate(range(start, end + 1)):
                    mapping[source] = _hex_text(f"{first + offset:X}")
            elif len(tokens) >= 4 and tokens[2] == "[":
                for offset, token in enumerate(tokens[3:]):
                    if token == "]":
                        break
                    mapping[start + offset] = _hex_text(token[1:-1])
        except (TypeError, ValueError):
            continue
    return mapping, code_bytes


def _font_maps(document: Mapping[str, Any], page: Mapping[str, Any]) -> dict[str, tuple[dict[int, str], int, str | None]]:
    objects = document.get("objects") or {}
    page_values = _values(objects, page.get("page_ref"))
    resources = _values(objects, page_values.get("/Resources"))
    fonts = resources.get("/Font") or {}
    result: dict[str, tuple[dict[int, str], int, str | None]] = {}
    for resource_name, font_ref in fonts.items():
        font_name = str(resource_name).removeprefix("/")
        font = _values(objects, font_ref)
        to_unicode = _resolve(objects, font.get("/ToUnicode"))
        decoded = to_unicode.get("decoded_bytes_b64") if isinstance(to_unicode, Mapping) else None
        if isinstance(decoded, str):
            try:
                cmap, code_bytes = _parse_cmap(base64.b64decode(decoded))
            except (ValueError, TypeError):
                cmap, code_bytes = {}, 1
        else:
            cmap, code_bytes = {}, 1
        result[font_name] = (cmap, code_bytes, _name(font.get("/BaseFont")))
    return result


def _decode_string(value: Any, cmap: tuple[dict[int, str], int, str | None]) -> tuple[str, list[int]]:
    if not isinstance(value, Mapping) or value.get("type") != "string":
        return "", []
    raw_b64 = value.get("raw_bytes_b64")
    if isinstance(raw_b64, str):
        try:
            raw = base64.b64decode(raw_b64)
        except (ValueError, TypeError):
            raw = bytes(ord(character) & 0xFF for character in str(value.get("value", "")))
    else:
        raw = bytes(ord(character) & 0xFF for character in str(value.get("value", "")))
    mapping, code_bytes, _ = cmap
    if not mapping:
        return raw.decode("cp1252", errors="replace"), []
    text: list[str] = []
    unknown: list[int] = []
    for offset in range(0, len(raw), code_bytes):
        chunk = raw[offset:offset + code_bytes]
        if len(chunk) != code_bytes:
            unknown.append(offset)
            continue
        code = int.from_bytes(chunk, "big")
        decoded = mapping.get(code)
        if decoded is None:
            unknown.append(code)
            text.append("\ufffd")
        else:
            text.append(decoded)
    return "".join(text), unknown


def _text_from_operation(operation: Mapping[str, Any], cmap: tuple[dict[int, str], int, str | None]) -> tuple[str, list[int]]:
    operands = operation.get("operands") or []
    if not operands:
        return "", []
    payload = operands[0]
    values = payload if isinstance(payload, list) else [payload]
    decoded: list[str] = []
    unknown: list[int] = []
    for value in values:
        text, missing = _decode_string(value, cmap)
        decoded.append(text)
        unknown.extend(missing)
    return "".join(decoded), unknown


def _group_paths(page: Mapping[str, Any]) -> dict[int, list[dict[str, Any]]]:
    paths: dict[int, list[dict[str, Any]]] = {}

    def visit(node: Mapping[str, Any], path: list[Mapping[str, Any]]) -> set[int]:
        current = path + [node]
        ordinals = {int(value) for value in (node.get("operation_ordinals") or [])}
        ordinals.update(int(value) for value in (node.get("source_operation_ordinals") or []))
        child_results: list[tuple[Mapping[str, Any], set[int]]] = []
        for key in ("children", "operation_groups"):
            for child in node.get(key) or []:
                if isinstance(child, Mapping):
                    ordinals_from_child = visit(child, current)
                    child_results.append((child, ordinals_from_child))
                    ordinals.update(ordinals_from_child)
        descriptor = {key: node[key] for key in ("id", "type", "role", "layout_kind", "layout_box", "bbox") if key in node}
        for ordinal in ordinals:
            paths.setdefault(ordinal, []).insert(0, descriptor)
        return ordinals

    for root in page.get("operation_groups") or []:
        if isinstance(root, Mapping):
            visit(root, [])
    return paths


def _context(page: Mapping[str, Any], path: list[dict[str, Any]]) -> dict[str, Any]:
    geometry = [item["id"] for item in path if item.get("role") in {"geometric_construct", "geometry_container"} and item.get("id")]
    tables = [{key: item[key] for key in ("id", "role", "layout_kind") if key in item} for item in path if item.get("layout_kind") in {"table", "row", "cell"}]
    return {"page_id": page.get("id"), "group_path": copy.deepcopy(path), "geometry_group_ids": geometry, "table_path": tables}


def _projection_units(document: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    units: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for page_index, page in enumerate(document.get("pages") or []):
        if not isinstance(page, Mapping):
            continue
        media_box = page.get("media_box") or [0, 0, 0, 0]
        page_height = float(media_box[3] or page.get("height") or 0)
        maps = _font_maps(document, page)
        paths = _group_paths(page)
        current_font = ""
        current_size = 12.0
        text_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
        in_text = False
        for operation in page.get("operations") or []:
            operator = operation.get("operator")
            ordinal = int(operation.get("ordinal", 0))
            operands = operation.get("operands") or []
            if operator == "BT":
                in_text = True
                text_matrix = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
            elif operator == "ET":
                in_text = False
            elif operator == "Tf" and len(operands) >= 2:
                current_font = _name(operands[0]) or ""
                try:
                    current_size = float(operands[1])
                except (TypeError, ValueError):
                    current_size = 12.0
            elif operator == "Tm" and len(operands) >= 6:
                try:
                    text_matrix = [float(value) for value in operands[:6]]
                except (TypeError, ValueError):
                    pass
            if operator not in {"Tj", "TJ"} or not in_text:
                continue
            cmap = maps.get(current_font, ({}, 1, None))
            source_text, unknown = _text_from_operation(operation, cmap)
            if not source_text:
                if unknown:
                    issues.append({"page_id": page.get("id"), "operation_ordinal": ordinal, "kind": "undecodable_text"})
                continue
            x = float(text_matrix[4])
            y = page_height - float(text_matrix[5]) - current_size
            page_id = str(page.get("id") or f"page-{page_index + 1:03d}")
            units.append({
                "id": f"{page_id}-text-{ordinal:05d}",
                "source_order": len(units),
                "page_index": page_index,
                "source_operation_ordinal": ordinal,
                "source_text": source_text,
                "font_resource": current_font,
                "font_family": cmap[2],
                "font_size": current_size,
                "bbox": {"x": x, "y": y, "w": max(current_size * 0.5, len(source_text) * current_size * 0.5), "h": current_size},
                "context": _context(page, paths.get(ordinal, [])),
                "decode_status": "partial" if unknown else "complete",
                "decode_unknown_codes": unknown,
            })
    return units, issues


def project_translation(document: dict[str, Any], *, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    if document.get("schema") != "pdf-training-pdf-ir-v1":
        raise TranslationProjectionError("translation projection expects pdf-training-pdf-ir-v1")
    result = copy.deepcopy(document)
    options = options or {}
    units, issues = _projection_units(result)
    projection = {
        "schema": PROJECTION_SCHEMA,
        "transform": PROJECT_TRANSFORM_ID,
        "source_sha256": (result.get("source") or {}).get("sha256"),
        "target_language": str(options.get("target_language", "English")),
        "model_input_policy": {"include_images": False, "include_raw_geometry": False, "include_coordinates": False, "include_source_pdf": False, "include_context_ids": True},
        "translation_prompt": str(options.get("translation_prompt") or (
            "Translate every supplied source text node from Czech into precise formal English suitable for a mathematics examination. "
            "Return one object for every input ID, in input order, as valid JSON. Preserve every number, unit, variable, mathematical symbol, superscript relationship, sign, and quantity exactly. "
            "Do not solve, explain, omit, merge, split, reorder, or normalize. Translate Czech words and phrases even when they contain ASCII-only text."
        )),
        "terminology_prompt": str(options.get("terminology_prompt") or (
            "Using the original Czech text and its approved English translation, return a JSON table of important Czech mathematical terms and phrases. "
            "For each entry provide the Czech term, the English equivalent used, and a short mathematical usage note. Do not invent terms not present in the input."
        )),
        "target_font_policy": copy.deepcopy(options.get("target_font_policy") or {
            "family": "Arial", "fallback_family": "Liberation Sans", "preserve_nominal_size": True,
            "body_size_pt": 12.0, "minimum_automatic_scale": 0.90, "preferred_automatic_scale": 0.95,
            "reflow_before_scale": True,
        }),
        "units": units,
        "diagnostics": issues,
        "summary": {"pages": len(result.get("pages") or []), "units": len(units), "diagnostic_count": len(issues)},
    }
    result.setdefault("metadata", {})["translation_projection"] = projection
    result.setdefault("provenance", {})["translation_projection"] = {"transform": PROJECT_TRANSFORM_ID, "active_structure_unchanged": True, "model_input": "text-only-projection"}
    return result


def validate_translation_projection(document: dict[str, Any], *, options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    result = copy.deepcopy(document)
    projection = ((result.get("metadata") or {}).get("translation_projection"))
    if not isinstance(projection, Mapping) or projection.get("schema") != PROJECTION_SCHEMA:
        raise TranslationProjectionError("translation projection is missing or has an unknown schema")
    units = projection.get("units")
    if not isinstance(units, list):
        raise TranslationProjectionError("translation projection units must be a list")
    seen: set[str] = set()
    pages = {str(page.get("id")) for page in result.get("pages") or [] if isinstance(page, Mapping)}
    ordinals = {str(page.get("id")): {int(item.get("ordinal", -1)) for item in page.get("operations") or []} for page in result.get("pages") or [] if isinstance(page, Mapping)}
    for index, unit in enumerate(units):
        if not isinstance(unit, Mapping):
            raise TranslationProjectionError(f"translation unit {index} is not an object")
        unit_id = str(unit.get("id") or "")
        if not unit_id or unit_id in seen:
            raise TranslationProjectionError(f"translation unit {index} has a missing or duplicate id")
        seen.add(unit_id)
        if unit.get("source_order") != index:
            raise TranslationProjectionError(f"translation unit {unit_id} is out of source order")
        page_id = str((unit.get("context") or {}).get("page_id") or "")
        if page_id not in pages:
            raise TranslationProjectionError(f"translation unit {unit_id} references an unknown page")
        if int(unit.get("source_operation_ordinal", -1)) not in ordinals[page_id]:
            raise TranslationProjectionError(f"translation unit {unit_id} references a missing operation")
        if not isinstance(unit.get("source_text"), str) or not unit.get("source_text"):
            raise TranslationProjectionError(f"translation unit {unit_id} has no source text")
        if not isinstance((unit.get("context") or {}).get("group_path"), list):
            raise TranslationProjectionError(f"translation unit {unit_id} has no recursive group path")
    result.setdefault("metadata", {}).setdefault("translation_projection", {})["validation"] = {"transform": VALIDATE_TRANSFORM_ID, "status": "passed", "unit_count": len(units), "diagnostic_count": len(projection.get("diagnostics") or [])}
    return result
