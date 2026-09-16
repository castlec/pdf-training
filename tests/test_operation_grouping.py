from tools.operation_grouping import apply_operation_grouping


def test_marked_geometry_span_is_attached_to_enclosing_group():
    data = {
        "pages": [{
            "id": "p1",
            "width": 100,
            "height": 100,
            "realization": {"media_box": {"x": 0, "y": 0, "w": 100, "h": 100}, "operations": [
                {"ordinal": 0, "operator": "BDC", "operands": [{"type": "name", "value": "/P"}, {"/MCID": 1}]},
                {"ordinal": 1, "operator": "m", "operands": [10, 80]},
                {"ordinal": 2, "operator": "l", "operands": [30, 60]},
                {"ordinal": 3, "operator": "S", "operands": []},
                {"ordinal": 4, "operator": "EMC", "operands": []},
                {"ordinal": 5, "operator": "BDC", "operands": [{"type": "name", "value": "/P"}, {"/MCID": 2}]},
                {"ordinal": 6, "operator": "m", "operands": [20, 60]},
                {"ordinal": 7, "operator": "l", "operands": [30, 70]},
                {"ordinal": 8, "operator": "S", "operands": []},
                {"ordinal": 9, "operator": "EMC", "operands": []},
            ]},
            "group_tree": [{"id": "g1", "type": "group", "bbox": {"x": 0, "y": 0, "w": 100, "h": 100}, "children": []}],
        }],
    }
    page = apply_operation_grouping(data)["pages"][0]
    assert len(page["operation_groups"]) == 1
    group = page["group_tree"][0]["operation_groups"][0]
    assert group["operation_ordinals"] == list(range(10))
    assert group["source_space"] == "pdf-user-space"
    assert group["role"] == "geometric_construct"
    assert group in page["group_tree"][0]["children"]
    assert data["pages"][0].get("operation_groups") is None


def test_content_group_promotes_to_one_cell_table_without_replacing_geometry():
    from tools.operation_grouping import promote_content_groups_to_one_cell_tables

    data = {"pages": [{
        "id": "p1",
        "group_tree": [{
            "id": "section",
            "children": [{
                "id": "box",
                "role": "content_group",
                "bbox": {"x": 10, "y": 20, "w": 80, "h": 40},
                "source_bbox": {"x": 11, "y": 21, "w": 78, "h": 38},
                "operation_ordinals": [4, 5],
            }],
        }],
        "operation_groups": [],
    }]}

    result = promote_content_groups_to_one_cell_tables(data)
    table = result["pages"][0]["group_tree"][0]["children"][0]
    cell = table["children"][0]

    assert table["type"] == "group"
    assert table["role"] == "content_group"
    assert table["layout_kind"] == "table"
    assert cell["type"] == "group"
    assert cell["role"] == "content_group"
    assert cell["layout_kind"] == "cell"
    assert cell["bbox"] == table["bbox"]
    assert cell["children"] == []
    assert result["provenance"]["one_cell_tables"]["groups_replaced"] == 1
    assert result["provenance"]["one_cell_tables"]["replaced_group_ids"] == ["box"]
    assert "layout_table" not in table


def test_text_association_transfers_complete_source_span_into_child_owner():
    from tools.operation_grouping import associate_text_operations, expand_associated_content

    operations = [
        {"ordinal": 0, "operator": "BT", "operands": []},
        {"ordinal": 1, "operator": "Tm", "operands": [1, 0, 0, 1, 12, 72]},
        {"ordinal": 2, "operator": "TJ", "operands": [[{"type": "string", "value": "label"}]]},
        {"ordinal": 3, "operator": "ET", "operands": []},
    ]
    data = {"pages": [{
        "id": "p1",
        "width": 100,
        "height": 100,
        "operations": operations,
        "operation_groups": [{"id": "p1-geometry-01", "role": "geometric_construct", "bbox": {"x": 10, "y": 20, "w": 20, "h": 20}, "children": []}],
    }]}

    result = associate_text_operations(data, options={"collision_span": {"left": 10, "right": 24, "top": 10, "bottom": 0}})
    group = result["pages"][0]["operation_groups"][0]
    text_group = group["children"][0]
    assert text_group["role"] == "text_operations"
    assert text_group["operation_ordinals"] == [0, 1, 2, 3]
    assert text_group["children"][0]["source_operation_ordinals"] == [0, 2]
    expanded = expand_associated_content(result)
    wrapper = expanded["pages"][0]["operation_groups"][0]
    assert wrapper["role"] == "content_group"
    assert wrapper["children"][0]["children"][0]["role"] == "text_operations"
    assert expanded["pages"][0]["operations"] == operations
    assert "associated_text" not in data["pages"][0]["operation_groups"][0]


def test_origin_move_shifts_associated_text_matrix_with_group():
    from tools.operation_grouping import move_groups_to_parent_origin

    data = {"pages": [{
        "operations": [
            {"ordinal": 0, "operator": "BT", "operands": []},
            {"ordinal": 1, "operator": "Tm", "operands": [1, 0, 0, 1, 50, 100]},
            {"ordinal": 2, "operator": "TJ", "operands": [[{"type": "string", "value": "x"}]]},
            {"ordinal": 3, "operator": "ET", "operands": []},
        ],
        "operation_groups": [{
            "id": "geometry",
            "operations_localized": True,
            "relative_offset_pdf": {"x": 10, "y": 5},
            "children": [{"role": "associated_text", "source_operation_ordinals": [0, 2]}],
        }],
    }]}

    result = move_groups_to_parent_origin(data)
    operation = result["pages"][0]["operations"][1]
    assert operation["operands"][4:6] == [40.0, 105.0]
    assert operation["original_operands"][4:6] == [50, 100]
    assert result["pages"][0]["operation_groups"][0]["relative_offset_pdf"] == {"x": 0.0, "y": 0.0}
    assert result["provenance"]["move_groups_to_parent_origin"]["text_matrices_moved"] == 1


def test_operation_group_ownership_ignores_nonoverlapping_sparse_spans():
    from tools.operation_grouping import operation_group_ownership_diagnostics

    groups = [
        {"id": "upper", "operation_ordinals": [10, 11]},
        {"id": "lower", "operation_ordinals": [5, 15]},
    ]

    assert operation_group_ownership_diagnostics(groups) == []


def test_operation_group_ownership_reports_duplicate_ordinals():
    from tools.operation_grouping import operation_group_ownership_diagnostics

    groups = [
        {"id": "upper", "operation_ordinals": [10, 11]},
        {"id": "lower", "operation_ordinals": [10, 15]},
    ]

    assert operation_group_ownership_diagnostics(groups) == [{
        "kind": "overlapping_sibling_operation_ownership",
        "parent_id": None,
        "left_id": "upper",
        "right_id": "lower",
        "operation_ordinals": [10],
    }]


def test_operation_group_ownership_allows_explicit_nested_groups():
    from tools.operation_grouping import operation_group_ownership_diagnostics

    groups = [{
        "id": "parent",
        "operation_ordinals": [0, 5],
        "children": [
            {"id": "child", "operation_ordinals": [1, 2]},
            {"id": "text-a", "role": "associated_text", "source_operation_ordinals": [1, 3]},
            {"id": "text-b", "role": "associated_text", "source_operation_ordinals": [1, 4]},
        ],
    }]

    assert operation_group_ownership_diagnostics(groups) == []


def test_materialize_operation_tree_assigns_each_operation_once():
    from tools.extract_pdf_ir import structured_operations
    from tools.operation_grouping import materialize_operation_tree

    operations = [
        {"ordinal": 0, "operator": "q", "operands": []},
        {"ordinal": 1, "operator": "m", "operands": [10, 10]},
        {"ordinal": 2, "operator": "S", "operands": []},
        {"ordinal": 3, "operator": "Q", "operands": []},
    ]
    data = {"pages": [{
        "id": "p1",
        "operations": operations,
        "structured_operations": structured_operations(operations),
        "operation_groups": [{"id": "g1", "type": "draw_group", "role": "geometric_construct", "operation_ordinals": [1, 2], "children": []}],
    }]}

    page = materialize_operation_tree(data)["pages"][0]
    nodes = page["operation_tree"]["children"]
    assert [node["source_ordinal"] for node in nodes if node["type"] == "operation"] == [0, 3]
    assert [node["id"] for node in nodes if node["type"] == "operation_group"] == ["g1"]
    assert [node["source_ordinal"] for node in nodes[1]["children"]] == [1, 2]


def test_materialize_operation_tree_rejects_duplicate_ownership():
    from tools.extract_pdf_ir import structured_operations
    from tools.operation_grouping import materialize_operation_tree

    operations = [{"ordinal": 0, "operator": "S", "operands": []}]
    data = {"pages": [{
        "id": "p1",
        "operations": operations,
        "structured_operations": structured_operations(operations),
        "operation_groups": [
            {"id": "g1", "operation_ordinals": [0], "children": []},
            {"id": "g2", "operation_ordinals": [0], "children": []},
        ],
    }]}

    import pytest
    with pytest.raises(ValueError, match="invalid operation ownership"):
        materialize_operation_tree(data)
