#!/usr/bin/env python3
"""Registry and first implementation for renderable document transforms."""

from __future__ import annotations

import copy
import inspect
from collections.abc import Callable, Mapping
from typing import Any

Transform = Callable[[dict[str, Any]], dict[str, Any]]
IDENTITY_ID = "identity.v1"
PROJECT_PDF_IR_ID = "project.pdf-ir.v1"


class TransformError(ValueError):
    pass


def _replace_nodes(value: Any) -> Any:
    """Recursively replace every structural node object with a deep copy."""
    if isinstance(value, list):
        return [_replace_nodes(item) for item in value]
    if not isinstance(value, dict):
        return value
    out = {key: _replace_nodes(item) for key, item in value.items()}
    if isinstance(value.get("nodes"), list):
        out["nodes"] = [copy.deepcopy(node) for node in value["nodes"]]
        for node in out["nodes"]:
            if isinstance(node, dict) and isinstance(node.get("children"), list):
                node["children"] = _replace_nodes(node["children"])
    return out


def identity(document: dict[str, Any]) -> dict[str, Any]:
    """Return an equivalent document made from newly allocated node objects."""
    if not isinstance(document, dict):
        raise TransformError("identity expects a document object")
    result = _replace_nodes(copy.deepcopy(document))
    if result == document and result is not document:
        return result
    raise TransformError("identity transform failed equivalence check")


def project(document: dict[str, Any]) -> dict[str, Any]:
    """Load the projection implementation lazily to avoid an import cycle."""
    from tools.project_pdf_ir import project as project_impl

    return project_impl(document)

from tools.relative_coordinates import DEBUG_VISUALIZATION_ID, TRANSFORM_ID as RELATIVE_COORDINATES_ID, apply_debug_visualization, apply_relative_coordinates
from tools.rule_grouping import apply_horizontal_rule_grouping
from tools.operation_grouping import CONTENT_GROUP_TRANSFORM_ID, INJECT_TEXT_LINES_TRANSFORM_ID, ONE_CELL_TABLE_TRANSFORM_ID, OPERATION_TREE_TRANSFORM_ID, ORIGIN_PROOF_TRANSFORM_ID, TEXT_ASSOCIATION_TRANSFORM_ID, TRANSFORM_ID as OPERATION_GROUPING_ID, apply_operation_grouping, associate_text_operations, expand_associated_content, inject_text_lines_into_first_cell, materialize_operation_tree, move_groups_to_parent_origin, promote_content_groups_to_one_cell_tables
from tools.localize_operations import TRANSFORM_ID as LOCALIZE_OPERATIONS_ID, apply_local_operation_coordinates

TRANSFORMS: dict[str, Transform] = {
    IDENTITY_ID: identity,
    PROJECT_PDF_IR_ID: project,
    "group.horizontal-rules.v1": apply_horizontal_rule_grouping,
    OPERATION_GROUPING_ID: apply_operation_grouping,
    TEXT_ASSOCIATION_TRANSFORM_ID: associate_text_operations,
    CONTENT_GROUP_TRANSFORM_ID: expand_associated_content,
    ONE_CELL_TABLE_TRANSFORM_ID: promote_content_groups_to_one_cell_tables,
    INJECT_TEXT_LINES_TRANSFORM_ID: inject_text_lines_into_first_cell,
    OPERATION_TREE_TRANSFORM_ID: materialize_operation_tree,
    ORIGIN_PROOF_TRANSFORM_ID: move_groups_to_parent_origin,
    LOCALIZE_OPERATIONS_ID: apply_local_operation_coordinates,
    RELATIVE_COORDINATES_ID: apply_relative_coordinates,
    DEBUG_VISUALIZATION_ID: apply_debug_visualization,
}


def resolve(transform_id: str) -> Transform:
    try:
        return TRANSFORMS[transform_id]
    except KeyError as exc:
        raise TransformError(f"unknown transform: {transform_id}") from exc


def apply_transform(document: dict[str, Any], transform_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Apply a registered transform with persisted manifest options."""
    transform = resolve(transform_id)
    options = options or {}
    parameters = inspect.signature(transform).parameters
    if "options" in parameters or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()):
        result = transform(document, options=options)
    else:
        result = transform(document)
    from tools.transform_contract import validate_transform_result

    validate_transform_result(result)
    return result
