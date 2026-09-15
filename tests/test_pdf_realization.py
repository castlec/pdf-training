from __future__ import annotations

import pytest

from tools.pdf_realization import RealizationError, node_realization, operation, realization_page, resources


def test_realization_page_preserves_order_resources_and_scopes() -> None:
    page = realization_page(
        page_id="page-001",
        width=595,
        height=842,
        page_resources=resources(
            fonts={"F1": {"object": "font-1"}},
            xobjects={"Meta16": {"object": "form-16"}},
        ),
        operations=[
            operation("save", ordinal=0),
            operation("draw_form", ordinal=1, xobject="Meta16", transform=[1, 0, 0, 1, 10, 20]),
            operation("begin_text", ordinal=2),
            operation("set_font", ordinal=3, font="F1"),
            operation("end_text", ordinal=4),
            operation("restore", ordinal=5),
        ],
    )
    assert page["schema"] == "pdf-training-realization-page-v1"
    assert [item["ordinal"] for item in page["operations"]] == list(range(6))
    assert set(page["resources"]) == {"fonts", "xobjects", "extgstates", "color_spaces", "patterns", "shadings", "properties"}


def test_container_scope_has_explicit_transform() -> None:
    node = node_realization(
        {"id": "problem-1", "type": "container", "children": []},
        operations=[operation("save", ordinal=0), operation("restore", ordinal=1)],
        local_transform=[1, 0, 0, 1, 24, 36],
    )
    assert node["realization"]["local_transform"]["e"] == 24.0


def test_undeclared_resource_is_rejected() -> None:
    with pytest.raises(RealizationError, match="undeclared fonts resource"):
        realization_page(
            page_id="page-001",
            width=10,
            height=10,
            operations=[operation("set_font", ordinal=0, font="Missing")],
        )


def test_unbalanced_scope_is_rejected() -> None:
    with pytest.raises(RealizationError, match="unclosed operation scope"):
        realization_page(
            page_id="page-001",
            width=10,
            height=10,
            operations=[operation("save", ordinal=0)],
        )
