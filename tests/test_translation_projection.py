import base64
import copy
import json

from tools.export_translation_projection import build_request
from tools.translation_projection import project_translation, validate_translation_projection


def _document():
    cmap = b"""/CIDInit /ProcSet findresource begin
begincmap
1 begincodespacerange
<0000> <00FF>
endcodespacerange
2 beginbfchar
<0003> <0020>
<0048> <0041>
endbfchar
endcmap
"""
    return {
        "schema": "pdf-training-pdf-ir-v1",
        "source": {"sha256": "fixture"},
        "pages": [{
            "id": "page-001",
            "index": 0,
            "page_ref": "page 0",
            "media_box": [0, 0, 200, 300],
            "operations": [
                {"ordinal": 0, "operator": "BT", "operands": []},
                {"ordinal": 1, "operator": "Tf", "operands": [{"type": "name", "value": "/F1"}, 12]},
                {"ordinal": 2, "operator": "Tm", "operands": [1, 0, 0, 1, 10, 280]},
                {"ordinal": 3, "operator": "TJ", "operands": [[
                    {"type": "string", "value": "\x00H\x00\x03\x00H"}
                ]]},
                {"ordinal": 4, "operator": "ET", "operands": []},
            ],
            "operation_groups": [{
                "id": "section-1",
                "type": "group",
                "role": "section",
                "operation_ordinals": [0, 1, 2, 3, 4],
                "children": [],
            }],
        }],
        "objects": {
            "page 0": {"values": {"/Resources": {"/Font": {"/F1": {"ref": "font 0"}}}}},
            "font 0": {"values": {"/BaseFont": {"type": "name", "value": "/MyriadPro-Regular"}, " /ToUnicode": {"ref": "cmap 0"}, "/ToUnicode": {"ref": "cmap 0"}}},
            "cmap 0": {"decoded_bytes_b64": base64.b64encode(cmap).decode("ascii")},
        },
    }


def test_projection_decodes_embedded_to_unicode_and_preserves_active_structure():
    source = _document()
    active_before = copy.deepcopy(source["pages"][0]["operation_groups"])
    projected = project_translation(source)
    units = projected["metadata"]["translation_projection"]["units"]
    assert [unit["source_text"] for unit in units] == ["A A"]
    assert units[0]["context"]["group_path"][0]["id"] == "section-1"
    assert projected["pages"][0]["operation_groups"] == active_before


def test_projection_validation_and_export_remove_render_noise():
    projected = validate_translation_projection(project_translation(_document()))
    request = build_request(projected)
    assert request["status"] == "ready_for_model"
    assert request["model_invoked"] is False
    assert request["units"] == [{
        "id": "page-001-text-00003",
        "text": "A A",
        "page_id": "page-001",
        "group_path": [{"id": "section-1", "role": "section"}],
        "geometry_group_ids": [],
        "table_path": [],
    }]
    assert "bbox" not in json.dumps(request)
    assert "operation_ordinal" not in json.dumps(request)
