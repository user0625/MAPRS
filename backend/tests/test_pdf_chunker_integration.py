from pathlib import Path

import pytest

from backend.tools.chunker import DocumentChunker
from backend.tools.pdf_loader import PDFLoader


@pytest.mark.skipif(
    not Path("backend/data/raw/example.pdf").exists(),
    reason="Test PDF does not exist.",
)
def test_pdf_loader_and_chunker_integration():
    loader = PDFLoader()
    chunker = DocumentChunker(chunk_size=1200, chunk_overlap=150)

    document = loader.load("backend/data/raw/example.pdf")
    document = chunker.chunk(document)

    assert document.metadata.total_pages is not None
    assert len(document.pages) > 0
    assert len(document.chunks) > 0
    assert document.chunks[0].text