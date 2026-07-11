from __future__ import annotations

from dataclasses import dataclass

from backend.schemas.paper import PaperChunk, PaperDocument, PaperPage

class ChunkingError(Exception):
  """Raise when document chunking fails"""

@dataclass
class ChunkingConfig:
  """configuration for text chunking"""

  chunk_size: int = 1200
  chunk_overlap: int = 150

  def __post_init__(self) -> None:
    if self.chunk_size <= 0:
      raise ValueError("chunk_size must be positive")

    if self.chunk_overlap < 0:
      raise ValueError("chunk_overlap must be non-negative")

    if self.chunk_overlap >= self.chunk_size:
      raise ValueError("chunk_overlap must be smaller than chunk_size")


class DocumentChunker:
  """
    split a paperdocument into paperchunk object.

    this class only performs text chunking. it does not create embedding, build vector indexes, or call llms.
  """

  def __init__(self, chunk_size:int = 1200, chunk_overlap:int = 150,) -> None:
    self.config = ChunkingConfig(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

  def chunk(self, document:PaperDocument) -> PaperDocument:
    """
      chunk a PaperDocument and return the same document with chunks filled.

      Parameters:
      ------------
      document:
          parsed paper document with paper-level text.
      Returns
      --------
      PaperDocument
          Document with generated chunks.
    """

    if not document.pages:
      raise ChunkingError("cannot chunk document without pages")
    
    chunks:list[PaperChunk] = []
    for page in document.pages:
      section = next((s.name for s in document.sections
                      if s.page_start and s.page_end and s.page_start <= page.page_number <= s.page_end), None)
      page_chunks = self._chunk_page(page=page, paper_id=document.metadata.paper_id, section=section)
      chunks.extend(page_chunks)
    
    document.chunks = chunks
    return document
  
  def _chunk_page(self, page:PaperPage, paper_id:str|None = None,
                  section: str | None = None) -> list[PaperChunk]:
    """split one page into chunks"""

    text = self._normalize_text(page.text)

    if not text:
      return []
    
    spans = self._split_text(text)
    chunks:list[PaperChunk] = []

    for chunk_index, (char_start, char_end, chunk_text) in enumerate(spans, start=1):
      chunk_id = self._build_chunk_id(paper_id=paper_id, page_number=page.page_number, chunk_index=chunk_index)
      chunks.append(
        PaperChunk(
          chunk_id=chunk_id,
          paper_id=paper_id,
          text=chunk_text,
          page_start=page.page_number,
          page_end=page.page_number,
          section=section,
          char_start=char_start,
          char_end=char_end
        )
      )

    return chunks

  
  def _split_text(self, text:str) -> list[tuple[int,int,str]]:
    """
    split text into overlapping chunks.

    returns
    ----------
    list[tuple[int,int,str]]
        each tuple contains char_start, char_end, and chunk_text
    """
    chunk_size = self.config.chunk_size
    chunk_overlap = self.config.chunk_overlap

    if len(text) <= chunk_size:
      return [(0,len(text), text)]
    
    spans:list[tuple[int,int,str]] = []

    start = 0

    while start < len(text):
      end = min(start + chunk_size, len(text))
      chunk_text = text[start:end].strip()

      if chunk_text:
        spans.append((start, end, chunk_text))
      
      if end >= len(text):
        break

      start = end - chunk_overlap
    
    return spans
  

  def _build_chunk_id(self, paper_id:str|None, page_number:int, chunk_index:int) -> str:
    """
      build a stable chunk id.

      example:
        paper_abed1234_page_1_chunk_1
    """

    prefix = paper_id or "paper_unknown"
    return f"{prefix}_page_{page_number}_chunk_{chunk_index}"
  
  
  def _normalize_text(self, text:str) -> str:
    """
      lightly normalize page text.

      avoid aggressive cleaning beacuse scientific text structure may matter.
    """

    return text.replace("\x00", "").strip()
