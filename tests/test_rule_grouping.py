from tools.rule_grouping import apply_horizontal_rule_grouping, group_page_by_horizontal_rules


def test_horizontal_rules_define_sections_and_retain_nodes():
    page = {
        "id": "p4",
        "width": 100,
        "height": 200,
        "nodes": [
            {"id": "top", "type": "text", "bbox": {"x": 10, "y": 10, "w": 30, "h": 10}},
            {"id": "rule", "type": "rule", "bbox": {"x": 0, "y": 80, "w": 100, "h": 1}},
            {"id": "bottom", "type": "text", "bbox": {"x": 10, "y": 100, "w": 30, "h": 10}},
        ],
    }
    groups = group_page_by_horizontal_rules(page)
    assert len(groups) == 2
    assert groups[0]["node_ids"] == ["top"]
    assert groups[1]["node_ids"] == ["bottom"]
    assert page["nodes"][0]["id"] == "top"


def test_grouping_is_additive_and_preserves_source_nodes():
    data = {"pages": [{"id": "p1", "width": 100, "height": 100, "nodes": []}]}
    result = apply_horizontal_rule_grouping(data)
    assert "group_tree" in result["pages"][0]
    assert result["pages"][0]["nodes"] == []
    assert data == {"pages": [{"id": "p1", "width": 100, "height": 100, "nodes": []}]}
