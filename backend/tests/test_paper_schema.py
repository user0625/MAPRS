from backend.schemas.paper import (
    PaperChunk,
    PaperDocument,
    PaperInput,
    PaperMetadata,
    PaperPage,
)


def test_create_paper_input():
    paper_input = PaperInput(
        source_type="pdf",
        source_path="data/raw/example.pdf",
    )

    assert paper_input.source_type == "pdf"
    assert paper_input.source_path == "data/raw/example.pdf"


def test_create_paper_document():
    metadata = PaperMetadata(
        title="Example Paper",
        authors=["Alice", "Bob"],
        total_pages=2,
    )

    pages = [
        PaperPage(page_number=1, text="This is page one."),
        PaperPage(page_number=2, text="This is page two."),
    ]

    document = PaperDocument(metadata=metadata, pages=pages)

    assert document.metadata.title == "Example Paper"
    assert document.page_count() == 2
    assert "page one" in document.full_text()


def test_create_paper_chunk():
    chunk = PaperChunk(
        chunk_id="page_1_chunk_1",
        text="This is a test chunk.",
        page_start=1,
        page_end=1,
        char_start=0,
        char_end=21,
    )

    assert chunk.chunk_id == "page_1_chunk_1"
    assert chunk.page_start == 1