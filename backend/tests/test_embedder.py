import pytest

from backend.schemas.paper import PaperChunk
from backend.tools.embedder import EmbeddingError, EmbeddingRecord, MockEmbedder


def test_mock_embedder_creates_vector_with_expected_dimension():
    embedder = MockEmbedder(dimension=64)

    vector = embedder.embed_text("This is a test sentence.")

    assert isinstance(vector, list)
    assert len(vector) == 64
    assert all(isinstance(value, float) for value in vector)


def test_mock_embedder_is_deterministic():
    embedder = MockEmbedder(dimension=64)

    vector_1 = embedder.embed_text("same text")
    vector_2 = embedder.embed_text("same text")

    assert vector_1 == vector_2


def test_mock_embedder_returns_different_vectors_for_different_texts():
    embedder = MockEmbedder(dimension=64)

    vector_1 = embedder.embed_text("text A")
    vector_2 = embedder.embed_text("text B")

    assert vector_1 != vector_2


def test_mock_embedder_rejects_empty_text():
    embedder = MockEmbedder(dimension=64)

    with pytest.raises(EmbeddingError):
        embedder.embed_text("   ")


def test_embed_chunks_creates_embedding_records():
    chunks = [
        PaperChunk(
            chunk_id="paper_001_page_1_chunk_1",
            paper_id="paper_001",
            text="This paper proposes a multi-agent reading system.",
            page_start=1,
            page_end=1,
        ),
        PaperChunk(
            chunk_id="paper_001_page_2_chunk_1",
            paper_id="paper_001",
            text="The method contains Planner, Reader, Critic, and Writer agents.",
            page_start=2,
            page_end=2,
        ),
    ]

    embedder = MockEmbedder(dimension=32)
    records = embedder.embed_chunks(chunks)

    assert len(records) == 2
    assert isinstance(records[0], EmbeddingRecord)
    assert records[0].chunk_id == "paper_001_page_1_chunk_1"
    assert records[0].paper_id == "paper_001"
    assert records[0].dimension == 32
    assert len(records[0].vector) == 32
    assert records[0].metadata["page_start"] == 1


def test_embed_texts_returns_same_number_of_vectors_as_inputs():
    embedder = MockEmbedder(dimension=16)

    texts = ["first text", "second text", "third text"]
    vectors = embedder.embed_texts(texts)

    assert len(vectors) == len(texts)
    assert all(len(vector) == 16 for vector in vectors)