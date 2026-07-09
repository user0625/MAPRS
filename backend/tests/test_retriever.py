import pytest

from backend.schemas.paper import PaperChunk, PaperDocument, PaperMetadata, PaperPage
from backend.tools.embedder import MockEmbedder
from backend.tools.retriever import PaperRetriever, RetrieverError
from backend.tools.vector_store import NumpyVectorStore


def make_chunks() -> list[PaperChunk]:
    return [
        PaperChunk(
            chunk_id="paper_001_page_1_chunk_1",
            paper_id="paper_001",
            text="This paper proposes a multi-agent system for reading scientific papers.",
            page_start=1,
            page_end=1,
            section="Abstract",
        ),
        PaperChunk(
            chunk_id="paper_001_page_2_chunk_1",
            paper_id="paper_001",
            text="The method includes Planner, Reader, Critic, and Writer agents.",
            page_start=2,
            page_end=2,
            section="Method",
        ),
        PaperChunk(
            chunk_id="paper_001_page_3_chunk_1",
            paper_id="paper_001",
            text="Experiments evaluate the system on paper understanding tasks.",
            page_start=3,
            page_end=3,
            section="Experiments",
        ),
    ]


def test_retriever_build_index():
    chunks = make_chunks()

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    retriever.build_index(chunks)

    assert retriever.is_built() is True
    assert len(retriever.chunk_map) == 3


def test_retriever_rejects_empty_chunks():
    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    with pytest.raises(RetrieverError):
        retriever.build_index([])


def test_retriever_rejects_duplicate_chunk_ids():
    chunks = [
        PaperChunk(
            chunk_id="duplicate_chunk",
            paper_id="paper_001",
            text="First chunk.",
            page_start=1,
            page_end=1,
        ),
        PaperChunk(
            chunk_id="duplicate_chunk",
            paper_id="paper_001",
            text="Second chunk.",
            page_start=2,
            page_end=2,
        ),
    ]

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    with pytest.raises(RetrieverError):
        retriever.build_index(chunks)


def test_retriever_retrieve_returns_evidence_bundle():
    chunks = make_chunks()

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    retriever.build_index(chunks)

    bundle = retriever.retrieve(
        query="What is the method?",
        top_k=2,
    )

    assert bundle.query_list == ["What is the method?"]
    assert len(bundle.items) == 2

    first_item = bundle.items[0]

    assert first_item.evidence_id.startswith("ev_q001_r")
    assert first_item.chunk_id in {chunk.chunk_id for chunk in chunks}
    assert first_item.text
    assert first_item.score is not None
    assert first_item.page_start is not None


def test_retriever_retrieve_many_returns_combined_bundle():
    chunks = make_chunks()

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    retriever.build_index(chunks)

    bundle = retriever.retrieve_many(
        queries=[
            "What is the method?",
            "What are the experiments?",
        ],
        top_k=2,
    )

    assert bundle.query_list == [
        "What is the method?",
        "What are the experiments?",
    ]
    assert len(bundle.items) == 4
    assert bundle.items[0].evidence_id.startswith("ev_q001")
    assert bundle.items[2].evidence_id.startswith("ev_q002")


def test_retriever_rejects_query_before_build_index():
    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    with pytest.raises(RetrieverError):
        retriever.retrieve("What is the method?", top_k=1)


def test_retriever_rejects_empty_query():
    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    retriever.build_index(make_chunks())

    with pytest.raises(RetrieverError):
        retriever.retrieve("   ", top_k=1)


def test_retriever_build_index_from_document():
    chunks = make_chunks()

    document = PaperDocument(
        metadata=PaperMetadata(
            paper_id="paper_001",
            title="Example Paper",
            total_pages=3,
        ),
        pages=[
            PaperPage(page_number=1, text="Page one."),
            PaperPage(page_number=2, text="Page two."),
            PaperPage(page_number=3, text="Page three."),
        ],
        chunks=chunks,
    )

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    retriever.build_index_from_document(document)

    assert retriever.is_built() is True
    assert len(retriever.chunk_map) == 3


def test_retriever_clear():
    chunks = make_chunks()

    retriever = PaperRetriever(
        embedder=MockEmbedder(dimension=32),
        vector_store=NumpyVectorStore(),
    )

    retriever.build_index(chunks)

    assert retriever.is_built() is True

    retriever.clear()

    assert retriever.is_built() is False
    assert len(retriever.chunk_map) == 0