import pytest

from backend.schemas.paper import PaperDocument, PaperMetadata, PaperPage, PaperTextBlock
from backend.tools.chunker import ChunkingConfig, ChunkingError, DocumentChunker


def test_chunking_config_rejects_invalid_overlap():
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=100, chunk_overlap=100)


def test_chunker_rejects_document_without_pages():
    document = PaperDocument(
        metadata=PaperMetadata(paper_id="paper_001"),
        pages=[],
    )

    chunker = DocumentChunker(chunk_size=100, chunk_overlap=10)

    with pytest.raises(ChunkingError):
        chunker.chunk(document)


def test_chunker_creates_single_chunk_for_short_page():
    document = PaperDocument(
        metadata=PaperMetadata(paper_id="paper_001"),
        pages=[
            PaperPage(
                page_number=1,
                text="This is a short page.",
            )
        ],
    )

    chunker = DocumentChunker(chunk_size=100, chunk_overlap=10)
    document = chunker.chunk(document)

    assert len(document.chunks) == 1
    assert document.chunks[0].chunk_id == "paper_001_page_1_chunk_1"
    assert document.chunks[0].page_start == 1
    assert document.chunks[0].page_end == 1
    assert document.chunks[0].text == "This is a short page."


def test_chunker_creates_multiple_chunks_for_long_page():
    text = "a" * 250

    document = PaperDocument(
        metadata=PaperMetadata(paper_id="paper_001"),
        pages=[
            PaperPage(
                page_number=1,
                text=text,
            )
        ],
    )

    chunker = DocumentChunker(chunk_size=100, chunk_overlap=20)
    document = chunker.chunk(document)

    assert len(document.chunks) == 3

    first_chunk = document.chunks[0]
    second_chunk = document.chunks[1]

    assert first_chunk.char_start == 0
    assert first_chunk.char_end == 100

    assert second_chunk.char_start == 80
    assert second_chunk.char_end == 180


def test_chunker_skips_empty_pages():
    document = PaperDocument(
        metadata=PaperMetadata(paper_id="paper_001"),
        pages=[
            PaperPage(page_number=1, text=""),
            PaperPage(page_number=2, text="This page has content."),
        ],
    )

    chunker = DocumentChunker(chunk_size=100, chunk_overlap=10)
    document = chunker.chunk(document)

    assert len(document.chunks) == 1
    assert document.chunks[0].page_start == 2


def test_document_has_chunks_after_chunking():
    document = PaperDocument(
        metadata=PaperMetadata(paper_id="paper_001"),
        pages=[
            PaperPage(page_number=1, text="This is page one."),
            PaperPage(page_number=2, text="This is page two."),
        ],
    )

    chunker = DocumentChunker(chunk_size=100, chunk_overlap=10)
    document = chunker.chunk(document)

    assert document.has_chunks() is True


def test_chunk_metadata_maps_intersecting_layout_blocks_without_duplicates():
    page = PaperPage(
        page_number=1,
        text="Heading\n\nBody text",
        blocks=[
            PaperTextBlock(
                block_id="page_0001_block_0000", page_number=1,
                bbox=(10, 10, 100, 20), reading_order=0, block_type="heading",
                char_start=0, char_end=7, text="Heading",
            ),
            PaperTextBlock(
                block_id="page_0001_block_0001", page_number=1,
                bbox=(10, 30, 100, 50), reading_order=1, block_type="body",
                char_start=9, char_end=18, text="Body text",
            ),
        ],
        extraction_method="layout",
        layout_metadata={"layout_version": "pymupdf-layout-v1"},
    )
    document = PaperDocument(metadata=PaperMetadata(paper_id="paper_001"), pages=[page])

    chunk = DocumentChunker(chunk_size=100, chunk_overlap=10).chunk(document).chunks[0]

    assert chunk.metadata == {
        "layout_block_ids": ["page_0001_block_0000", "page_0001_block_0001"],
        "layout_block_types": ["heading", "body"],
        "layout_version": "pymupdf-layout-v1",
    }
