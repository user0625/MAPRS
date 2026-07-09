from pathlib import Path

import pytest

from backend.tools.chunker import DocumentChunker
from backend.tools.embedder import MockEmbedder
from backend.tools.pdf_loader import PDFLoader
from backend.tools.retriever import PaperRetriever
from backend.tools.vector_store import NumpyVectorStore


@pytest.mark.skipif(
    not Path("backend/data/raw/example.pdf").exists(),
    reason="Test PDF does not exist.",
)
def test_pdf_to_retriever_integration():
    loader = PDFLoader()
    chunker = DocumentChunker(chunk_size=1200, chunk_overlap=150)

    document = loader.load("backend/data/raw/example.pdf")
    document = chunker.chunk(document)

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=64),
        vector_store=NumpyVectorStore(),
    )

    retriever.build_index_from_document(document)

    bundle = retriever.retrieve(
        query="What is the main contribution of this paper?",
        top_k=3,
    )

    assert len(bundle.items) > 0
    assert bundle.items[0].text
    assert bundle.items[0].chunk_id
    assert bundle.items[0].score is not None