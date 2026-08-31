from __future__ import annotations

from tests.commercial_api.adapters import (
    FoxitStructuralLayoutAdapter,
    PdfRestLayoutAdapter,
)
from tests.commercial_api.providers import _normalize_foxit, _normalize_pdfrest


def test_pdfrest_normalizes_word_typography_and_bottom_left_coordinates() -> None:
    evidence = _normalize_pdfrest(
        {
            "words": [
                {
                    "text": "Example",
                    "page": 1,
                    "coordinates": {
                        "topLeft": {"x": 72, "y": 720},
                        "topRight": {"x": 120, "y": 720},
                        "bottomLeft": {"x": 72, "y": 700},
                        "bottomRight": {"x": 120, "y": 700},
                    },
                    "style": {
                        "font": {"name": "Calibri-Bold", "size": 12},
                        "color": {"space": "DeviceRGB", "values": [0, 0, 0]},
                    },
                }
            ],
            "fullText": "Example",
        },
        {1: (612, 792)},
    )

    block = evidence.text_blocks[0]
    assert evidence.provider == "pdfrest"
    assert block.font_family == "Calibri-Bold"
    assert block.font_size == 12
    assert block.bold is True
    assert block.color == "DeviceRGB(0,0,0)"
    assert block.bbox is not None
    assert block.bbox.top == round((792 - 720) / 792, 6)
    assert block.bbox.bottom == round((792 - 700) / 792, 6)


def test_foxit_normalizes_versioned_elements_and_table_cells() -> None:
    evidence = _normalize_foxit(
        {
            "analyzeResult": {
                "version": {"schema": "1.0.7"},
                "pages": [{"pageNumber": 1, "size": {"width": 612, "height": 792}}],
                "elements": [
                    {
                        "type": "title",
                        "content": {
                            "text": "INVOICE",
                            "style": {"fontFamilyName": "Arial", "fontSize": 24},
                        },
                        "region": {
                            "page": 1,
                            "boundingBox": [90, 71, 189, 71, 189, 99, 90, 99],
                        },
                    },
                    {
                        "type": "table",
                        "content": {
                            "body": {
                                "cells": [
                                    {
                                        "paragraph": {
                                            "content": {"text": "Description"}
                                        }
                                    }
                                ]
                            }
                        },
                        "region": {"page": 1},
                    },
                ],
            }
        },
        {},
    )

    assert evidence.provider == "foxit_structural"
    assert evidence.provider_version == "1.0.7"
    assert evidence.table_count == 1
    assert evidence.text_blocks[0].role == "title"
    assert evidence.text_blocks[0].font_size == 24
    assert evidence.text_blocks[1].text == "Description"
    assert evidence.paragraphs[0].text == "INVOICE"


def test_additional_layout_adapters_declare_only_their_credentials() -> None:
    assert PdfRestLayoutAdapter().capabilities().required_config == ["PDFREST_API_KEY"]
    assert FoxitStructuralLayoutAdapter().capabilities().required_config == [
        "FOXIT_PDF_SERVICES_CLIENT_ID",
        "FOXIT_PDF_SERVICES_CLIENT_SECRET",
    ]
