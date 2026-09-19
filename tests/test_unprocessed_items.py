from tools.transform_library import apply_transform


TRANSFORM_ID = "diagnostics.unprocessed-items.v1"


def test_unprocessed_item_diagnostics_preserves_operations_and_reports_candidates():
    operations = [
        {"ordinal": 0, "operator": "m", "operands": [1, 2]},
        {"ordinal": 1, "operator": "l", "operands": [3, 4]},
        {"ordinal": 2, "operator": "S", "operands": []},
        {"ordinal": 3, "operator": "re", "operands": [10, 20, 30, 1]},
        {"ordinal": 4, "operator": "f*", "operands": []},
        {"ordinal": 5, "operator": "BT", "operands": []},
        {"ordinal": 6, "operator": "TJ", "operands": [[{"type": "string", "value": "x"}]]},
        {"ordinal": 7, "operator": "ET", "operands": []},
    ]
    document = {
        "schema": "pdf-training-pdf-ir-v1",
        "type": "document",
        "role": "document_container",
        "pages": [
            {
                "type": "page",
                "role": "page_container",
                "id": "page-001",
                "media_box": [0, 0, 100, 100],
                "operations": operations,
                "operation_groups": [
                    {
                        "id": "paint-group",
                        "type": "group",
                        "children": [],
                        "source_operation_ordinals": [3, 4],
                    }
                ],
            }
        ],
        "objects": {},
    }

    result = apply_transform(document, TRANSFORM_ID)
    report = result["pages"][0]["unprocessed_items"]

    assert result["pages"][0]["operations"] == operations
    assert report["unprocessed_item_count"] == 2
    assert report["counts"] == {
        "radical_or_geometry_stroke_candidate": 1,
        "text_content_candidate": 1,
    }
    assert report["items"][0]["source_operation_ordinals"] == [0, 1, 2]
    assert report["items"][0]["ownership"] == "unowned"
    assert report["items"][1]["classification"] == "text_content_candidate"
    assert report["owned_operation_count"] == 2
    document_summary = result["diagnostics"]["unprocessed_items"]
    assert document_summary["unprocessed_item_count"] == 2
    assert document_summary["pages"] == [
        {
            "page_id": "page-001",
            "unprocessed_item_count": 2,
            "counts": {
                "radical_or_geometry_stroke_candidate": 1,
                "text_content_candidate": 1,
            },
            "owned_operation_count": 2,
        }
    ]


def test_thin_rectangle_is_reported_as_rule_or_fraction_candidate():
    document = {
        "schema": "pdf-training-pdf-ir-v1",
        "type": "document",
        "role": "document_container",
        "pages": [
            {
                "type": "page",
                "role": "page_container",
                "id": "page-001",
                "media_box": [0, 0, 100, 100],
                "operations": [
                    {"ordinal": 0, "operator": "re", "operands": [10, 20, 40, 1]},
                    {"ordinal": 1, "operator": "f*", "operands": []},
                ],
            }
        ],
        "objects": {},
    }

    result = apply_transform(document, TRANSFORM_ID)

    assert result["pages"][0]["unprocessed_items"]["items"][0]["classification"] == "thin_rule_or_fraction_bar_candidate"
    assert result["pages"][0]["unprocessed_items"]["items"][0]["bbox_pdf"] == {"x": 10.0, "y": 20.0, "w": 40.0, "h": 1.0}
