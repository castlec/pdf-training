from __future__ import annotations

import copy

import pytest

from tools.font_catalog import FontCatalogError, catalogue_fonts
from tools.transform_contract import TransformContractError, validate_transform_result
from tools.transform_library import apply_transform


def _document() -> dict:
    return {
        "schema": "pdf-training-pdf-ir-v1",
        "objects": {
            "1 0": {
                "kind": "dictionary",
                "values": {
                    "/Resources": {
                        "/Font": {
                            "/F1": {"ref": "2 0"},
                            "/F2": {"ref": "3 0"},
                        }
                    }
                },
            },
            "2 0": {
                "kind": "dictionary",
                "values": {
                    "/Type": {"type": "name", "value": "/Font"},
                    "/Subtype": {"type": "name", "value": "/TrueType"},
                    "/BaseFont": {"type": "name", "value": "/ABCDEF+Example-Regular"},
                    "/Encoding": {"type": "name", "value": "/WinAnsiEncoding"},
                    "/FirstChar": 32,
                    "/LastChar": 122,
                    "/Widths": [600, 0],
                },
            },
            "3 0": {
                "kind": "dictionary",
                "values": {
                    "/Type": {"type": "name", "value": "/Font"},
                    "/Subtype": {"type": "name", "value": "/TrueType"},
                    "/BaseFont": {"type": "name", "value": "/Example-Bold"},
                    "/FirstChar": 32,
                    "/LastChar": 122,
                    "/Widths": [700, 700],
                },
            },
        },
        "pages": [
            {
                "index": 0,
                "resources_ref": {"ref": "1 0"},
                "operations": [
                    {"ordinal": 0, "operator": "Tf", "operands": [{"type": "name", "value": "/F1"}, 12]},
                    {"ordinal": 1, "operator": "Tf", "operands": [{"type": "name", "value": "/F2"}, 18]},
                ],
            }
        ],
    }


def test_catalogue_is_metadata_only_by_default() -> None:
    document = _document()
    original_operations = copy.deepcopy(document["pages"][0]["operations"])

    result = apply_transform(document, "font.catalog.v1")

    assert result is not document
    assert result["pages"][0]["operations"] == original_operations
    catalog = result["metadata"]["font_catalog"]
    assert catalog["schema"] == "pdf-training-font-catalog-v1"
    assert catalog["preserves_source_operations"] is True
    assert catalog["rewritten_tf_operations"] == 0
    first = catalog["fonts"][0]
    assert first["resource_names"] == ["F1"]
    assert first["family"] == "Example"
    assert first["style"] == "Regular"
    assert first["subset"] is True
    assert first["usage"] == {"pages": [0], "sizes": [12], "tf_operations": 1}
    assert "non-positive-width" in first["issues"]


def test_catalogue_can_explicitly_remap_existing_resources() -> None:
    document = _document()

    result = apply_transform(document, "font.catalog.v1", {"resource_mapping": {"/F1": "/F2"}})

    assert result["pages"][0]["operations"][0]["operands"][0] == {"type": "name", "value": "/F2"}
    catalog = result["metadata"]["font_catalog"]
    assert catalog["preserves_source_operations"] is False
    assert catalog["rewritten_tf_operations"] == 1
    assert catalog["resource_mapping"] == {"/F1": "/F2"}


def test_catalogue_rejects_missing_remap_target() -> None:
    with pytest.raises(FontCatalogError, match="mapping target /F9"):
        catalogue_fonts(_document(), {"resource_mapping": {"F1": "F9"}})


def test_font_catalog_metadata_is_part_of_the_transform_cfg() -> None:
    document = catalogue_fonts(_document())

    validate_transform_result(document)
    invalid = copy.deepcopy(document)
    invalid["metadata"]["font_catalog"]["schema"] = "unknown"
    with pytest.raises(TransformContractError, match="unknown schema"):
        validate_transform_result(invalid)
