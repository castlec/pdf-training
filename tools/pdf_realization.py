#!/usr/bin/env python3
"""Data model and validation for source-independent PDF realization."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from math import isfinite
from typing import Any

SCHEMA = "pdf-training-realization-page-v1"
MATRIX_KEYS = ("a", "b", "c", "d", "e", "f")
RESOURCE_KINDS = ("fonts", "xobjects", "extgstates", "color_spaces", "patterns", "shadings", "properties")
RESOURCE_OPERATORS = {"set_font": "fonts", "set_graphics_state": "extgstates", "draw_image": "xobjects", "draw_form": "xobjects", "set_color_space": "color_spaces", "set_pattern": "patterns"}

class RealizationError(ValueError):
    """Raised when a realization tree cannot be emitted deterministically."""


def matrix(value: Mapping[str, Any] | Iterable[Any] | None = None) -> dict[str, float]:
    """Normalize a PDF affine matrix to named, JSON-friendly values."""
    if value is None:
        return {key: 1.0 if key in {"a", "d"} else 0.0 for key in MATRIX_KEYS}
    raw = [value.get(key) for key in MATRIX_KEYS] if isinstance(value, Mapping) else list(value)
    if len(raw) != 6:
        raise RealizationError("matrix must contain exactly six values")
    result = {}
    for key, item in zip(MATRIX_KEYS, raw):
        try:
            number = float(item)
        except (TypeError, ValueError) as exc:
            raise RealizationError(f"matrix value {key} is not numeric") from exc
        if not isfinite(number):
            raise RealizationError(f"matrix value {key} is not finite")
        result[key] = number
    return result


def operation(kind: str, *, ordinal: int, **values: Any) -> dict[str, Any]:
    """Create one ordered, typed operation without losing operation metadata."""
    if not kind or ordinal < 0:
        raise RealizationError("operation requires a non-empty kind and non-negative ordinal")
    return {"ordinal": int(ordinal), "kind": kind, **values}


def resources(**groups: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Create a complete resource dictionary with explicit empty groups."""
    unknown = set(groups) - set(RESOURCE_KINDS)
    if unknown:
        raise RealizationError(f"unknown resource groups: {sorted(unknown)}")
    return {kind: dict(groups.get(kind) or {}) for kind in RESOURCE_KINDS}


def realization_page(*, page_id: str, width: float, height: float, operations: Iterable[Mapping[str, Any]], page_resources: Mapping[str, Mapping[str, Any]] | None = None, nodes: Iterable[Mapping[str, Any]] | None = None, user_unit: float = 1.0) -> dict[str, Any]:
    """Build and validate a standalone page realization."""
    page = {"schema": SCHEMA, "id": page_id, "media_box": {"x": 0.0, "y": 0.0, "w": float(width), "h": float(height)}, "user_unit": float(user_unit), "resources": resources(**(page_resources or {})), "operations": [dict(item) for item in operations], "nodes": [dict(item) for item in (nodes or [])]}
    validate_page(page)
    return page


def _resource_name(op: Mapping[str, Any]) -> str | None:
    for key in ("resource", "font", "xobject", "graphics_state", "color_space", "pattern"):
        value = op.get(key)
        if value is not None:
            return str(value).lstrip("/")
    return None


def validate_page(page: Mapping[str, Any]) -> None:
    """Validate invariants needed for deterministic PDF stream regeneration."""
    if page.get("schema") != SCHEMA:
        raise RealizationError(f"expected schema {SCHEMA}")
    media_box = page.get("media_box") or {}
    for key in ("w", "h"):
        try:
            value = float(media_box[key])
        except (KeyError, TypeError, ValueError) as exc:
            raise RealizationError(f"media_box.{key} must be numeric") from exc
        if not isfinite(value) or value <= 0:
            raise RealizationError(f"media_box.{key} must be positive and finite")
    if float(page.get("user_unit", 1)) <= 0:
        raise RealizationError("user_unit must be positive")
    declared = page.get("resources") or {}
    for kind in RESOURCE_KINDS:
        if not isinstance(declared.get(kind, {}), Mapping):
            raise RealizationError(f"resources.{kind} must be an object")
    operations = list(page.get("operations") or [])
    ordinals = [int(op.get("ordinal", -1)) for op in operations]
    if ordinals != sorted(ordinals) or len(ordinals) != len(set(ordinals)):
        raise RealizationError("operations must have unique ascending ordinals")
    scopes: list[str] = []
    for op in operations:
        kind = str(op.get("kind") or "")
        if not kind:
            raise RealizationError("each operation requires kind")
        if kind == "save":
            scopes.append("restore")
        elif kind == "restore":
            if not scopes or scopes.pop() != "restore":
                raise RealizationError("restore has no matching save")
        elif kind == "begin_text":
            scopes.append("end_text")
        elif kind == "end_text":
            if not scopes or scopes.pop() != "end_text":
                raise RealizationError("end_text has no matching begin_text")
        resource_kind = RESOURCE_OPERATORS.get(kind)
        if resource_kind:
            name = _resource_name(op)
            if not name:
                raise RealizationError(f"{kind} requires a resource name")
            if name not in declared[resource_kind]:
                raise RealizationError(f"{kind} references undeclared {resource_kind} resource {name}")
        if "transform" in op:
            matrix(op["transform"])
        if "bbox" in op and op["bbox"] is not None and any(key not in op["bbox"] for key in ("x", "y", "w", "h")):
            raise RealizationError("operation bbox requires x, y, w, h")
    if scopes:
        raise RealizationError(f"unclosed operation scope: {scopes[-1]}")
    for node in page.get("nodes") or []:
        if node.get("type") == "container":
            if node.get("local_transform") is not None:
                matrix(node["local_transform"])
            if node.get("children") is None:
                raise RealizationError("container requires children")


def node_realization(node: Mapping[str, Any], *, operations: Iterable[Mapping[str, Any]], local_transform: Mapping[str, Any] | Iterable[Any] | None = None) -> dict[str, Any]:
    """Attach a realization scope to an existing structural node."""
    out = dict(node)
    out["realization"] = {"operations": [dict(item) for item in operations], "local_transform": matrix(local_transform)}
    return out
