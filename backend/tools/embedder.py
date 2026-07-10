from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from typing import Any

import numpy as np
from pydantic import BaseModel, Field, field_validator

from backend.schemas.paper import PaperChunk


class EmbeddingError(Exception):
  """Raise when embedding generation fails"""


class EmbeddingRecord(BaseModel):
  """
    Embedding result for one chunk.

    the vector is stored as list[float] rather than numpy.ndarray so that it can be serialized to JSON if needed.
  """

  chunk_id:str = Field(..., min_length=1)
  paper_id:str|None = None
  vector:list[float] = Field(..., min_length=1)
  model:str = Field(..., min_length=1)
  dimension:int = Field(..., ge=1)
  metadata:dict[str, Any] = Field(default_factory=dict)

  @field_validator("chunk_id", "model")
  @classmethod
  def strip_required_text(cls, value:str) -> str:
    value = value.strip()
    if not value:
      raise ValueError("Field cannot be empty")
    return value
  
  @field_validator("paper_id")
  @classmethod
  def strip_optional_text(cls, value:str|None) -> str|None:
    if value is None:
      return None
    value = value.strip()
    return value or None
  


class BaseEmbedder(ABC):
  """
    Base interface for all embedders.

    concrete implementations can be mock, openai-compatible, local model, etc.
  """

  model_name:str

  @abstractmethod
  def embed_text(self, text:str) -> list[float]:
    """embed a single text string"""

  def embed_texts(self, texts:list[str]) -> list[list[float]]:
    """
      Embed multiple text strings.

      default implementation calls embed_text one by one.
      subclasses can override this method for efficient batch api calls.
    """
    return [self.embed_text(text) for text in texts]

  def embed_query(self, query:str) -> list[float]:
    """
      embed a retrieval query

      by default, query embedding is the same as text embedding.
    """
    return self.embed_text(query)
  
  def embed_chunks(self, chunks:list[PaperChunk]) -> list[EmbeddingRecord]:
    """
      Embed a list of PaperChunk objects.
    """
    if not chunks:
      return []
    
    texts = [chunk.text for chunk in chunks]
    vectors = self.embed_texts(texts)

    if len(vectors) != len(chunks):
      raise EmbeddingError(f"Embedding count mismatch: got {len(vectors)} vectors for {len(chunks)} chunks")
    
    records:list[EmbeddingRecord] = []

    for chunk, vector in zip(chunks, vectors):
      records.append(EmbeddingRecord(
        chunk_id=chunk.chunk_id,
        paper_id=chunk.paper_id,
        vector=vector,
        model=self.model_name,
        dimension=len(vector),
        metadata={
          "page_start": chunk.page_start,
          "page_end": chunk.page_end,
          "section": chunk.section
        }
      ))

    return records
    

class MockEmbedder(BaseEmbedder):
  """
    Deterministic mock embedder for local development and text.

    this embedder does not capture real semantic meaning. it only produces stable pseudo-random vectors from input text.
  """
  def __init__(self, dimension:int=128, model_name:str="mock-embedding", normalize: bool=True) -> None:
    if dimension <= 0:
      raise ValueError("dimension must be positive")
    
    self.dimension = dimension
    self.model_name = model_name
    self.normalize = normalize

  def embed_text(self, text:str) -> list[float]:
    text = text.strip()

    if not text:
      raise EmbeddingError("cannot embed empty text.")
    
    seed = self._text_to_seed(text)
    rng = np.random.default_rng(seed)

    vector = rng.normal(size=self.dimension).astype(np.float32)

    if self.normalize:
      vector = self._normalize_vector(vector)

    return vector.tolist()
  

  def _text_to_seed(self, text:str) -> int:
    """
      convert text into a deterministic random seed.

      do not use python's built-in hash(), because it is randomized between processes by default.
    """
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=False) % (2**32)
  
  def _normalize_vector(self, vector:np.ndarray) -> np.ndarray:
    norm = np.linalg.norm(vector)

    if norm == 0:
      return vector
    return vector / norm
  

class OpenAICompatibleEmbedder(BaseEmbedder):
  """
    Embedder for openai-compatible embedding apis.

    it can be used with openai, qwen-compatible apis, or other compatible services as long as they support the embedding endpoint.
  """
  #text-embedding-v4 每次最多接受 10 条输入。
  def __init__(self, api_key:str, model_name:str, base_url:str|None=None, batch_size:int=8) -> None:
    if not api_key.strip():
      raise ValueError("api_key cannot be empty")
    
    if not model_name.strip():
      raise ValueError("model_name cannot be empty")

    if batch_size <= 0:
      raise ValueError("batch_size must be positive")
    
    try:
      from openai import OpenAI
    except ImportError as exc:
      raise EmbeddingError("openai package is required for OpenaiCompatibleEmbedder.Install it with pip install openai") from exc
    
    self.model_name = model_name
    self.batch_size = batch_size
    self.client = OpenAI(api_key=api_key, base_url=base_url)
  
  def embed_text(self, text:str) -> list[float]:
    vectors = self.embed_texts([text])
    return vectors[0]
  
  def embed_texts(self, texts:list[str]) -> list[list[float]]:
    cleaned_texts = [text.strip() for text in texts]

    if any(not text for text in cleaned_texts):
      raise EmbeddingError("cannot embed empty text")
    
    all_vectors: list[list[float]] = []

    for start in range(0, len(cleaned_texts), self.batch_size):
      batch = cleaned_texts[start:start+self.batch_size]

      try:
        response = self.client.embeddings.create(
          model=self.model_name,
          input=batch
        )
      except Exception as exc:
        raise EmbeddingError(
          f"failed to call embedding api: {type(exc).__name__}: {exc}"
        ) from exc
      
      batch_vectors = [item.embedding for item in response.data]
      all_vectors.extend(batch_vectors)
    
    return all_vectors


