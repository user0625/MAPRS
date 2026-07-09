from pathlib import Path

import pytest

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