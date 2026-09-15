from __future__ import annotations

import pikepdf
import pytest

from tools.extract_pdf_ir import structured_operations
from tools.render_pdf_ir import Resolver, operation_tree_groups, serialize_structured_operations


def op(ordinal: int, operator: str, operands: list[object] | None = None) -> dict[str, object]:
    return {"ordinal": ordinal, "operator": operator, "operands": operands or []}


def test_structured_serializer_replays_verified_operation_order() -> None:
    operations = [op(0, "q"), op(1, "BT"), op(2, "ET"), op(3, "Q")]
    tree = structured_operations(operations)
    pdf = pikepdf.Pdf.new()
    stream = serialize_structured_operations(pdf, Resolver(pdf, {}), tree, operations)

    assert [str(item.operator) for item in pikepdf.parse_content_stream(stream)] == ["q", "BT", "ET", "Q"]


def test_structured_serializer_rejects_parity_failure() -> None:
    operations = [op(0, "q"), op(1, "Q")]
    tree = structured_operations(operations)
    tree["children"][0]["close_ordinal"] = 0
    pdf = pikepdf.Pdf.new()

    with pytest.raises(ValueError, match="structured operation parity failure"):
        serialize_structured_operations(pdf, Resolver(pdf, {}), tree, operations)


def test_operation_tree_groups_preserves_reference_contexts() -> None:
    tree = {
        "children": [
            {
                "type": "operation_group",
                "id": "container",
                "group": {"id": "container", "operation_ordinals": []},
                "children": [
                    {
                        "type": "operation_group_reference",
                        "id": "label",
                        "group": {
                            "id": "label",
                            "source_operation_ordinals": [4, 5],
                            "paint": {"debug": {"stroke_gray": 0.2}},
                        },
                        "children": [],
                        "references": [],
                    }
                ],
                "references": [],
            }
        ]
    }

    groups = operation_tree_groups(tree)

    assert groups[0]["children"][0]["id"] == "label"
    assert groups[0]["children"][0]["source_operation_ordinals"] == [4, 5]
    assert groups[0]["children"][0]["paint"]["debug"]["stroke_gray"] == 0.2
