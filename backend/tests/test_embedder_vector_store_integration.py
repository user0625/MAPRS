from backend.schemas.paper import PaperChunk
from backend.tools.embedder import MockEmbedder
from backend.tools.vector_store import NumpyVectorStore


def test_embedder_and_vector_store_integration():
    chunks = [
        PaperChunk(
            chunk_id="chunk_1",
            paper_id="paper_001",
            text="This paper proposes a multi-agent system for reading papers.",
            page_start=1,
            page_end=1,
        ),
        PaperChunk(
            chunk_id="chunk_2",
            paper_id="paper_001",
            text="The experiment evaluates the model on multiple datasets.",
            page_start=2,
            page_end=2,
        ),
    ]

    embedder = MockEmbedder(dimension=32)
    records = embedder.embed_chunks(chunks)

    store = NumpyVectorStore()
    store.add(records)

    query_vector = embedder.embed_query("multi-agent paper reading")
    results = store.search(query_vector, top_k=1)

    assert len(results) == 1
    assert results[0].chunk_id in {"chunk_1", "chunk_2"}
    assert results[0].rank == 1