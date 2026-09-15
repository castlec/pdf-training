#!/usr/bin/env python3
"""Render pdf-training-pdf-ir-v1 without opening the source PDF."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
from typing import Any

import pikepdf

from tools.extract_pdf_ir import flatten_structured_operations, validate_operation_parity

SCHEMA = "pdf-training-pdf-ir-v1"


def name(value: str) -> pikepdf.Name:
    return pikepdf.Name(value)


class Resolver:
    def __init__(self, pdf: pikepdf.Pdf, objects: dict[str, dict[str, Any]]) -> None:
        self.pdf = pdf
        self.objects = objects
        self.cache: dict[str, Any] = {}

    def value(self, value: Any) -> Any:
        if isinstance(value, dict) and set(value) == {"ref"}:
            return self.object(value["ref"])
        if isinstance(value, dict) and value.get("type") == "name":
            return name(str(value["value"]))
        if isinstance(value, dict) and value.get("type") == "string":
            return pikepdf.String(str(value["value"]))
        if isinstance(value, list):
            return pikepdf.Array([self.value(item) for item in value])
        if isinstance(value, dict):
            return pikepdf.Dictionary({key if str(key).startswith("/") else "/" + str(key): self.value(item) for key, item in value.items()})
        return value

    def object(self, ref: str) -> Any:
        if ref in self.cache:
            return self.cache[ref]
        record = self.objects.get(ref)
        if record is None:
            raise ValueError(f"missing PDF object {ref}")
        kind = record.get("kind")
        if kind == "stream":
            dictionary = {}
            for key, value in (record.get("dictionary") or {}).items():
                if key not in {"/Length", "/Filter", "/DecodeParms"}:
                    dictionary[key] = self.value(value)
            data = base64.b64decode(record.get("decoded_bytes_b64", ""))
            stream = pikepdf.Stream(self.pdf, data, pikepdf.Dictionary({key if str(key).startswith("/") else "/" + str(key): value for key, value in dictionary.items()}))
            self.cache[ref] = self.pdf.make_indirect(stream)
            return self.cache[ref]
        if kind == "dictionary":
            dictionary = pikepdf.Dictionary()
            self.cache[ref] = self.pdf.make_indirect(dictionary)
            for key, value in (record.get("values") or {}).items():
                dictionary[key if str(key).startswith("/") else "/" + str(key)] = self.value(value)
            return self.cache[ref]
        if kind == "array":
            array = pikepdf.Array([self.value(item) for item in (record.get("values") or [])])
            self.cache[ref] = self.pdf.make_indirect(array)
            return self.cache[ref]
        if kind == "value":
            value = self.value(record.get("value"))
            self.cache[ref] = self.pdf.make_indirect(value)
            return self.cache[ref]
        raise ValueError(f"unsupported PDF object kind {kind!r}")


def page_field(resolver: Resolver, page: dict[str, Any], key: str) -> Any:
    page_record = resolver.objects.get(str(page.get("page_ref")), {})
    value = (page_record.get("values") or {}).get(key)
    return resolver.value(value) if value is not None else None


def serialize_operations(pdf: pikepdf.Pdf, resolver: Resolver, operations: list[dict[str, Any]]) -> pikepdf.Stream:
    """Serialize the transformed operation list without consulting the source PDF."""
    instructions = []
    for item in operations:
        operands = [resolver.value(value) for value in item.get("operands") or []]
        instructions.append(pikepdf.ContentStreamInstruction(operands, pikepdf.Operator(str(item["operator"]))))
    return pikepdf.Stream(pdf, pikepdf.unparse_content_stream(instructions))


def serialize_structured_operations(
    pdf: pikepdf.Pdf,
    resolver: Resolver,
    tree: dict[str, Any],
    operations: list[dict[str, Any]],
) -> pikepdf.Stream:
    """Serialize a structured operation tree only after exact raw parity validation."""
    errors = validate_operation_parity(tree, operations)
    if errors:
        raise ValueError("structured operation parity failure: " + "; ".join(errors))
    by_ordinal = {int(item["ordinal"]): item for item in operations}
    ordered = [by_ordinal[ordinal] for ordinal in flatten_structured_operations(tree, operations)]
    return serialize_operations(pdf, resolver, ordered)


def _group_span(group: dict[str, Any]) -> tuple[int, int] | None:
    ordinals = [int(value) for value in group.get("operation_ordinals") or []]
    ordinals.extend(int(value) for value in group.get("source_operation_ordinals") or [])
    spans = [_group_span(child) for child in group.get("children") or [] if isinstance(child, dict)]
    spans.extend(_group_span(child) for child in group.get("operation_groups") or [] if isinstance(child, dict))
    spans = [span for span in spans if span is not None]
    if not ordinals and not spans:
        return None
    values = []
    if ordinals:
        values.extend((min(ordinals), max(ordinals)))
    if spans:
        values.extend((min(span[0] for span in spans), max(span[1] for span in spans)))
    return min(values), max(values)


def _group_transform(page: pikepdf.Page, group: dict[str, Any]) -> list[float] | None:
    transform = group.get("render_transform")
    if transform is not None:
        return [float(value) for value in transform]
    coordinate_space = group.get("coordinate_space") or {}
    if not isinstance(coordinate_space, dict) or coordinate_space.get("origin_convention") != "top-left":
        return None
    parent = group.get("parent_context_bbox_pdf") or group.get("parent_context_bbox")
    if not parent:
        raise ValueError(f"localized operation group {group.get('id')} has no enclosing render context")
    offset = group.get("relative_offset_pdf") or {}
    height = float(page.MediaBox[3]) - float(page.MediaBox[1])
    return [1, 0, 0, -1, float(parent.get("x", 0)) + float(offset.get("x", 0)), height - float(parent.get("y", 0)) - float(offset.get("y", 0))]


def _debug_instructions(box: dict[str, Any], paint: dict[str, Any], *, height: float, local: bool, before: bool) -> list[pikepdf.ContentStreamInstruction]:
    if not paint or not all(key in box for key in ("x", "y", "w", "h")): 
        return []
    if before and not paint.get("background", True):
        return []
    stroke_gray = float(paint.get("stroke_gray", 0.15))
    stroke_width = float(paint.get("stroke_width", 0.7))
    dash = paint.get("dash") or [3, 2]
    x, y = (0, 0) if local else (float(box["x"]), height - float(box["y"]) - float(box["h"]))
    rectangle = [x, y, float(box["w"]), float(box["h"])]
    result = []
    if before:
        result.extend([
            pikepdf.ContentStreamInstruction([float(paint.get("fill_gray", 0.96))], pikepdf.Operator("g")),
            pikepdf.ContentStreamInstruction(rectangle, pikepdf.Operator("re")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("f")),
        ])
    else:
        result.extend([
            pikepdf.ContentStreamInstruction([stroke_gray], pikepdf.Operator("G")),
            pikepdf.ContentStreamInstruction([stroke_width], pikepdf.Operator("w")),
            pikepdf.ContentStreamInstruction([pikepdf.Array(dash), 0], pikepdf.Operator("d")),
            pikepdf.ContentStreamInstruction(rectangle, pikepdf.Operator("re")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("S")),
        ])
    return result


def render_recursive_operation_groups(pdf: pikepdf.Pdf, page: pikepdf.Page, resolver: Resolver, operations: list[dict[str, Any]], groups: list[dict[str, Any]]) -> tuple[pikepdf.Stream, int]:
    """Render operations through explicit enter/exit ownership contexts."""
    height = float(page.MediaBox[3]) - float(page.MediaBox[1])
    by_ordinal = {int(item.get("ordinal", index)): item for index, item in enumerate(operations)}
    owner_by_ordinal: dict[int, list[dict[str, Any]]] = {}
    replaced_ordinals: set[int] = set()
    owned_groups: set[int] = set()

    def children(group: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            child
            for key in ("children", "operation_groups")
            for child in group.get(key) or []
            if isinstance(child, dict)
        ]

    def assign(group: dict[str, Any], path: list[dict[str, Any]]) -> None:
        next_path = path + [group]
        direct = set()
        if group.get("role") != "associated_text":
            direct.update(int(value) for value in group.get("operation_ordinals") or [])
            direct.update(int(value) for value in group.get("source_operation_ordinals") or [])
        if direct:
            owned_groups.add(id(group))
        if group.get("render_operations") is not None:
            replaced_ordinals.update(direct)
        for ordinal in direct:
            previous = owner_by_ordinal.get(ordinal)
            if previous is not None:
                raise ValueError(f"operation {ordinal} has multiple active render owners")
            owner_by_ordinal[ordinal] = next_path
        for child in children(group):
            assign(child, next_path)

    for root in groups:
        assign(root, [])

    def enter(group: dict[str, Any]) -> list[pikepdf.ContentStreamInstruction]:
        transform = _group_transform(page, group)
        coordinate_space = group.get("coordinate_space") or {}
        local = isinstance(coordinate_space, dict) and coordinate_space.get("origin_convention") == "top-left"
        paint = ((group.get("paint") or {}).get("debug") or {})
        result = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))]
        if transform is not None:
            result.append(pikepdf.ContentStreamInstruction(transform, pikepdf.Operator("cm")))
        result.extend(_debug_instructions(group.get("bbox") or {}, paint, height=height, local=local, before=True))
        for operation in group.get("render_operations") or []:
            result.append(pikepdf.ContentStreamInstruction([resolver.value(value) for value in operation.get("operands") or []], pikepdf.Operator(str(operation["operator"]))))
        return result

    def exit_context(group: dict[str, Any]) -> list[pikepdf.ContentStreamInstruction]:
        transform = _group_transform(page, group)
        coordinate_space = group.get("coordinate_space") or {}
        local = isinstance(coordinate_space, dict) and coordinate_space.get("origin_convention") == "top-left"
        paint = ((group.get("paint") or {}).get("debug") or {})
        result = _debug_instructions(group.get("bbox") or {}, paint, height=height, local=local, before=False)
        if transform is not None:
            result.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")))
        else:
            result.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")))
        return result

    instructions: list[pikepdf.ContentStreamInstruction] = []
    active: list[dict[str, Any]] = []
    rendered = 0
    for ordinal in sorted(by_ordinal):
        desired = owner_by_ordinal.get(ordinal, [])
        common = 0
        while common < len(active) and common < len(desired) and active[common] is desired[common]:
            common += 1
        for group in reversed(active[common:]):
            instructions.extend(exit_context(group))
        active = active[:common]
        for group in desired[common:]:
            instructions.extend(enter(group))
        if desired[common:] :
            rendered += sum(1 for group in desired[common:] if id(group) in owned_groups)
        active = desired
        if ordinal not in replaced_ordinals:
            operation = by_ordinal[ordinal]
            instructions.append(pikepdf.ContentStreamInstruction([resolver.value(value) for value in operation.get("operands") or []], pikepdf.Operator(str(operation["operator"]))))
    for group in reversed(active):
        instructions.extend(exit_context(group))
    return pikepdf.Stream(pdf, pikepdf.unparse_content_stream(instructions)), len(owned_groups)


def render_recursive_operation_groups_legacy(pdf: pikepdf.Pdf, page: pikepdf.Page, resolver: Resolver, operations: list[dict[str, Any]], groups: list[dict[str, Any]]) -> tuple[pikepdf.Stream, int]:
    """Legacy span renderer retained for output comparison."""
    height = float(page.MediaBox[3]) - float(page.MediaBox[1])
    by_ordinal = {int(item.get("ordinal", index)): item for index, item in enumerate(operations)}
    def direct_owned(group: dict[str, Any]) -> set[int]:
        if group.get("role") == "associated_text":
            return set()
        ordinals = {int(value) for value in group.get("operation_ordinals") or []}
        ordinals.update(int(value) for value in group.get("source_operation_ordinals") or [])
        return ordinals

    def subtree_owned(group: dict[str, Any]) -> set[int]:
        result = direct_owned(group)
        for child in (group.get("children") or []) + (group.get("operation_groups") or []):
            if isinstance(child, dict):
                result.update(subtree_owned(child))
        return result

    roots = [(group, _group_span(group)) for group in groups]
    roots = [(group, span) for group, span in roots if span is not None]
    rendered = 0

    def children_with_spans(group: dict[str, Any]) -> list[tuple[dict[str, Any], tuple[int, int]]]:
        children = (group.get("children") or []) + (group.get("operation_groups") or [])
        return sorted([(child, span) for child in children if isinstance(child, dict) for span in [_group_span(child)] if span is not None], key=lambda item: (item[1][0], -item[1][1]))

    def context_frame(group: dict[str, Any], before: bool) -> list[pikepdf.ContentStreamInstruction]:
        paint = ((group.get("paint") or {}).get("debug") or {})
        transform = _group_transform(page, group)
        coordinate_space = group.get("coordinate_space") or {}
        local = isinstance(coordinate_space, dict) and coordinate_space.get("origin_convention") == "top-left"
        result = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))]
        if transform is not None:
            result.append(pikepdf.ContentStreamInstruction(transform, pikepdf.Operator("cm")))
        result.extend(_debug_instructions(group.get("bbox") or {}, paint, height=height, local=local, before=before))
        result.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")))
        return result

    def emit_backgrounds(group: dict[str, Any], span: tuple[int, int]) -> list[pikepdf.ContentStreamInstruction]:
        transform = _group_transform(page, group)
        result = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))]
        if transform is not None:
            result.append(pikepdf.ContentStreamInstruction(transform, pikepdf.Operator("cm")))
        coordinate_space = group.get("coordinate_space") or {}
        local = isinstance(coordinate_space, dict) and coordinate_space.get("origin_convention") == "top-left"
        paint = ((group.get("paint") or {}).get("debug") or {})
        result.extend(_debug_instructions(group.get("bbox") or {}, paint, height=height, local=local, before=True))
        for child, child_span in children_with_spans(group):
            result.extend(emit_backgrounds(child, child_span))
        result.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")))
        return result

    def emit_content(group: dict[str, Any], span: tuple[int, int]) -> list[pikepdf.ContentStreamInstruction]:
        transform = _group_transform(page, group)
        result = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))]
        if transform is not None:
            result.append(pikepdf.ContentStreamInstruction(transform, pikepdf.Operator("cm")))
        if group.get("render_operations") is not None:
            for operation in group.get("render_operations") or []:
                result.append(pikepdf.ContentStreamInstruction([resolver.value(value) for value in operation.get("operands") or []], pikepdf.Operator(str(operation["operator"]))))
            visible_ordinals: set[int] = set()
        else:
            visible_ordinals = subtree_owned(group)
        child_entries = children_with_spans(group)
        child_by_start = {child_span[0]: (child, child_span) for child, child_span in child_entries}
        child_owned = set().union(*(subtree_owned(child) for child, _ in child_entries)) if child_entries else set()
        ordinal = span[0]
        while ordinal <= span[1]:
            child_entry = child_by_start.get(ordinal)
            if child_entry is not None:
                child, child_span = child_entry
                result.extend(emit_content(child, child_span))
                ordinal += 1
                continue
            if ordinal in child_owned or ordinal not in visible_ordinals:
                ordinal += 1
                continue
            operation = by_ordinal.get(ordinal)
            if operation is not None:
                result.append(pikepdf.ContentStreamInstruction([resolver.value(value) for value in operation.get("operands") or []], pikepdf.Operator(str(operation["operator"]))))
            ordinal += 1
        result.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")))
        return result

    def emit_borders(group: dict[str, Any], span: tuple[int, int]) -> list[pikepdf.ContentStreamInstruction]:
        transform = _group_transform(page, group)
        result = [pikepdf.ContentStreamInstruction([], pikepdf.Operator("q"))]
        if transform is not None:
            result.append(pikepdf.ContentStreamInstruction(transform, pikepdf.Operator("cm")))
        for child, child_span in children_with_spans(group):
            result.extend(emit_borders(child, child_span))
        coordinate_space = group.get("coordinate_space") or {}
        local = isinstance(coordinate_space, dict) and coordinate_space.get("origin_convention") == "top-left"
        paint = ((group.get("paint") or {}).get("debug") or {})
        result.extend(_debug_instructions(group.get("bbox") or {}, paint, height=height, local=local, before=False))
        result.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")))
        return result

    instructions = []
    root_by_start = {span[0]: (group, span) for group, span in roots}
    rendered_owned: set[int] = set()
    ordinal = 0
    while ordinal < len(operations):
        root_entry = root_by_start.get(ordinal)
        if root_entry is not None:
            group, span = root_entry
            instructions.extend(emit_backgrounds(group, span))
            instructions.extend(emit_content(group, span))
            instructions.extend(emit_borders(group, span))
            rendered_owned.update(subtree_owned(group))
            rendered += 1
            ordinal += 1
            continue
        if ordinal not in rendered_owned:
            operation = by_ordinal.get(ordinal)
            if operation is not None:
                instructions.append(pikepdf.ContentStreamInstruction([resolver.value(value) for value in operation.get("operands") or []], pikepdf.Operator(str(operation["operator"]))))
        ordinal += 1
    return pikepdf.Stream(pdf, pikepdf.unparse_content_stream(instructions)), rendered


def render_debug_overlays(pdf: pikepdf.Pdf, page: pikepdf.Page, overlays: list[dict[str, Any]]) -> None:
    """Render persisted nesting overlays behind the canonical page content."""
    if not overlays:
        return
    height = float(page.MediaBox[3]) - float(page.MediaBox[1])
    overlays = sorted(
        overlays,
        key=lambda overlay: (
            int(overlay.get("depth", 0)),
            -float((overlay.get("bbox") or {}).get("w", 0)) * float((overlay.get("bbox") or {}).get("h", 0)),
        ),
    )
    instructions = []
    for overlay in overlays:
        box = overlay.get("bbox") or {}
        if not all(key in box for key in ("x", "y", "w", "h")):
            continue
        depth = int(overlay.get("depth", 0))
        paint = (overlay.get("paint") or {}).get("debug") or {}
        gray = float(paint.get("fill_gray", max(0.75, 0.96 - depth * 0.06)))
        stroke_gray = float(paint.get("stroke_gray", 0.15))
        stroke_width = float(paint.get("stroke_width", 0.7))
        dash = paint.get("dash") or [3, 2]
        y = height - float(box["y"]) - float(box["h"])
        instructions.extend([
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("q")),
            pikepdf.ContentStreamInstruction([gray], pikepdf.Operator("g")),
            pikepdf.ContentStreamInstruction([float(box["x"]), y, float(box["w"]), float(box["h"])], pikepdf.Operator("re")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("f")),
            pikepdf.ContentStreamInstruction([stroke_gray], pikepdf.Operator("G")),
            pikepdf.ContentStreamInstruction([stroke_width], pikepdf.Operator("w")),
            pikepdf.ContentStreamInstruction([pikepdf.Array(dash), 0], pikepdf.Operator("d")),
            pikepdf.ContentStreamInstruction([float(box["x"]), y, float(box["w"]), float(box["h"])], pikepdf.Operator("re")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("S")),
            pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")),
        ])
    if not instructions:
        return
    overlay_stream = pikepdf.Stream(pdf, pikepdf.unparse_content_stream(instructions))
    existing = page.Contents
    page.Contents = pikepdf.Array([overlay_stream, existing]) if existing is not None else overlay_stream


def apply_operation_contexts(pdf: pikepdf.Pdf, page: pikepdf.Page, operation_groups: list[dict[str, Any]]) -> int:
    """Replay declared operation groups under their context matrices."""
    flattened = []

    def collect(group: dict[str, Any]) -> None:
        if group.get("operation_ordinals"):
            flattened.append(group)
        children = (group.get("children") or []) + (group.get("operation_groups") or [])
        for child in children:
            if isinstance(child, dict) and (child.get("operation_ordinals") or child.get("operation_groups")):
                collect(child)

    for group in operation_groups:
        collect(group)
    operation_groups = flattened
    contexts = []
    for group in operation_groups:
        transform = group.get("render_transform")
        if transform is None and group.get("coordinate_space", {}).get("origin_convention") == "top-left":
            if not group.get("parent_context_bbox_pdf"):
                raise ValueError(f"localized operation group {group.get('id')} has no enclosing render context")
            parent = group.get("parent_context_bbox_pdf") or group.get("parent_context_bbox") or {}
            offset = group.get("relative_offset_pdf") or {}
            page_box = page.MediaBox
            page_height = float(page_box[3]) - float(page_box[1])
            transform = [
                1,
                0,
                0,
                -1,
                float(parent.get("x", 0)) + float(offset.get("x", 0)),
                page_height - float(parent.get("y", 0)) - float(offset.get("y", 0)),
            ]
        ordinals = group.get("operation_ordinals") or []
        if not transform or len(transform) != 6 or not ordinals:
            continue
        paint = group.get("paint", {}).get("debug")
        if paint:
            paint = dict(paint)
            paint["size"] = group.get("local_size") or {}
        contexts.append((min(int(value) for value in ordinals), max(int(value) for value in ordinals), [float(value) for value in transform], paint))
    if not contexts:
        return 0
    instructions = pikepdf.parse_content_stream(page)
    wrapped = []
    backgrounds = []
    starts = {start: (transforms, paint) for start, _, transforms, paint in contexts}
    ends = {end: True for _, end, _, _ in contexts}
    for ordinal, instruction in enumerate(instructions):
        if ordinal in starts:
            transform, paint = starts[ordinal]
            wrapped.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("q")))
            wrapped.append(pikepdf.ContentStreamInstruction(transform, pikepdf.Operator("cm")))
            if paint:
                size = paint.get("size") or {}
                stroke_gray = float(paint.get("stroke_gray", 0.15))
                width = float(paint.get("stroke_width", 0.7))
                dash = paint.get("dash") or [3, 2]
                if paint.get("background", True):
                    fill_gray = float(paint.get("fill_gray", 0.96))
                    backgrounds.extend([
                        pikepdf.ContentStreamInstruction([], pikepdf.Operator("q")),
                        pikepdf.ContentStreamInstruction(transform, pikepdf.Operator("cm")),
                        pikepdf.ContentStreamInstruction([fill_gray], pikepdf.Operator("g")),
                        pikepdf.ContentStreamInstruction([0, 0, float(size.get("w", 0)), float(size.get("h", 0))], pikepdf.Operator("re")),
                        pikepdf.ContentStreamInstruction([], pikepdf.Operator("f")),
                        pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")),
                    ])
                wrapped.extend([
                    pikepdf.ContentStreamInstruction([stroke_gray], pikepdf.Operator("G")),
                    pikepdf.ContentStreamInstruction([width], pikepdf.Operator("w")),
                    pikepdf.ContentStreamInstruction([pikepdf.Array(dash), 0], pikepdf.Operator("d")),
                    pikepdf.ContentStreamInstruction([0, 0, float(size.get("w", 0)), float(size.get("h", 0))], pikepdf.Operator("re")),
                    pikepdf.ContentStreamInstruction([], pikepdf.Operator("S")),
                ])
        wrapped.append(instruction)
        if ordinal in ends:
            wrapped.append(pikepdf.ContentStreamInstruction([], pikepdf.Operator("Q")))
    page.Contents = pikepdf.Stream(pdf, pikepdf.unparse_content_stream(backgrounds + wrapped))
    return len(contexts)


def operation_tree_groups(tree: dict[str, Any]) -> list[dict[str, Any]]:
    """Project operation-tree group nodes into recursive render contexts."""
    def group_from_node(node: dict[str, Any]) -> dict[str, Any]:
        group = dict(node.get("group") or {})
        direct_ordinals = [
            int(child["source_ordinal"])
            for child in node.get("children") or []
            if isinstance(child, dict) and child.get("type") == "operation"
        ]
        nested = [
            group_from_node(child)
            for child in node.get("children") or []
            if isinstance(child, dict) and child.get("type") in {"operation_group", "operation_group_reference"}
        ]
        existing_references = [int(ordinal) for ordinal in group.get("source_operation_ordinals") or []]
        group["operation_ordinals"] = direct_ordinals
        group["source_operation_ordinals"] = existing_references
        group["operation_references"] = [
            {"source_operation_ordinals": [int(ordinal) for ordinal in reference.get("source_operation_ordinals") or []]}
            for reference in node.get("references") or []
        ]
        group["children"] = nested
        group.pop("operation_groups", None)
        return group

    return [
        group_from_node(node)
        for node in tree.get("children") or []
        if isinstance(node, dict) and node.get("type") == "operation_group"
    ]


def render_recursive_operation_tree(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    resolver: Resolver,
    tree: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[pikepdf.Stream, int]:
    """Render the persisted operation tree through the recursive group renderer."""
    groups = operation_tree_groups(tree)
    return render_recursive_operation_groups(pdf, page, resolver, operations, groups)


def render(input_path: Path, output_path: Path) -> dict[str, Any]:
    raw = json.loads(input_path.read_text(encoding="utf-8"))
    if raw.get("schema") != SCHEMA:
        raise ValueError(f"expected input schema {SCHEMA}")
    pdf = pikepdf.Pdf.new()
    resolver = Resolver(pdf, raw.get("objects") or {})
    for raw_page in raw.get("pages") or []:
        media = raw_page["media_box"]
        width = float(media[2]) - float(media[0])
        height = float(media[3]) - float(media[1])
        page = pdf.add_blank_page(page_size=(width, height))
        page.MediaBox = resolver.value(media)
        crop = raw_page.get("crop_box")
        if crop is not None:
            page.CropBox = resolver.value(crop)
        page.Rotate = resolver.value(raw_page.get("rotate", 0))
        page.UserUnit = resolver.value(raw_page.get("user_unit", 1))
        resources = page_field(resolver, raw_page, "/Resources")
        contents = page_field(resolver, raw_page, "/Contents")
        if resources is not None:
            page.Resources = resources
        if contents is not None:
            page.Contents = contents
        def has_localized_group(value: dict[str, Any]) -> bool:
            if value.get("operations_localized"):
                return True
            return any(has_localized_group(child) for child in value.get("children") or [] if isinstance(child, dict))

        operation_groups = raw_page.get("operation_groups") or []
        operation_tree = raw_page.get("operation_tree")
        if operation_tree and operation_groups:
            page.Contents, rendered_groups = render_recursive_operation_tree(
                pdf,
                page,
                resolver,
                operation_tree,
                raw_page.get("operations") or [],
            )
            applied_contexts = rendered_groups
        elif operation_groups:
            page.Contents, rendered_groups = render_recursive_operation_groups(
                pdf,
                page,
                resolver,
                raw_page.get("operations") or [],
                operation_groups,
            )
            applied_contexts = rendered_groups
        elif raw_page.get("operations_localized"):
            page.Contents = serialize_operations(pdf, resolver, raw_page.get("operations") or [])
            applied_contexts = 0
        elif raw_page.get("structured_operations"):
            page.Contents = serialize_structured_operations(
                pdf,
                resolver,
                raw_page["structured_operations"],
                raw_page.get("operations") or [],
            )
            applied_contexts = 0
        else:
            applied_contexts = 0
        debug_overlays = []
        seen_overlay_ids: set[str] = set()

        def collect_debug_paint(value: Any, depth: int) -> None:
            if not isinstance(value, dict):
                return
            value_id = value.get("id")
            if value.get("paint") and value.get("role") != "geometric_construct":
                if value_id is None or str(value_id) not in seen_overlay_ids:
                    debug_overlays.append({"bbox": value.get("bbox"), "depth": depth, "paint": value.get("paint")})
                    if value_id is not None:
                        seen_overlay_ids.add(str(value_id))
            for child in value.get("children") or []:
                collect_debug_paint(child, depth + 1)

        for group in raw_page.get("group_tree") or []:
            collect_debug_paint(group, 0)
        render_debug_overlays(pdf, page, debug_overlays)
        if applied_contexts:
            raw_page.setdefault("render_report", {})["operation_contexts"] = applied_contexts
    output_path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(output_path)
    return {"schema": "pdf-training-pdf-ir-render-report-v1", "input": str(input_path), "output": str(output_path), "pages": len(raw.get("pages") or [])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(render(args.input, args.out), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
