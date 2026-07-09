import pytest

from backend.tools.embedder import EmbeddingRecord
from backend.tools.vector_store import NumpyVectorStore, VectorStoreError


def make_record(chunk_id: str, vector: list[float]) -> EmbeddingRecord:
    return EmbeddingRecord(
        chunk_id=chunk_id,
        paper_id="paper_001",
        vector=vector,
        model="mock-embedding",
        dimension=len(vector),
    )


def test_vector_store_add_records():
    store = NumpyVectorStore()

    records = [
        make_record("chunk_1", [1.0, 0.0, 0.0]),
        make_record("chunk_2", [0.0, 1.0, 0.0]),
    ]

    store.add(records)

    assert len(store) == 2
    assert store.dimension == 3


def test_vector_store_search_returns_top_k():
    store = NumpyVectorStore()

    records = [
        make_record("chunk_1", [1.0, 0.0, 0.0]),
        make_record("chunk_2", [0.0, 1.0, 0.0]),
        make_record("chunk_3", [0.0, 0.0, 1.0]),
    ]

    store.add(records)

    results = store.search([1.0, 0.0, 0.0], top_k=2)

    assert len(results) == 2
    assert results[0].chunk_id == "chunk_1"
    assert results[0].rank == 1
    assert results[0].score > results[1].score


def test_vector_store_rejects_dimension_mismatch_on_add():
    store = NumpyVectorStore()

    store.add([make_record("chunk_1", [1.0, 0.0, 0.0])])

    with pytest.raises(VectorStoreError):
        store.add([make_record("chunk_2", [1.0, 0.0])])


def test_vector_store_rejects_dimension_mismatch_on_search():
    store = NumpyVectorStore()

    store.add([make_record("chunk_1", [1.0, 0.0, 0.0])])

    with pytest.raises(VectorStoreError):
        store.search([1.0, 0.0], top_k=1)


def test_vector_store_rejects_empty_search():
    store = NumpyVectorStore()

    with pytest.raises(VectorStoreError):
        store.search([1.0, 0.0, 0.0], top_k=1)


def test_vector_store_clear():
    store = NumpyVectorStore()

    store.add([
        make_record("chunk_1", [1.0, 0.0]),
        make_record("chunk_2", [0.0, 1.0]),
    ])

    assert len(store) == 2

    store.clear()

    assert len(store) == 0
    assert store.dimension is None