"""Group marked geometric PDF operation spans without rasterizing them."""

from __future__ import annotations

import copy
from typing import Any


TRANSFORM_ID = "geometry.operations-into-groups.v1"
OPERATION_TREE_TRANSFORM_ID = "layout.materialize-operation-tree.v1"
ONE_CELL_TABLE_TRANSFORM_ID = "layout.promote-content-groups-to-one-cell-tables.v1"
INTRINSIC_LAYOUT_TRANSFORM_ID = "layout.intrinsic-container-growth.v1"
_GEOMETRY_OPS = {"m", "l", "c", "re", "h", "S", "s", "f", "f*", "B", "b"}


def _numbers(operation: dict[str, Any]) -> list[float]:
    values = []
    for operand in operation.get("operands") or []:
        if isinstance(operand, (int, float)):
            values.append(float(operand))
    return values


def _matrix_multiply(first: list[float], second: list[float]) -> list[float]:
    """Compose PDF affine matrices using column-vector coordinates."""
    a, b, c, d, e, f = first
    g, h, i, j, k, l = second
    return [
        a * g + c * h,
        b * g + d * h,
        a * i + c * j,
        b * i + d * j,
        a * k + c * l + e,
        b * k + d * l + f,
    ]


def _transform_point(matrix: list[float], x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = matrix
    return (a * x + c * y + e, b * x + d * y + f)


def _operation_bbox(operations: list[dict[str, Any]]) -> dict[str, float] | None:
    points: list[tuple[float, float]] = []
    ctm = [1.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    ctm_stack: list[list[float]] = []

    def add_point(x: float, y: float) -> None:
        points.append(_transform_point(ctm, x, y))

    for operation in operations:
        operator = operation.get("operator")
        values = _numbers(operation)
        if operator == "q":
            ctm_stack.append(ctm.copy())
        elif operator == "Q":
            if ctm_stack:
                ctm = ctm_stack.pop()
        elif operator == "cm" and len(values) >= 6:
            ctm = _matrix_multiply(ctm, values[:6])
        elif operator == "re" and len(values) >= 4:
            x, y, width, height = values[:4]
            for point_x, point_y in (
                (x, y),
                (x + width, y),
                (x, y + height),
                (x + width, y + height),
            ):
                add_point(point_x, point_y)
        elif operator in {"m", "l"} and len(values) >= 2:
            add_point(values[0], values[1])
        elif operator == "c" and len(values) >= 6:
            for point_x, point_y in (
                (values[0], values[1]),
                (values[2], values[3]),
                (values[4], values[5]),
            ):
                add_point(point_x, point_y)
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return {
        "x": min(xs),
        "y": min(ys),
        "w": max(xs) - min(xs),
        "h": max(ys) - min(ys),
    }


def _marked_geometry_blocks(operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks = []
    start = None
    mcid = None
    for index, operation in enumerate(operations):
        if operation.get("operator") == "BDC":
            operands = operation.get("operands") or []
            properties = operands[1] if len(operands) > 1 else {}
            if isinstance(properties, dict) and "/MCID" in properties:
                start = index
                mcid = properties["/MCID"]
        elif operation.get("operator") == "EMC" and start is not None:
            block_operations = operations[start : index + 1]
            geometry = [item for item in block_operations if item.get("operator") in _GEOMETRY_OPS]
            bbox = _operation_bbox(block_operations)
            has_path_geometry = any(item.get("operator") in {"m", "l", "c", "h"} for item in geometry)
            if bbox and has_path_geometry:
                blocks.append({"start": start, "end": index, "mcid": mcid, "bbox": bbox})
            start = None
            mcid = None
    return blocks


def _union(first: dict[str, float], second: dict[str, float]) -> dict[str, float]:
    x1 = min(first["x"], second["x"])
    y1 = min(first["y"], second["y"])
    x2 = max(first["x"] + first["w"], second["x"] + second["w"])
    y2 = max(first["y"] + first["h"], second["y"] + second["h"])
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def _cluster_blocks(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters = []
    for block in blocks:
        if clusters and block["start"] - clusters[-1]["end"] <= 8:
            cluster = clusters[-1]
            cluster["end"] = block["end"]
            cluster["mcids"].append(block["mcid"])
            cluster["bbox"] = _union(cluster["bbox"], block["bbox"])
        else:
            clusters.append({"start": block["start"], "end": block["end"], "mcids": [block["mcid"]], "bbox": block["bbox"]})
    return [cluster for cluster in clusters if len(cluster["mcids"]) >= 2]


def _page_dimensions(page: dict[str, Any]) -> tuple[float, float, float, float]:
    media = page.get("realization", {}).get("media_box") or {}
    media_box = page.get("media_box") or []
    if isinstance(media, dict):
        raw_width = float(media.get("w") or 0)
        raw_height = float(media.get("h") or 0)
    else:
        raw_width = 0.0
        raw_height = 0.0
    if len(media_box) >= 4:
        raw_width = raw_width or float(media_box[2]) - float(media_box[0])
        raw_height = raw_height or float(media_box[3]) - float(media_box[1])
    raw_width = raw_width or float(page.get("width") or 1)
    raw_height = raw_height or float(page.get("height") or 1)
    width = float(page.get("width") or raw_width)
    height = float(page.get("height") or raw_height)
    return raw_width, raw_height, width, height


def _page_bbox(page: dict[str, Any]) -> dict[str, float]:
    _, _, width, height = _page_dimensions(page)
    return {"x": 0.0, "y": 0.0, "w": width, "h": height}


def _to_page_bbox(raw: dict[str, float], page: dict[str, Any]) -> dict[str, float]:
    raw_width, raw_height, width, height = _page_dimensions(page)
    return {"x": raw["x"] * width / raw_width, "y": (raw_height - raw["y"] - raw["h"]) * height / raw_height, "w": raw["w"] * width / raw_width, "h": raw["h"] * height / raw_height}


def _contains(outer: dict[str, Any], inner: dict[str, float]) -> bool:
    box = outer.get("bbox") or {}
    return box.get("x", 0) <= inner["x"] and box.get("y", 0) <= inner["y"] and box.get("x", 0) + box.get("w", 0) >= inner["x"] + inner["w"] and box.get("y", 0) + box.get("h", 0) >= inner["y"] + inner["h"]


def _raw_frame_candidates(page: dict[str, Any]) -> list[dict[str, Any]]:
    operations = page.get("operations") or (page.get("realization") or {}).get("operations") or []
    frames = []
    horizontal = []
    vertical = []
    for operation in operations:
        if operation.get("operator") != "re":
            continue
        values = operation.get("operands") or []
        if len(values) < 4:
            continue
        item = {"ordinal": int(operation.get("ordinal", 0)), "x": float(values[0]), "y": float(values[1]), "w": float(values[2]), "h": float(values[3])}
        if item["w"] > 300 and item["h"] <= 2:
            horizontal.append(item)
        elif item["w"] <= 2 and item["h"] > 10:
            vertical.append(item)
    for top in horizontal:
        for bottom in horizontal:
            if top["ordinal"] >= bottom["ordinal"] or abs(top["x"] - bottom["x"]) > 2 or abs(top["w"] - bottom["w"]) > 2:
                continue
            if top["y"] <= bottom["y"] or top["y"] - bottom["y"] < 10:
                continue
            matching_vertical = [item for item in vertical if abs(item["x"] - bottom["x"]) <= 2 or abs(item["x"] + item["w"] - (bottom["x"] + bottom["w"])) <= 2]
            if not any(abs(item["y"] - bottom["y"]) <= 2 and item["y"] + item["h"] <= top["y"] + top["h"] for item in matching_vertical):
                continue
            frames.append({"source_bbox": {"x": bottom["x"], "y": bottom["y"], "w": bottom["w"], "h": top["y"] + top["h"] - bottom["y"]}, "border_operations": [bottom["ordinal"], top["ordinal"]]})
    return frames


def apply_operation_grouping(input_data: dict[str, Any]) -> dict[str, Any]:
    """Create real containers and attach geometric operation spans beneath them."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    if not pages:
        pages = [page for document in out.get("documents") or [] for page in document.get("pages") or []]
    total = 0
    for page in pages:
        realization = page.get("realization") or {}
        operations = realization.get("operations") or page.get("operations") or []
        clusters = _cluster_blocks(_marked_geometry_blocks(operations))
        page["operation_groups"] = list(page.get("operation_groups") or [])
        page.setdefault("bbox", _page_bbox(page))
        containers = []

        def frame_paint_operations(source_bbox: dict[str, float], seed: list[int]) -> list[int]:
            """Capture every source rectangle/fill that paints this frame."""
            x0 = float(source_bbox["x"])
            y0 = float(source_bbox["y"])
            x1 = x0 + float(source_bbox["w"])
            y1 = y0 + float(source_bbox["h"])
            ordinals = {int(value) for value in seed}
            for index, operation in enumerate(operations):
                if operation.get("operator") != "re":
                    continue
                values = _numbers(operation)
                if len(values) < 4:
                    continue
                x, y, w, h = values[:4]
                horizontal = (
                    h <= 2
                    and w >= float(source_bbox["w"]) * 0.8
                    and (abs(y - y0) <= 2 or abs(y + h - y1) <= 2)
                )
                vertical = (
                    w <= 2
                    and h > 2
                    and (abs(x - x0) <= 2 or abs(x + w - x1) <= 2)
                    and y >= y0 - 2
                    and y + h <= y1 + 2
                )
                corner = (
                    w <= 2
                    and h <= 2
                    and (abs(x - x0) <= 2 or abs(x + w - x1) <= 2)
                    and (abs(y - y0) <= 2 or abs(y + h - y1) <= 2)
                )
                if not (horizontal or vertical or corner):
                    continue
                ordinals.add(int(operation.get("ordinal", 0)))
                if index + 1 < len(operations) and operations[index + 1].get("operator") == "f*":
                    ordinals.add(int(operations[index + 1].get("ordinal", 0)))
            return sorted(ordinals)

        for frame_index, frame in enumerate(_raw_frame_candidates(page), start=1):
            frame_box = _to_page_bbox(frame["source_bbox"], page)
            sections = [section for section in page.get("group_tree") or [] if _contains(section, frame_box) or (
                section["bbox"].get("x", 0) <= frame_box.get("x", 0)
                and section["bbox"].get("x", 0) + section["bbox"].get("w", 0) >= frame_box.get("x", 0) + frame_box.get("w", 0)
                and frame_box.get("y", 0) >= section["bbox"].get("y", 0) - 2
                and frame_box.get("y", 0) + frame_box.get("h", 0) <= section["bbox"].get("y", 0) + section["bbox"].get("h", 0) + 2
            )]
            if not sections:
                continue
            section = min(sections, key=lambda candidate: candidate["bbox"]["w"] * candidate["bbox"]["h"])
            container = {"id": f"{page.get('id') or 'page'}-geometry-container-{frame_index:02d}", "type": "group", "role": "geometry_container", "bbox": frame_box, "source_bbox": frame["source_bbox"], "border_operations": frame_paint_operations(frame["source_bbox"], frame["border_operations"]), "children": []}
            section.setdefault("children", []).append(container)
            containers.append(container)
        for index, cluster in enumerate(clusters, start=1):
            page_box = _to_page_bbox(cluster["bbox"], page)
            group = {"id": f"{page.get('id') or 'page'}-geometry-{index:02d}", "type": "draw_group", "role": "geometric_construct", "bbox": page_box, "source_bbox": cluster["bbox"], "operation_ordinals": [int(operations[item]["ordinal"]) for item in range(cluster["start"], cluster["end"] + 1)], "mcids": cluster["mcids"], "source_space": "pdf-user-space"}
            page["operation_groups"].append(group)
            parents = [candidate for candidate in containers if _contains(candidate, page_box)]
            if not parents:
                parents = [candidate for candidate in page.get("group_tree") or [] if _contains(candidate, page_box)]
            if not parents:
                # The page container is the structural root for page-level marks
                # that are not enclosed by a semantic section.
                parents = [page]
            if parents:
                parent = min(parents, key=lambda candidate: candidate["bbox"]["w"] * candidate["bbox"]["h"])
                raw_width, raw_height, page_width, page_height = _page_dimensions(page)
                parent_box = parent.get("bbox") or {}
                group["parent_context_bbox"] = copy.deepcopy(parent_box)
                group["parent_context_bbox_pdf"] = {
                    "x": float(parent_box.get("x", 0)) * raw_width / page_width,
                    "y": float(parent_box.get("y", 0)) * raw_height / page_height,
                    "w": float(parent_box.get("w", 0)) * raw_width / page_width,
                    "h": float(parent_box.get("h", 0)) * raw_height / page_height,
                }
                group["relative_offset_pdf"] = {
                    "x": float(page_box.get("x", 0)) * raw_width / page_width - group["parent_context_bbox_pdf"]["x"],
                    "y": float(page_box.get("y", 0)) * raw_height / page_height - group["parent_context_bbox_pdf"]["y"],
                }
                if parent is not page:
                    parent.setdefault("operation_groups", []).append(copy.deepcopy(group))
                    parent.setdefault("children", []).append(copy.deepcopy(group))
                if parent in containers:
                    parent["container_role"] = "geometric_parent"
            else:
                raise ValueError(f"geometric group {group['id']} has no enclosing semantic group")
            total += 1
    out.setdefault("provenance", {})["operation_grouping"] = {"transform": TRANSFORM_ID, "pages": len(pages), "groups": total}
    return out


TEXT_ASSOCIATION_TRANSFORM_ID = "layout.associate-text-operations.v1"


def _text_operation_candidates(page: dict[str, Any]) -> list[dict[str, Any]]:
    operations = page.get("operations") or (page.get("realization") or {}).get("operations") or []
    media_box = page.get("media_box") or []
    page_height = float(page.get("height") or (media_box[3] - media_box[1] if len(media_box) >= 4 else 0))
    candidates = []
    text_start = None
    marked_start = None
    text_matrix = None
    font_size = 12.0
    for operation in operations:
        operator = operation.get("operator")
        ordinal = int(operation.get("ordinal", 0))
        if operator == "BDC":
            marked_start = ordinal
        elif operator == "BT":
            text_start = ordinal
            text_matrix = None
        elif operator == "Tf":
            values = operation.get("operands") or []
            if len(values) >= 2:
                font_size = float(values[1])
        elif operator == "Tm":
            values = operation.get("operands") or []
            if len(values) >= 6:
                text_matrix = values
        elif operator in {"Tj", "TJ"} and text_start is not None and text_matrix is not None:
            values = operation.get("operands") or []
            payload = values[0] if operator == "Tj" and values else values[0] if values else []
            strings = payload if isinstance(payload, list) else [payload]
            length = sum(len(item.get("value", "")) for item in strings if isinstance(item, dict))
            width = max(font_size * 0.5, length * font_size * 0.5)
            candidates.append({"operation_ordinal": ordinal, "text_span": [marked_start if marked_start is not None else text_start, ordinal], "bbox": {"x": float(text_matrix[4]), "y": page_height - float(text_matrix[5]) - font_size, "w": width, "h": font_size}})
        elif operator == "ET":
            text_start = None
            text_matrix = None
        elif operator == "EMC":
            marked_start = None
    return candidates


def associate_text_operations(input_data: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Associate text with geometry and transfer its complete source span atomically."""
    out = copy.deepcopy(input_data)
    options = options or {}
    span = options.get("collision_span") or {}
    left, right = float(span.get("left", 0)), float(span.get("right", 0))
    top, bottom = float(span.get("top", 0)), float(span.get("bottom", 0))
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    associated = 0
    transferred_groups = 0
    transferred_ordinals: set[int] = set()

    def complete_text_span(operations: list[dict[str, Any]], candidate: dict[str, Any]) -> list[int]:
        """Return the local q/clip/text/Q realization for a text candidate."""
        anchors = [int(value) for value in candidate.get("text_span") or []]
        if not anchors:
            return []
        end_anchor = max(anchors)
        end_index = next(
            (index for index, operation in enumerate(operations)
             if int(operation.get("ordinal", 0)) == end_anchor),
            None,
        )
        if end_index is None:
            return sorted(set(anchors))
        text_start_index = next(
            (index for index in range(end_index, -1, -1)
             if operations[index].get("operator") == "BT"),
            None,
        )
        if text_start_index is None:
            return sorted(set(anchors))

        start_index = text_start_index
        for index in range(text_start_index - 1, -1, -1):
            operator = operations[index].get("operator")
            if operator == "q":
                start_index = index
                break
            if operator in {"Q", "BT", "ET", "BDC", "EMC"}:
                break

        end_index = next(
            (index for index in range(end_index, len(operations))
             if operations[index].get("operator") == "ET"),
            end_index,
        )
        if end_index + 1 < len(operations) and operations[end_index + 1].get("operator") == "Q":
            end_index += 1
        return [
            int(operation.get("ordinal", 0))
            for operation in operations[start_index:end_index + 1]
        ]

    def area(box: dict[str, Any]) -> float:
        return max(0.0, float(box.get("w", 0))) * max(0.0, float(box.get("h", 0)))

    def remove_ownership(value: Any, ordinals: set[int]) -> None:
        if not isinstance(value, dict):
            return
        for key in ("operation_ordinals", "source_operation_ordinals"):
            if key in value:
                value[key] = [int(item) for item in value.get(key) or [] if int(item) not in ordinals]
        for key in ("children", "operation_groups"):
            for child in value.get(key) or []:
                remove_ownership(child, ordinals)

    for page in pages:
        operations = page.get("operations") or (page.get("realization") or {}).get("operations") or []
        candidates = _text_operation_candidates(page)
        groups: list[dict[str, Any]] = []

        def collect(value: Any) -> None:
            if not isinstance(value, dict):
                return
            if value.get("role") == "geometric_construct":
                groups.append(value)
            for child in value.get("children") or []:
                collect(child)
            for child in value.get("operation_groups") or []:
                collect(child)

        for group in page.get("operation_groups") or []:
            collect(group)

        assignments: dict[str, list[dict[str, Any]]] = {}
        for candidate in candidates:
            text_box = candidate["bbox"]
            matching = []
            for group in groups:
                box = group.get("bbox") or {}
                envelope = {
                    "x": box.get("x", 0) - left,
                    "y": box.get("y", 0) - top,
                    "w": box.get("w", 0) + left + right,
                    "h": box.get("h", 0) + top + bottom,
                }
                if _contains({"bbox": envelope}, text_box) or _contains({"bbox": text_box}, box):
                    matching.append(group)
            if not matching:
                continue
            owner = min(matching, key=lambda group: (area(group.get("bbox") or {}), str(group.get("id"))))
            assignments.setdefault(str(owner.get("id")), []).append(candidate)

        by_group_id = {str(group.get("id")): group for group in groups}
        for group_id, assigned in assignments.items():
            group = by_group_id[group_id]
            spans: dict[tuple[int, ...], list[dict[str, Any]]] = {}
            for candidate in assigned:
                source_span = tuple(complete_text_span(operations, candidate))
                if source_span:
                    spans.setdefault(source_span, []).append(candidate)
            for source_span, span_candidates in spans.items():
                source_ordinals = set(source_span)
                remove_ownership(group, source_ordinals)
                text_box = _union_boxes([candidate["bbox"] for candidate in span_candidates])
                text_group_id = f"{group_id}-text-ops-{source_span[0]}"
                existing_ids = {
                    str(child.get("id"))
                    for child in group.get("children") or []
                    if isinstance(child, dict)
                }
                if text_group_id not in existing_ids:
                    text_group = {
                        "id": text_group_id,
                        "type": "group",
                        "role": "text_operations",
                        "bbox": text_box,
                        "operation_ordinals": list(source_span),
                        "coordinate_space": "page",
                        "render_mode": "source_operations",
                        "children": [],
                    }
                    for candidate in span_candidates:
                        text_group["children"].append({
                            "id": f"{group_id}-text-{candidate['operation_ordinal']}",
                            "type": "text_realization",
                            "role": "associated_text",
                            "bbox": candidate["bbox"],
                            "source_operation_ordinals": candidate["text_span"],
                            "source_operation_span": list(source_span),
                            "coordinate_space": "page",
                            "render_mode": "source_operation_reference",
                        })
                    group.setdefault("children", []).append(text_group)
                    transferred_groups += 1
                transferred_ordinals.update(source_ordinals)
                associated += len(span_candidates)
        page.setdefault("operation_groups", [])

    out.setdefault("provenance", {})["text_operation_association"] = {
        "transform": TEXT_ASSOCIATION_TRANSFORM_ID,
        "associated": associated,
        "transferred_groups": transferred_groups,
        "transferred_operation_ordinals": sorted(transferred_ordinals),
        "collision_span": span,
        "ownership_transfer": "complete_source_span",
    }
    return out


CONTENT_GROUP_TRANSFORM_ID = "layout.expand-associated-content.v1"


def _union_boxes(boxes: list[dict[str, Any]]) -> dict[str, float]:
    x1 = min(float(box["x"]) for box in boxes)
    y1 = min(float(box["y"]) for box in boxes)
    x2 = max(float(box["x"]) + float(box["w"]) for box in boxes)
    y2 = max(float(box["y"]) + float(box["h"]) for box in boxes)
    return {"x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1}


def operation_group_ownership_diagnostics(groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Report duplicate exact operation ownership among sibling groups."""
    diagnostics: list[dict[str, Any]] = []

    def owned(group: dict[str, Any]) -> set[int]:
        if group.get("role") == "associated_text":
            result: set[int] = set()
        else:
            result = {int(value) for value in group.get("operation_ordinals") or []}
            result.update(int(value) for value in group.get("source_operation_ordinals") or [])
        for child in (group.get("children") or []) + (group.get("operation_groups") or []):
            if isinstance(child, dict):
                result.update(owned(child))
        return result

    def visit(parent_id: str | None, siblings: list[dict[str, Any]]) -> None:
        entries = [(group, owned(group)) for group in siblings]
        for index, (left, left_owned) in enumerate(entries):
            for right, right_owned in entries[index + 1:]:
                overlap = sorted(left_owned & right_owned)
                if not overlap:
                    continue
                diagnostics.append({
                    "kind": "overlapping_sibling_operation_ownership",
                    "parent_id": parent_id,
                    "left_id": left.get("id"),
                    "right_id": right.get("id"),
                    "operation_ordinals": overlap,
                })
        for group in siblings:
            children = [child for child in (group.get("children") or []) + (group.get("operation_groups") or []) if isinstance(child, dict)]
            visit(str(group.get("id")) if group.get("id") is not None else None, children)

    visit(None, groups)
    return diagnostics


def promote_content_groups_to_one_cell_tables(input_data: dict[str, Any]) -> dict[str, Any]:
    """Replace each content group with a recursive one-cell table group."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    promoted = 0
    replaced_ids: list[str] = []
    frame_by_geometry_id: dict[str, dict[str, Any]] = {}

    def collect_frames(value: Any, frame: dict[str, Any] | None = None) -> None:
        if not isinstance(value, dict):
            return
        current_frame = value if value.get("role") == "geometry_container" else frame
        if current_frame is not None and value.get("role") == "geometric_construct" and value.get("id"):
            frame_by_geometry_id[str(value["id"])] = current_frame
        for key in ("children", "operation_groups"):
            for child in value.get(key) or []:
                collect_frames(child, current_frame)

    for page in pages:
        for root in page.get("group_tree") or []:
            collect_frames(root)

    def transform_children(children: list[Any]) -> list[Any]:
        return [transform_node(child) for child in children]

    def transform_node(value: Any) -> Any:
        nonlocal promoted
        if not isinstance(value, dict):
            return value
        if value.get("role") != "content_group" or value.get("layout_kind"):
            result = copy.deepcopy(value)
            if "children" in result:
                result["children"] = transform_children(result.get("children") or [])
            if "operation_groups" in result:
                result["operation_groups"] = transform_children(result.get("operation_groups") or [])
            return result

        original = copy.deepcopy(value)
        group_id = str(original.get("id") or f"content-group-{promoted + 1}")
        child_values = transform_children(
            (original.get("children") or []) + (original.get("operation_groups") or [])
        )
        table = {
            key: copy.deepcopy(item)
            for key, item in original.items()
            if key not in {"children", "operation_groups", "operation_ordinals", "source_operation_ordinals"}
        }
        frame = None
        for child in child_values:
            if isinstance(child, dict) and child.get("role") == "geometric_construct":
                frame = frame_by_geometry_id.get(str(child.get("id")))
                if frame is not None:
                    break
        table_bbox = copy.deepcopy(original.get("bbox") or {})
        if frame is not None and frame.get("bbox"):
            table_bbox = _union_boxes([table_bbox, frame["bbox"]])
        frame_children = [
            {
                "id": f"{group_id}::frame::{ordinal}",
                "type": "group",
                "role": "container_border_operation",
                "bbox": copy.deepcopy((frame or {}).get("bbox") or table_bbox),
                "operation_ordinals": [int(ordinal)],
            }
            for ordinal in (frame or {}).get("border_operations") or []
        ]
        table.update({
            "id": group_id,
            "type": "group",
            "role": "content_group",
            "layout_kind": "table",
            "bbox": table_bbox,
            "children": [],
        })
        cell = {
            key: copy.deepcopy(item)
            for key, item in original.items()
            if key not in {"children", "operation_groups", "operation_ordinals", "source_operation_ordinals"}
        }
        cell.update({
            "id": f"{group_id}::cell",
            "type": "group",
            "role": "content_group",
            "layout_kind": "cell",
            "bbox": copy.deepcopy(table_bbox),
            "children": frame_children + child_values,
        })
        table["children"] = [cell]
        promoted += 1
        replaced_ids.append(group_id)
        return table

    for page in pages:
        if "group_tree" in page:
            page["group_tree"] = transform_children(page.get("group_tree") or [])
        if "operation_groups" in page:
            page["operation_groups"] = transform_children(page.get("operation_groups") or [])
    out.setdefault("provenance", {})["one_cell_tables"] = {
        "transform": ONE_CELL_TABLE_TRANSFORM_ID,
        "groups_replaced": promoted,
        "replaced_group_ids": replaced_ids,
    }
    return out



def apply_intrinsic_container_layout(input_data: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Grow intrinsic containers from child bounds and replace frame ops with a border realization."""
    out = copy.deepcopy(input_data)
    options = options or {}
    padding = options.get("padding") or {}
    default_padding = {
        "left": float(padding.get("left", 0)),
        "top": float(padding.get("top", 0)),
        "right": float(padding.get("right", 0)),
        "bottom": float(padding.get("bottom", 0)),
    }

    def border_operations(bbox: dict[str, Any], page_height: float) -> list[dict[str, Any]]:
        x = float(bbox.get("x", 0))
        y = page_height - float(bbox.get("y", 0)) - float(bbox.get("h", 0))
        return [
            {"operator": "q", "operands": []},
            {"operator": "G", "operands": [0.0]},
            {"operator": "w", "operands": [0.96]},
            {"operator": "re", "operands": [x, y, float(bbox.get("w", 0)), float(bbox.get("h", 0))]},
            {"operator": "S", "operands": []},
            {"operator": "Q", "operands": []},
        ]

    def layout_node(node: dict[str, Any], page_height: float) -> dict[str, Any]:
        result = copy.deepcopy(node)
        children = [layout_node(child, page_height) for child in _node_children(result)]
        frame_children = [child for child in children if child.get("role") == "container_border_operation"]
        content_children = [child for child in children if child.get("role") != "container_border_operation"]
        result["children"] = content_children
        result.pop("operation_groups", None)
        if result.get("layout_kind") not in {"cell", "table"}:
            if children:
                result["children"] = children
            return result

        policy = copy.deepcopy(result.get("layout") or {})
        policy.setdefault("sizing", "intrinsic")
        policy.setdefault("overflow", "grow")
        policy.setdefault("overlap", "allowed")
        result["layout"] = policy
        boxes = [child.get("bbox") for child in content_children if child.get("bbox")]
        existing = result.get("bbox") or {}
        if policy["overlap"] in {"forbidden", "reject"}:
            for index, left in enumerate(boxes):
                for right in boxes[index + 1:]:
                    if (
                        float(left.get("x", 0)) < float(right.get("x", 0)) + float(right.get("w", 0))
                        and float(right.get("x", 0)) < float(left.get("x", 0)) + float(left.get("w", 0))
                        and float(left.get("y", 0)) < float(right.get("y", 0)) + float(right.get("h", 0))
                        and float(right.get("y", 0)) < float(left.get("y", 0)) + float(left.get("h", 0))
                    ):
                        raise ValueError(f"{result.get('id', 'container')}: overlapping children are forbidden")
        if policy["overflow"] == "reject" and existing:
            x1, y1 = float(existing.get("x", 0)), float(existing.get("y", 0))
            x2 = x1 + float(existing.get("w", 0))
            y2 = y1 + float(existing.get("h", 0))
            for box in boxes:
                bx1, by1 = float(box.get("x", 0)), float(box.get("y", 0))
                bx2, by2 = bx1 + float(box.get("w", 0)), by1 + float(box.get("h", 0))
                if bx1 < x1 or by1 < y1 or bx2 > x2 or by2 > y2:
                    raise ValueError(f"{result.get('id', 'container')}: child overflows fixed bounds")
        if boxes and policy["sizing"] == "intrinsic" and policy["overflow"] == "grow":
            grown = _union_boxes([existing] + boxes)
        else:
            grown = copy.deepcopy(existing)
        grown["x"] = float(grown.get("x", 0)) - default_padding["left"]
        grown["y"] = float(grown.get("y", 0)) - default_padding["top"]
        grown["w"] = float(grown.get("w", 0)) + default_padding["left"] + default_padding["right"]
        grown["h"] = float(grown.get("h", 0)) + default_padding["top"] + default_padding["bottom"]
        result["bbox"] = grown
        if result.get("layout_kind") in {"cell", "table"} and any(
            float(grown.get(key, 0)) != float(existing.get(key, 0))
            for key in ("x", "y", "w", "h")
        ):
            before_bottom = float(existing.get("y", 0)) + float(existing.get("h", 0))
            after_bottom = float(grown.get("y", 0)) + float(grown.get("h", 0))
            policy["growth"] = {
                "before": copy.deepcopy(existing),
                "after": copy.deepcopy(grown),
                "delta": {
                    "x": float(grown.get("x", 0)) - float(existing.get("x", 0)),
                    "y": float(grown.get("y", 0)) - float(existing.get("y", 0)),
                    "w": float(grown.get("w", 0)) - float(existing.get("w", 0)),
                    "h": float(grown.get("h", 0)) - float(existing.get("h", 0)),
                    "bottom": after_bottom - before_bottom,
                },
            }
        if result.get("layout_kind") in {"table", "cell"}:
            result.setdefault("coordinate_space", {
                "name": "page",
                "origin": "top-left",
                "mapping": "absolute",
            })
        if result.get("layout_kind") == "cell" and frame_children:
            source_ordinals = sorted({
                int(ordinal)
                for child in frame_children
                for ordinal in child.get("operation_ordinals") or []
            })
            result["children"].insert(0, {
                "id": f"{result.get('id', 'cell')}::border",
                "type": "group",
                "role": "container_border",
                "bbox": copy.deepcopy(grown),
                "source_operation_ordinals": source_ordinals,
                "render_operations": border_operations(grown, page_height),
                "coordinate_space": {"name": "page", "origin": "top-left", "mapping": "absolute"},
            })
        if result.get("layout_kind") == "table":
            cell_boxes = [child.get("bbox") for child in result["children"] if child.get("layout_kind") == "cell" and child.get("bbox")]
            if cell_boxes:
                result["bbox"] = _union_boxes([result["bbox"]] + cell_boxes)
        return result

    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    for page in pages:
        media_box = page.get("media_box") or {}
        if isinstance(media_box, dict):
            media_height = media_box.get("h")
        elif isinstance(media_box, (list, tuple)) and len(media_box) >= 4:
            media_height = float(media_box[3]) - float(media_box[1])
        else:
            media_height = None
        page_height = float(page.get("height") or media_height or 841.92)
        page["operation_groups"] = [layout_node(group, page_height) for group in page.get("operation_groups") or []]
    out.setdefault("provenance", {})["intrinsic_layout"] = {
        "transform": INTRINSIC_LAYOUT_TRANSFORM_ID,
        "sizing": "intrinsic",
        "overflow": "grow",
        "overlap": "allowed",
    }
    return out


def _node_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        child
        for key in ("children", "operation_groups")
        for child in node.get(key) or []
        if isinstance(child, dict)
    ]


def materialize_operation_tree(input_data: dict[str, Any]) -> dict[str, Any]:
    """Materialize operation ownership as a recursive tree without changing raw operations."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    materialized = 0
    for page in pages:
        operations = page.get("operations") or (page.get("realization") or {}).get("operations") or []
        structured = page.get("structured_operations")
        if not structured:
            raise ValueError(f"page {page.get('id')} has no structured_operations")
        from tools.extract_pdf_ir import validate_operation_parity
        parity_errors = validate_operation_parity(structured, operations)
        if parity_errors:
            raise ValueError(f"page {page.get('id')} structured parity failure: {'; '.join(parity_errors)}")
        groups = page.get("operation_groups") or []
        ownership_errors = operation_group_ownership_diagnostics(groups)
        if ownership_errors:
            raise ValueError(f"page {page.get('id')} has invalid operation ownership: {ownership_errors}")
        by_ordinal = {int(item["ordinal"]): item for item in operations}
        claimed: set[int] = set()
        seen_groups: set[str] = set()

        def build(group: dict[str, Any]) -> tuple[dict[str, Any], set[int], int | None]:
            group_id = str(group.get("id") or f"anonymous-{len(seen_groups)}")
            if group_id in seen_groups:
                raise ValueError(f"page {page.get('id')} repeats operation group {group_id}")
            seen_groups.add(group_id)
            child_groups: list[dict[str, Any]] = []
            references: list[dict[str, Any]] = []
            reference_nodes: list[tuple[int, dict[str, Any]]] = []
            for child in (group.get("children") or []) + (group.get("operation_groups") or []):
                if not isinstance(child, dict):
                    continue
                if child.get("role") == "associated_text":
                    reference = copy.deepcopy(child)
                    references.append(reference)
                    source_ordinals = [int(value) for value in reference.get("source_operation_ordinals") or []]
                    if source_ordinals:
                        reference_nodes.append((
                            min(source_ordinals),
                            {
                                "type": "operation_group_reference",
                                "id": str(reference.get("id") or f"reference-{min(source_ordinals)}"),
                                "group": reference,
                                "children": [],
                                "references": [],
                            },
                        ))
                elif child.get("type") in {"group", "draw_group"} or child.get("operation_ordinals"):
                    child_groups.append(child)
            child_nodes: list[tuple[int, dict[str, Any]]] = []
            child_owned: set[int] = set()
            for child in child_groups:
                node, owned, first = build(child)
                child_owned.update(owned)
                if first is not None:
                    child_nodes.append((first, node))
            direct = {
                int(value)
                for value in (group.get("operation_ordinals") or []) + (group.get("source_operation_ordinals") or [])
            }
            duplicate = direct & child_owned
            if duplicate:
                raise ValueError(f"operation group {group_id} claims child ordinals {sorted(duplicate)}")
            unknown = direct - set(by_ordinal)
            if unknown:
                raise ValueError(f"operation group {group_id} references unknown ordinals {sorted(unknown)}")
            owned = direct | child_owned
            duplicate_global = claimed & direct
            if duplicate_global:
                raise ValueError(f"operation ownership repeats ordinals {sorted(duplicate_global)}")
            claimed.update(direct)
            group_meta = copy.deepcopy(group)
            group_meta.pop("children", None)
            group_meta.pop("operation_groups", None)
            operation_nodes = [(ordinal, {"type": "operation", "source_ordinal": ordinal}) for ordinal in sorted(direct)]
            node_children = [item for _, item in sorted([*operation_nodes, *child_nodes, *reference_nodes], key=lambda item: item[0])]
            node = {"type": "operation_group", "id": group_id, "group": group_meta, "children": node_children, "references": references}
            first = min(owned) if owned else None
            return node, owned, first

        root_nodes: list[tuple[int, dict[str, Any]]] = []
        for group in groups:
            node, _, first = build(group)
            if first is not None:
                root_nodes.append((first, node))
        unowned = [(int(item["ordinal"]), {"type": "operation", "source_ordinal": int(item["ordinal"])}) for item in operations if int(item["ordinal"]) not in claimed]
        children = [node for _, node in sorted([*root_nodes, *unowned], key=lambda item: item[0])]
        page["operation_tree"] = {"schema": "pdf-training-operation-tree-v1", "children": children}
        materialized += 1
    out.setdefault("provenance", {})["operation_tree"] = {"transform": OPERATION_TREE_TRANSFORM_ID, "pages": materialized}
    return out


def expand_associated_content(input_data: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Wrap geometry and its recursively owned associated text in a content group."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    created = 0
    ownership_diagnostics: dict[str, list[dict[str, Any]]] = {}
    for page in pages:
        groups = page.get("operation_groups") or []
        replacement = []
        for group in groups:
            text_nodes: list[dict[str, Any]] = []

            def collect_text(value: Any) -> None:
                if not isinstance(value, dict):
                    return
                if value.get("role") == "associated_text":
                    text_nodes.append(value)
                for key in ("children", "operation_groups"):
                    for child in value.get(key) or []:
                        collect_text(child)

            collect_text(group)
            boxes = [group.get("bbox") or {}] + [node.get("bbox") or {} for node in text_nodes]
            if not text_nodes or not all(all(key in box for key in ("x", "y", "w", "h")) for box in boxes):
                replacement.append(group)
                continue
            content_box = _union_boxes(boxes)
            content_group = copy.deepcopy(group)
            wrapper = {
                "id": f"{group.get('id')}-content",
                "type": "group",
                "role": "content_group",
                "bbox": content_box,
                "children": [content_group],
                "content_bbox": copy.deepcopy(content_box),
                "source_group_id": group.get("id"),
            }
            old_parent = group.get("parent_context_bbox_pdf") or {}
            media = page.get("media_box") or []
            page_width = float(page.get("width") or (media[2] - media[0] if len(media) >= 4 else 1))
            page_height = float(page.get("height") or (media[3] - media[1] if len(media) >= 4 else 1))
            raw_width = float(media[2] - media[0]) if len(media) >= 4 else page_width
            raw_height = float(media[3] - media[1]) if len(media) >= 4 else page_height
            wrapper["parent_context_bbox_pdf"] = copy.deepcopy(old_parent)
            wrapper["relative_offset_pdf"] = {
                "x": float(content_box["x"]) * raw_width / page_width - float(old_parent.get("x", 0)),
                "y": float(content_box["y"]) * raw_height / page_height - float(old_parent.get("y", 0)),
            }
            content_group["parent_context_bbox_pdf"] = {
                "x": float(content_box["x"]) * raw_width / page_width,
                "y": float(content_box["y"]) * raw_height / page_height,
                "w": float(content_box["w"]) * raw_width / page_width,
                "h": float(content_box["h"]) * raw_height / page_height,
            }
            content_group["relative_offset_pdf"] = {
                "x": (float(content_group["bbox"]["x"]) - float(content_box["x"])) * raw_width / page_width,
                "y": (float(content_group["bbox"]["y"]) - float(content_box["y"])) * raw_height / page_height,
            }
            replacement.append(wrapper)
            created += 1
        page["operation_groups"] = replacement
        page_id = str(page.get("id") or f"page-{len(ownership_diagnostics) + 1:03d}")
        ownership_diagnostics[page_id] = operation_group_ownership_diagnostics(replacement)
    out.setdefault("provenance", {})["content_group"] = {
        "transform": CONTENT_GROUP_TRANSFORM_ID,
        "created": created,
        "ownership_diagnostics": ownership_diagnostics,
    }
    return out


ORIGIN_PROOF_TRANSFORM_ID = "geometry.move-groups-to-parent-origin.v1"


def move_groups_to_parent_origin(input_data: dict[str, Any], *, options: dict[str, Any] | None = None) -> dict[str, Any]:
    """Move localized groups and their associated text placements to parent origin."""
    out = copy.deepcopy(input_data)
    pages = out.get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    options = options or {}
    target_group_ids = {str(value) for value in options.get("group_ids") or []}
    moved_text = 0
    for page in pages:
        operations = page.get("operations") or []

        def visit(group: dict[str, Any], inside_moved_container: bool = False) -> None:
            nonlocal moved_text
            is_content_container = (
                group.get("role") == "content_group"
                and (not target_group_ids or str(group.get("id")) in target_group_ids)
            )
            if is_content_container:
                original_offset = copy.deepcopy(group.get("relative_offset_pdf") or {"x": 0.0, "y": 0.0})
                group["original_relative_offset_pdf"] = original_offset
                group["relative_offset_pdf"] = {"x": 0.0, "y": 0.0}
                group["render_transform"] = [
                    1.0,
                    0.0,
                    0.0,
                    1.0,
                    -float(original_offset.get("x", 0.0)),
                    float(original_offset.get("y", 0.0)),
                ]
                group["placement_mode"] = "parent-origin-proof"
            elif group.get("operations_localized") and not inside_moved_container and not target_group_ids:
                original_offset = copy.deepcopy(group.get("relative_offset_pdf") or {"x": 0.0, "y": 0.0})
                group["original_relative_offset_pdf"] = original_offset
                group["relative_offset_pdf"] = {"x": 0.0, "y": 0.0}
                group["placement_mode"] = "parent-origin-proof"
                dx = float(original_offset.get("x", 0.0))
                dy = float(original_offset.get("y", 0.0))
                seen = set()
                for node in group.get("children") or []:
                    if node.get("role") != "associated_text":
                        continue
                    span = [int(value) for value in node.get("source_operation_ordinals") or []]
                    if len(span) < 2:
                        continue
                    start, end = min(span), max(span)
                    for operation in operations:
                        operation_ordinal = int(operation.get("ordinal", -1))
                        if operation_ordinal < start or operation_ordinal > end or operation_ordinal in seen:
                            continue
                        operands = operation.get("operands") or []
                        if operation.get("operator") == "Tm" and len(operands) >= 6:
                            operation.setdefault("original_operands", copy.deepcopy(operands))
                            operands[4] = float(operands[4]) - dx
                            operands[5] = float(operands[5]) + dy
                            seen.add(operation_ordinal)
                            moved_text += 1
                        elif operation.get("operator") == "re" and len(operands) >= 4:
                            operation.setdefault("original_operands", copy.deepcopy(operands))
                            operands[0] = float(operands[0]) - dx
                            operands[1] = float(operands[1]) + dy
                            seen.add(operation_ordinal)
            children = (group.get("children") or []) + (group.get("operation_groups") or [])
            for child in children:
                if isinstance(child, dict):
                    visit(child, inside_moved_container or is_content_container)

        for group in page.get("operation_groups") or []:
            visit(group)

    out.setdefault("provenance", {})["move_groups_to_parent_origin"] = {
        "transform": ORIGIN_PROOF_TRANSFORM_ID,
        "text_matrices_moved": moved_text,
        "group_ids": sorted(target_group_ids) if target_group_ids else "all_content_groups",
    }
    return out
