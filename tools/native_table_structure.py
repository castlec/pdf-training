"""Materialize native PDF table structure into recursive CFG containers."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

TRANSFORM_ID = "structure.materialize-native-tables.v1"

_TABLE_ROLES = {"/Table", "/TR", "/TH", "/TD"}


def _children(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [child for child in value.get("children") or [] if isinstance(child, dict)]


def _role(value: dict[str, Any]) -> str | None:
    role = value.get("role")
    if role is None:
        return None
    role = str(role)
    return role if role.startswith("/") else "/" + role


def _mcid(value: dict[str, Any]) -> int | None:
    if value.get("type") != "pdf_marked_content":
        return None
    try:
        return int(value["mcid"])
    except (KeyError, TypeError, ValueError):
        return None


def _find_mcid(value: Any) -> int | None:
    if isinstance(value, dict):
        direct = _mcid(value)
        if direct is not None:
            return direct
        for key in ("MCID", "/MCID"):
            if key in value:
                try:
                    return int(value[key])
                except (TypeError, ValueError):
                    return None
        for child in _children(value):
            found = _find_mcid(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_mcid(child)
            if found is not None:
                return found
    return None


def _marked_content_ranges(operations: list[dict[str, Any]]) -> dict[int, list[int]]:
    active: list[tuple[int | None, int]] = []
    ranges: dict[int, list[int]] = {}
    for operation in operations:
        ordinal = int(operation.get("ordinal", -1))
        operator = str(operation.get("operator"))
        if operator == "BDC":
            active.append((_find_mcid(operation.get("operands") or []), ordinal))
        elif operator == "BMC":
            active.append((None, ordinal))
        elif operator == "EMC" and active:
            mcid, start = active.pop()
            if mcid is not None:
                ranges.setdefault(mcid, []).extend(range(start, ordinal + 1))
    return {mcid: sorted(set(ordinals)) for mcid, ordinals in ranges.items()}


def _descendant_mcids(value: dict[str, Any], *, stop_at_table: bool = True) -> list[int]:
    role = _role(value)
    if stop_at_table and role in _TABLE_ROLES and role not in {"/TH", "/TD"}:
        return []
    direct = _mcid(value)
    result = [direct] if direct is not None else []
    for child in _children(value):
        result.extend(_descendant_mcids(child, stop_at_table=stop_at_table))
    return result


def _table_nodes(value: dict[str, Any], page_ref: str) -> Iterable[dict[str, Any]]:
    if _role(value) == "/Table" and value.get("page_ref") == page_ref:
        yield value
        return
    for child in _children(value):
        yield from _table_nodes(child, page_ref)


def _operation_ordinals(value: dict[str, Any], ranges: dict[int, list[int]]) -> list[int]:
    result: list[int] = []
    for mcid in _descendant_mcids(value):
        result.extend(ranges.get(mcid) or [])
    return sorted(set(result))


def _materialize(
    value: dict[str, Any],
    *,
    page_id: str,
    path: tuple[int, ...],
    ranges: dict[int, list[int]],
) -> dict[str, Any] | None:
    role = _role(value)
    if role not in _TABLE_ROLES:
        return None
    layout_kind = {"/Table": "table", "/TR": "row", "/TH": "cell", "/TD": "cell"}[role]
    node_id = f"{page_id}::native-table::{layout_kind}-{'-'.join(str(item) for item in path)}"
    children: list[dict[str, Any]] = []
    for index, child in enumerate(_children(value)):
        child_node = _materialize(child, page_id=page_id, path=path + (index,), ranges=ranges)
        if child_node is not None:
            children.append(child_node)
    node: dict[str, Any] = {
        "id": node_id,
        "type": "group",
        "role": layout_kind,
        "layout_kind": layout_kind,
        "children": children,
    }
    ordinals = _operation_ordinals(value, ranges)
    if ordinals:
        node["source_operation_ordinals"] = ordinals
    return node


def materialize_native_tables(document: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replace native PDF table definitions with recursive internal containers."""
    del options
    result = copy.deepcopy(document)
    native_structure = result.pop("native_structure", None)
    if not isinstance(native_structure, dict):
        result.setdefault("provenance", {})["native_tables"] = {
            "transform": TRANSFORM_ID,
            "tables_materialized": 0,
        }
        return result

    structure_root = native_structure.get("children") or []
    materialized = 0
    for page in result.get("pages") or []:
        page_ref = str(page.get("page_ref"))
        ranges = _marked_content_ranges(page.get("operations") or [])
        table_groups = []
        for index, table in enumerate(_table_nodes_from_root(structure_root, page_ref)):
            group = _materialize(
                table,
                page_id=str(page.get("id")),
                path=(index,),
                ranges=ranges,
            )
            if group is not None:
                table_groups.append(group)
                materialized += 1
        if table_groups:
            page["operation_groups"] = list(page.get("operation_groups") or []) + table_groups

    result.setdefault("provenance", {})["native_tables"] = {
        "transform": TRANSFORM_ID,
        "tables_materialized": materialized,
    }
    return result


def _table_nodes_from_root(children: list[Any], page_ref: str) -> Iterable[dict[str, Any]]:
    for child in children:
        if not isinstance(child, dict):
            continue
        yield from _table_nodes(child, page_ref)
