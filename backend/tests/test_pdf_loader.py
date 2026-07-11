from pathlib import Path

import pytest
import pymupdf

from backend.tools.pdf_loader import PDFLoader
from backend.schemas.paper import PaperDocument


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
