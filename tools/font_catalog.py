#!/usr/bin/env python3
"""Catalogue PDF fonts without changing the faithful PDF operation stream."""

from __future__ import annotations

import copy
import re
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

TRANSFORM_ID = "font.catalog.v1"
_CSS_STYLE_NAMES = ("Regular", "Bold", "Italic", "Oblique", "Light", "Medium", "Semibold", "Black")


class FontCatalogError(ValueError):
    pass


def _ref(value: Any) -> str | None:
    if isinstance(value, dict) and isinstance(value.get("ref"), str):
        return value["ref"]
    return None


def _resolve(objects: Mapping[str, Any], value: Any) -> Any:
    reference = _ref(value)
    if reference is None and isinstance(value, str) and value in objects:
        reference = value
    if reference is None:
        return value
    return objects.get(reference, value)


def _values(objects: Mapping[str, Any], value: Any) -> Mapping[str, Any]:
    resolved = _resolve(objects, value)
    if not isinstance(resolved, Mapping):
        return {}
    candidate = resolved.get("values")
    if isinstance(candidate, Mapping):
        return candidate
    candidate = resolved.get("dictionary")
    if isinstance(candidate, Mapping):
        return candidate
    return resolved


def _name(value: Any) -> str | None:
    if isinstance(value, Mapping) and value.get("type") == "name":
        raw = value.get("value")
        return raw[1:] if isinstance(raw, str) and raw.startswith("/") else raw
    if isinstance(value, str):
        return value[1:] if value.startswith("/") else value
    return None


def _number(value: Any) -> int | float | None:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _array(objects: Mapping[str, Any], value: Any) -> list[Any]:
    resolved = _resolve(objects, value)
    if isinstance(resolved, list):
        return list(resolved)
    if isinstance(resolved, Mapping) and isinstance(resolved.get("values"), list):
        return list(resolved["values"])
    return []


def _font_object_refs(objects: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    fonts: list[tuple[str, Mapping[str, Any]]] = []
    for object_ref, object_value in objects.items():
        values = _values(objects, object_value)
        if _name(values.get("/Type")) == "Font":
            fonts.append((object_ref, values))
    return fonts


def _descendant_font(objects: Mapping[str, Any], values: Mapping[str, Any]) -> Mapping[str, Any]:
    descendants = _array(objects, values.get("/DescendantFonts"))
    return _values(objects, descendants[0]) if descendants else {}


def _descriptor_values(objects: Mapping[str, Any], values: Mapping[str, Any]) -> Mapping[str, Any]:
    descriptor = values.get("/FontDescriptor")
    if descriptor is None:
        descriptor = _descendant_font(objects, values).get("/FontDescriptor")
    return _values(objects, descriptor)


def _font_family(base_font: str | None) -> tuple[str | None, bool, str | None]:
    if not base_font:
        return None, False, None
    subset_match = re.match(r"^[A-Z]{6}\+(.+)$", base_font)
    unprefixed = subset_match.group(1) if subset_match else base_font
    style = None
    for style_name in _CSS_STYLE_NAMES:
        suffix = "-" + style_name
        if unprefixed.endswith(suffix):
            style = style_name
            unprefixed = unprefixed[: -len(suffix)]
            break
    return unprefixed, subset_match is not None, style


def _font_entry(objects: Mapping[str, Any], object_ref: str, values: Mapping[str, Any]) -> dict[str, Any]:
    base_font = _name(values.get("/BaseFont"))
    family, subset, style = _font_family(base_font)
    descendant = _descendant_font(objects, values)
    metrics = descendant or values
    widths = _array(objects, metrics.get("/Widths"))
    numeric_widths = [_number(width) for width in widths]
    numeric_widths = [width for width in numeric_widths if width is not None]
    descriptor = _descriptor_values(objects, values)
    embedded_keys = sorted(key[1:] for key in ("/FontFile", "/FontFile2", "/FontFile3") if key in descriptor)
    issues: list[str] = []
    if not base_font:
        issues.append("missing-base-font")
    if _name(values.get("/Subtype")) == "TrueType" and widths and any(width <= 0 for width in numeric_widths):
        issues.append("non-positive-width")
    if values.get("/ToUnicode") is None and _name(values.get("/Subtype")) in {"Type0", "Type1", "MMType1"}:
        issues.append("missing-to-unicode")
    return {
        "object_ref": object_ref,
        "base_font": base_font,
        "family": family,
        "style": style,
        "subset": subset,
        "subtype": _name(values.get("/Subtype")),
        "encoding": _name(values.get("/Encoding")),
        "embedded": bool(embedded_keys),
        "embedded_streams": embedded_keys,
        "descriptor_ref": _ref(values.get("/FontDescriptor")) or _ref(descendant.get("/FontDescriptor")),
        "to_unicode_ref": _ref(values.get("/ToUnicode")),
        "metrics": {
            "first_char": _number(metrics.get("/FirstChar")),
            "last_char": _number(metrics.get("/LastChar")),
            "width_count": len(widths),
            "non_positive_width_count": sum(width <= 0 for width in numeric_widths),
        },
        "issues": issues,
    }


def _page_font_resources(objects: Mapping[str, Any], page: Mapping[str, Any]) -> dict[str, str]:
    resources = _values(objects, page.get("resources_ref") or page.get("page_ref"))
    if "/Resources" in resources:
        resources = _values(objects, resources.get("/Resources"))
    fonts = resources.get("/Font")
    if not isinstance(fonts, Mapping):
        return {}
    return {
        name[1:] if isinstance(name, str) and name.startswith("/") else str(name): _ref(reference)
        for name, reference in fonts.items()
        if _ref(reference) is not None
    }


def _text_font_usage(pages: list[Any]) -> dict[str, dict[str, Any]]:
    usage: dict[str, dict[str, Any]] = defaultdict(lambda: {"pages": set(), "sizes": set(), "tf_operations": 0})
    for fallback_index, page in enumerate(pages):
        if not isinstance(page, Mapping):
            continue
        page_number = page.get("index", fallback_index)
        for operation in page.get("operations", []):
            if not isinstance(operation, Mapping) or operation.get("operator") != "Tf":
                continue
            operands = operation.get("operands", [])
            if not isinstance(operands, list) or not operands:
                continue
            resource_name = _name(operands[0])
            if not resource_name:
                continue
            item = usage[resource_name]
            item["pages"].add(page_number)
            if len(operands) > 1:
                size = _number(operands[1])
                if size is not None:
                    item["sizes"].add(size)
            item["tf_operations"] += 1
    return usage


def _normalization_mapping(options: Mapping[str, Any]) -> dict[str, str]:
    raw_mapping = options.get("resource_mapping", {})
    if not isinstance(raw_mapping, Mapping):
        raise FontCatalogError("font.catalog.v1 resource_mapping must be an object")
    return {str(key).lstrip("/"): str(value).lstrip("/") for key, value in raw_mapping.items()}


def _rewrite_font_resources(document: dict[str, Any], mapping: Mapping[str, str]) -> int:
    objects = document.get("objects", {})
    rewritten = 0
    for page in document.get("pages", []):
        if not isinstance(page, dict):
            continue
        available = _page_font_resources(objects, page)
        for source, target in mapping.items():
            if source in available and target not in available:
                raise FontCatalogError(f"font mapping target /{target} is not a resource on page {page.get('index')}")
        for operation in page.get("operations", []):
            if not isinstance(operation, dict) or operation.get("operator") != "Tf":
                continue
            operands = operation.get("operands", [])
            if not isinstance(operands, list) or not operands:
                continue
            source = _name(operands[0])
            target = mapping.get(source or "")
            if target:
                operands[0] = {"type": "name", "value": "/" + target}
                rewritten += 1
    return rewritten


def catalogue_fonts(document: dict[str, Any], options: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Add font metadata; optionally rewrite Tf references through explicit mapping."""
    if not isinstance(document, dict):
        raise FontCatalogError("font catalogue expects a document object")
    result = copy.deepcopy(document)
    options = options or {}
    objects = result.get("objects", {})
    if not isinstance(objects, Mapping):
        raise FontCatalogError("font catalogue expects an objects mapping")
    usage = _text_font_usage(result.get("pages", []))
    resource_names: dict[str, set[str]] = defaultdict(set)
    for page in result.get("pages", []):
        if not isinstance(page, Mapping):
            continue
        for resource_name, object_ref in _page_font_resources(objects, page).items():
            resource_names[object_ref].add(resource_name)
    entries: list[dict[str, Any]] = []
    for object_ref, values in _font_object_refs(objects):
        entry = _font_entry(objects, object_ref, values)
        names = sorted(resource_names.get(object_ref, set()))
        entry["resource_names"] = names
        entry["usage"] = {
            "pages": sorted({page for name in names for page in usage.get(name, {}).get("pages", set())}),
            "sizes": sorted({size for name in names for size in usage.get(name, {}).get("sizes", set())}),
            "tf_operations": sum(usage.get(name, {}).get("tf_operations", 0) for name in names),
        }
        entries.append(entry)
    entries.sort(key=lambda item: (item["resource_names"] or [""], item["object_ref"]))
    mapping = _normalization_mapping(options)
    rewritten = _rewrite_font_resources(result, mapping) if mapping else 0
    metadata = result.setdefault("metadata", {})
    if not isinstance(metadata, dict):
        raise FontCatalogError("document metadata must be an object")
    metadata["font_catalog"] = {
        "schema": "pdf-training-font-catalog-v1",
        "preserves_source_operations": not bool(mapping),
        "fonts": entries,
        "resource_mapping": {"/" + source: "/" + target for source, target in sorted(mapping.items())},
        "rewritten_tf_operations": rewritten,
    }
    return result
