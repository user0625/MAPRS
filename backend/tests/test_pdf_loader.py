from pathlib import Path

import pytest
import pymupdf

from backend.tools.pdf_loader import PDFLoader
from backend.schemas.paper import PaperDocument
from backend.scripts.generate_layout_demo_pdf import generate_layout_demo_pdf


@pytest.mark.skipif(
    not Path("backend/data/raw/example.pdf").exists(),
    reason="Test PDF does not exist.",
)
def test_pdf_loader_loads_real_pdf():
    loader = PDFLoader()

    document = loader.load("backend/data/raw/example.pdf")

    assert isinstance(document, PaperDocument)
    assert document.metadata.total_pages is not None
    assert document.metadata.total_pages > 0
    assert len(document.pages) == document.metadata.total_pages
    assert document.pages[0].page_number == 1


def test_pdf_loader_ignores_rotated_arxiv_stamp_for_title_and_authors(tmp_path):
    path = tmp_path / "pathomics.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((135, 125), "Pathology-and-genomics Multimodal Transformer", fontsize=14)
    page.insert_text((195, 145), "for Survival Outcome Prediction", fontsize=14)
    page.insert_text(
        (135, 180),
        "Kexin Ding 1, Mu Zhou 2, Dimitris N. Metaxas 2, and Shaoting Zhang 3",
        fontsize=10,
    )
    page.insert_text(
        (25, 560), "arXiv:2307.11952v1 [cs.CV] 22 Jul 2023", fontsize=20, rotate=90
    )
    page.insert_text(
        (135, 270),
        "Abstract. This paper presents a multimodal transformer for survival prediction.",
        fontsize=9,
    )
    page.insert_text((135, 520), "1 Introduction", fontsize=12)
    pdf.save(path)
    pdf.close()

    document = PDFLoader().load(path)

    assert document.metadata.title == (
        "Pathology-and-genomics Multimodal Transformer for Survival Outcome Prediction"
    )
    assert document.metadata.authors == [
        "Kexin Ding",
        "Mu Zhou",
        "Dimitris N. Metaxas",
        "Shaoting Zhang",
    ]
    assert document.metadata.fields["title"].source == "page_layout"
    rotated = [candidate for candidate in document.metadata.candidates if candidate.rotation == 90]
    assert rotated and rotated[0].text.startswith("arXiv:")
    assert "arXiv:" not in document.pages[0].text
    assert document.pages[0].layout_metadata["rotated_blocks_ignored"] == 1


def test_pdf_metadata_provenance_remains_typed_during_layout_enrichment(tmp_path):
    path = tmp_path / "metadata-title.pdf"
    pdf = pymupdf.open()
    pdf.set_metadata({"title": "Reliable Embedded Title", "author": "Ada Lovelace"})
    page = pdf.new_page(width=612, height=792)
    page.insert_text((100, 100), "A Different Large Layout Heading", fontsize=18)
    page.insert_text((100, 160), "Abstract", fontsize=12)
    page.insert_text((100, 180), "Enough body text for metadata enrichment.", fontsize=10)
    pdf.save(path)
    pdf.close()

    metadata = PDFLoader().load(path).metadata

    assert metadata.title == "Reliable Embedded Title"
    assert metadata.fields["title"].confidence == .9
    assert metadata.fields["authors"].confidence == .85


def test_layout_loader_orders_columns_filters_margins_and_tracks_blocks(tmp_path):
    path = generate_layout_demo_pdf(tmp_path / "layout-demo.pdf")

    document = PDFLoader().load(path)
    summary = PDFLoader.summarize_pages(document.pages, "auto")
    first = document.pages[0]

    assert summary == {
        "layout_version": "pymupdf-layout-v1",
        "mode": "auto",
        "total_pages": 3,
        "layout_pages": 3,
        "legacy_pages": 0,
        "fallback_pages": 0,
        "empty_pages": 0,
        "single_column_pages": 0,
        "double_column_pages": 3,
        "blocks_retained": 18,
        "header_footer_blocks_removed": 9,
        "dehyphenations": 3,
    }
    assert first.text.index("A Deterministic Layout-Aware") < first.text.index("Left column")
    assert first.text.index("Left column") < first.text.index("Right column")
    assert first.text.index("Right column") < first.text.index("Figure 1")
    assert "Proceedings of the Deterministic" not in first.text
    assert "Demonstration Copy" not in first.text
    assert "Similar Article Notes remain body content" in first.text
    assert "transformation rejoins" in first.text
    assert "transfor-" not in first.text
    assert "well-known" in first.text
    assert {block.block_type for block in first.blocks} >= {"body", "heading", "caption"}
    for order, block in enumerate(first.blocks):
        assert block.reading_order == order
        assert block.bbox[2] >= block.bbox[0]
        assert first.text[block.char_start:block.char_end] == block.text

    second_load = PDFLoader().load(path)
    assert [block.block_id for block in first.blocks] == [
        block.block_id for block in second_load.pages[0].blocks
    ]


def test_layout_loader_keeps_single_column_order_and_empty_pages(tmp_path):
    path = tmp_path / "single-column.pdf"
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((72, 120), "First single-column paragraph.", fontsize=10)
    page.insert_text((72, 220), "Second single-column paragraph.", fontsize=10)
    pdf.new_page(width=612, height=792)
    pdf.save(path)
    pdf.close()

    pages = PDFLoader().load(path).pages

    assert pages[0].text.index("First") < pages[0].text.index("Second")
    assert pages[0].layout_metadata["column_layout"] == "single_column"
    assert pages[1].text == ""
    assert pages[1].extraction_method == "empty"


def test_layout_failure_falls_back_for_only_the_damaged_page(tmp_path, monkeypatch):
    path = tmp_path / "fallback.pdf"
    pdf = pymupdf.open()
    first = pdf.new_page(width=612, height=792)
    first.insert_text((72, 120), "Healthy layout page.", fontsize=10)
    second = pdf.new_page(width=612, height=792)
    second.insert_text((72, 120), "CORRUPT layout page.", fontsize=10)
    pdf.save(path)
    pdf.close()
    loader = PDFLoader()
    parse = loader._parse_layout_block

    def fail_corrupt_block(raw_block, source_index):
        text = "".join(
            str(span.get("text", ""))
            for line in raw_block.get("lines", [])
            for span in line.get("spans", [])
        )
        if "CORRUPT" in text:
            raise ValueError("damaged block")
        return parse(raw_block, source_index)

    monkeypatch.setattr(loader, "_parse_layout_block", fail_corrupt_block)

    pages = loader.load(path).pages

    assert pages[0].extraction_method == "layout"
    assert pages[1].extraction_method == "legacy_fallback"
    assert pages[1].text == "CORRUPT layout page."
    assert pages[1].layout_metadata["fallback_reason"] == "ValueError"


def test_legacy_layout_mode_preserves_plain_text_extraction(tmp_path):
    path = generate_layout_demo_pdf(tmp_path / "legacy.pdf")
    with pymupdf.open(path) as pdf:
        expected = pdf[0].get_text("text").strip()

    page = PDFLoader(layout_mode="legacy").load(path).pages[0]

    assert page.text == expected
    assert page.extraction_method == "legacy"
    assert page.blocks == []
    assert page.layout_metadata["layout_version"] == "legacy"


def test_line_joining_only_repairs_latin_lowercase_dehyphenation():
    assert PDFLoader._join_block_lines(["transfor-", "mation works"]) == (
        "transformation works", 1
    )
    assert PDFLoader._join_block_lines(["well-known", "method"]) == (
        "well-known method", 0
    )
    assert PDFLoader._join_block_lines(["中文-", "公式"]) == ("中文- 公式", 0)
    assert PDFLoader._join_block_lines(["x-", "Y = 1"]) == ("x- Y = 1", 0)
