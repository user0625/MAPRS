from __future__ import annotations

from backend.schemas.agent_io import EvidenceBundle, EvidenceItem
from backend.schemas.paper import PaperChunk, PaperDocument
from backend.tools.embedder import BaseEmbedder, EmbeddingError
from backend.tools.vector_store import BaseVectorStore, VectorStoreError

class RetrieverError(Exception):
  """Raised when retrieval fails"""

class PaperRetriever:
  """
    Retrieve relevant paper chunks for a query.

    This class connects:
    - PaperChunk
    - Embedder
    - VectorStore
    - EvidenceBundle

    It does not parse PDFs, chunk documents, call LLMs, or write reports.
  """
  def __init__(self, embedder:BaseEmbedder, vector_store:BaseVectorStore) -> None:
    self.embedder = embedder
    self.vector_store = vector_store
    self.chunk_map:dict[str, PaperChunk] = {}
    self._is_built = False

  def build_index(self, chunks:list[PaperChunk]) -> None:
    """
      build retrieval index from paper chunks.

      Parameters
      -----------
      chunks:
          List of PaperChunk object.
    """

    if not chunks:
      raise RetrieverError("cannot build retriever index from empty chunks.")

    self._validate_unique_chunk_ids(chunks)
    self.chunk_map = {chunk.chunk_id: chunk for chunk in chunks}

    try:
      records = self.embedder.embed_chunks(chunks)
      self.vector_store.clear()
      self.vector_store.add(records)
    except (EmbeddingError, VectorStoreError) as exc:
      raise RetrieverError(
        f"Failed to build retriever index: {type(exc).__name__}: {exc}"
      ) from exc
    
    self._is_built=True
  

  def build_index_from_document(self, document:PaperDocument) -> None:
    """
      build retrieval index from a paperdocument
      the document must already have chunks
    """
    if not document.has_chunks():
      raise RetrieverError("Document has no chunks, Run chunker before retriever.")
    
    self.build_index(document.chunks)

  
  def retrieve(self, query:str, top_k:int=5,) -> EvidenceBundle:
    """retrieve relevant evidence for one query"""

    query = query.strip()
    if not query:
      raise RetrieverError("query cannot be empty.")
    
    self._ensure_built()

    try:
      query_vector = self.embedder.embed_query(query)
      search_results = self.vector_store.search(query_vector=query_vector, top_k=top_k)
    except (EmbeddingError, VectorStoreError) as exc:
      raise RetrieverError("failed to retrieve evidence") from exc
    
    evidence_items:list[EvidenceItem] = []

    for result in search_results:
      chunk = self.chunk_map.get(result.chunk_id)

      if chunk is None:
        raise RetrieverError(f"vector store returned unknown chunk_id: {result.chunk_id}")
      
      evidence_items.append(EvidenceItem.from_chunk(
        chunk=chunk,
        evidence_id=self._build_evidence_id(
          query_index=1,
          rank=result.rank,
        ),
        query=query,
        score=result.score
      ))
    
    return EvidenceBundle(query_list=[query], items=evidence_items, )
  

  def retrieve_many(self, queries:list[str], top_k:int=5) -> EvidenceBundle:
    """
      Retrieve relevant evidence for multiple queries,

      MVP behavior:
        - run retrieve for each query
        - concatenate all evidence items
        - do not deduplicate yet.
    """
    cleaned_queries = [query.strip() for query in queries if query.strip()]

    if not cleaned_queries:
      raise RetrieverError("queries cannot be empty")
    
    if top_k <= 0:
      raise ValueError("top_k must be positive")
    
    self._ensure_built()

    all_items:list[EvidenceItem] = []

    for query_index, query in enumerate(cleaned_queries, start=1):
      try:
        query_vector = self.embedder.embed_query(query)
        search_results = self.vector_store.search(query_vector=query_vector, top_k=top_k)
      except (EmbeddingError, VectorStoreError) as exc:
        raise RetrieverError(f"failed to retrieve evidence for query: {query}") from exc
      
      for result in search_results:
        chunk = self.chunk_map.get(result.chunk_id)
        if chunk is None:
          raise RetrieverError(f"vector store returned unknown chunk_id: {result.chunk_id}")
        
        all_items.append(EvidenceItem.from_chunk(
          chunk=chunk,
          evidence_id=self._build_evidence_id(query_index=query_index, rank=result.rank),
          query=query,
          score=result.score,
        ))
    
    return EvidenceBundle(query_list=cleaned_queries, items=all_items)


  def clear(self) -> None:
    """
      clear retriever state
    """
    self.chunk_map = {}
    self.vector_store.clear()
    self._is_built = False

  
  def is_built(self) -> bool:
    """return whether the retriever index has been built"""
    return self._is_built


  def _build_evidence_id(self, query_index:int, rank:int) -> str:
    """
      build stable evidence id,
      Example:
        ev_q001_r001
    """
    return f"ev_q{query_index:03d}_r{rank:03d}"

  
  def _ensure_built(self, ) -> None:
    if not self._is_built:
      raise RetrieverError("Retriever index has not been built")

  

  def _validate_unique_chunk_ids(self, chunks:list[PaperChunk]) -> None:
    chunk_ids = [chunk.chunk_id for chunk in chunks]
    if len(chunk_ids) != len(set(chunk_ids)):
      raise RetrieverError("Duplicate chunk_id found in chunk_ids")
