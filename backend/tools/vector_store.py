from __future__ import annotations

from abc import ABC, abstractmethod

import numpy as np
from pydantic import BaseModel, Field, field_validator

from backend.tools.embedder import EmbeddingRecord

class VectorStoreError(Exception):
  """raise when vector store operation fails"""


class VectorSearchResult(BaseModel):
  """
    Search result returned by vector store.

    it only contains vector-level retrieval information.
    the full chunk text should be retrievered later by retriever.py
  """
  chunk_id:str = Field(..., min_length=1)
  score:float
  rank:int = Field(..., ge=1)

  @field_validator("chunk_id")
  @classmethod
  def strip_chunk_id(cls, value:str) -> str:
    value = value.strip()
    if not value:
      raise ValueError("chunk id cannot be empty")
    return value
  

class BaseVectorStore(ABC):
  """
    Base interface for vectoe stores.
  """

  @abstractmethod
  def add(self, records:list[EmbeddingRecord]) -> None:
    """Add embedding records to the vector store"""
  
  @abstractmethod
  def search(self, query_vector:list[float], top_k:int=5) -> list[VectorSearchResult]:
    """Search similar vectors"""

  @abstractmethod
  def clear(self) -> None:
    """clear all vectors"""
  
  @abstractmethod
  def __len__(self) -> int:
    """return number of vectors in the store"""


class NumpyVectorStore(BaseVectorStore):
  """
    Simple in-memory vector store based on numpy cosine similarity

    This is designed for MVP and testing. It is not optimized for large-scale retrieval.
  """

  def __init__(self, normalize:bool=True) -> None:
    self.normalize = normalize
    self.chunk_ids:list[str] = []
    self.vectors:np.ndarray|None = None
    self.dimension:int|None = None
  
  def add(self, records:list[EmbeddingRecord]) -> None:
    """
      add embedding records to the store.
      all vectors must have the same dimension
    """
    if not records: 
      return
    
    new_chunk_ids = [record.chunk_id for record in records]
    new_vectors = np.asarray(
      [record.vector for record in records],
      dtype=np.float32
    )

    if new_vectors.ndim != 2:
      raise VectorStoreError("Excepted 2D vector array")
    
    new_dimension = new_vectors.shape[1]

    if self.dimension is None:
      self.dimension = new_dimension
    elif self.dimension != new_dimension:
      raise VectorStoreError(f"vector dimension mismatch:excepted {self.dimension}, got {new_dimension}")
    
    if self.normalize:
      new_vectors = self._normalize_matrix(new_vectors)

    if self.vectors is None:
      self.vectors = new_vectors
    else:
      self.vectors = np.vstack([self.vectors, new_vectors])
    
    self.chunk_ids.extend(new_chunk_ids)
  

  def search(self, query_vector:list[float], top_k:int=5,) -> list[VectorSearchResult]:
    """
      sreach top-k most similar vectors by cosine similarity.
    """
    if top_k<=0:
      raise ValueError("top_k must be positive")
    
    if self.vectors is None or not self.chunk_ids:
      raise VectorStoreError("vector store is empty")
    
    query = np.asarray(query_vector, dtype=np.float32)

    if query.ndim != 1:
      raise VectorStoreError("Query vector must be 1D")

    if self.dimension is None:
      raise VectorStoreError("vector store dimension is not initialized")
    
    if query.shape[0] != self.dimension:
      raise VectorStoreError(f"query dimension mismatch: expected {self.dimension}, got {query.shape[0]}")
    
    if self.normalize:
      query = self._normalize_vector(query)

    scores = self.vectors @ query

    top_k = min(top_k, len(self.chunk_ids))
    top_indices = np.argsort(scores)[::-1][:top_k]

    results:list[VectorSearchResult] = []

    for rank, index in enumerate(top_indices, start=1):
      results.append(
        VectorSearchResult(
          chunk_id=self.chunk_ids[int(index)],
          score=float(scores[int(index)]),
          rank=rank
        )
      )
    
    return results
  
  
  def clear(self) -> None:
    """clear all stored vectors"""
    self.chunk_ids = []
    self.vectors = None
    self.dimension = None


  def __len__(self) -> int:
    return len(self.chunk_ids)


  def _normalize_vector(self, vector:np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)

    if norm == 0:
      return vector
    return vector / norm


  def _normalize_matrix(self, matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms