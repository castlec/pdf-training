"""Contracts that every PDF IR transform must satisfy."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


class TransformContractError(ValueError):
    """Raised when a transform returns an invalid active document structure."""


def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        child
        for key in ("children", "operation_groups")
        for child in node.get(key) or []
        if isinstance(child, dict)
    ]


def _validate_cfg(node: dict[str, Any], path: str) -> None:
    if node.get("type") not in {"group", "draw_group", "content", "text_realization", "operation"}:
        raise TransformContractError(f"{path}: unsupported node type {node.get('type')!r}")
    layout_kind = node.get("layout_kind")
    children = _children(node)
    if layout_kind == "table" and not children:
        raise TransformContractError(f"{path}: table must contain one or more container children")
    for index, child in enumerate(children):
        _validate_cfg(child, f"{path}/{index}")


def validate_transform_result(document: dict[str, Any]) -> None:
    """Validate CFG shape, exact active ownership, and render closure."""
    metadata = document.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise TransformContractError("document metadata must be an object")
    font_catalog = metadata.get("font_catalog") if isinstance(metadata, dict) else None
    if font_catalog is not None:
        if not isinstance(font_catalog, dict):
            raise TransformContractError("metadata.font_catalog must be an object")
        if font_catalog.get("schema") != "pdf-training-font-catalog-v1":
            raise TransformContractError("metadata.font_catalog has an unknown schema")
        if not isinstance(font_catalog.get("fonts"), list):
            raise TransformContractError("metadata.font_catalog.fonts must be an array")
    if document.get("schema") != "pdf-training-pdf-ir-v1":
        pages = document.get("pages") or []
        if not pages or all(not (page.get("operations") or page.get("realization")) for page in pages):
            return
        raise TransformContractError("document schema is not pdf-training-pdf-ir-v1")
    pages = document.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    for page_index, page in enumerate(pages):
        operations = page.get("operations") or []
        operation_ordinals = [int(operation.get("ordinal", -1)) for operation in operations]
        if operation_ordinals != sorted(operation_ordinals) or len(operation_ordinals) != len(set(operation_ordinals)):
            raise TransformContractError(f"page {page_index}: operations are not uniquely ordered")
        operation_set = set(operation_ordinals)
        owners: dict[int, list[str]] = defaultdict(list)

        def visit(node: dict[str, Any], path: str) -> None:
            _validate_cfg(node, path)
            if node.get("owned_operation_ordinals"):
                raise TransformContractError(
                    f"{path}: owned_operation_ordinals is metadata-only ownership; operations must be structural children"
                )
            ordinals = [int(value) for value in node.get("operation_ordinals") or []]
            if node.get("role") != "associated_text":
                ordinals.extend(int(value) for value in node.get("source_operation_ordinals") or [])
            for ordinal in ordinals:
                if ordinal not in operation_set:
                    raise TransformContractError(f"{path}: operation ordinal {ordinal} is not present on the page")
                owners[ordinal].append(path)
            for index, child in enumerate(_children(node)):
                visit(child, f"{path}/{index}")

        for index, root in enumerate(page.get("operation_groups") or []):
            if isinstance(root, dict):
                visit(root, f"page[{page_index}]/operation_groups[{index}]")
        duplicate_owners = {ordinal: paths for ordinal, paths in owners.items() if len(paths) != 1}
        if duplicate_owners:
            raise TransformContractError(f"page {page_index}: operations have multiple active owners: {duplicate_owners}")

        tree = page.get("operation_tree")
        if tree is not None and not isinstance(tree, dict):
            raise TransformContractError(f"page {page_index}: operation_tree must be an object")
