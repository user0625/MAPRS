from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pymupdf

from backend.schemas.paper import PaperDocument, PaperMetadata, PaperPage, SourceType

class PDFLoadError(Exception):
  """Raise when pdf loading fails"""

class PDFLoader:
  """
    load a local pdf file and convert it into a PaperDocument.

    This class only handles pdf parsing. It does not perform chunking,
    embedding, retrieval, or LLM-based analysis.
  """

  def load(self, pdf_path:str|Path) -> PaperDocument:
    """
      locad pdf file and return a PaperDocument

      Parameters
      ----------
      pdf_path:
          Local path to the PDF file.
      Returns
      ----------
      PaperDocument
          Parsed document with metadata and page-level text.
    """
    path = self._validate_pdf_path(pdf_path)

    try:
      with pymupdf.open(path) as pdf:
        metadata =self._extract_metadata(pdf, path)
        pages = self._extract_pages(pdf)

      return PaperDocument(metadata=metadata, pages=pages)
    except Exception as exc:
      raise PDFLoadError(f"Failed to load pdf: {path}") from exc
    
  def _validate_pdf_path(self, pdf_path:str|Path) -> Path:
    """Validate PDF path"""
    path = Path(pdf_path)

    if not path.exists():
      raise FileNotFoundError(f"PDF file not exist: {path}")
    
    if not path.is_file():
      raise ValueError(f"PDF path is not a file: {path}")
    
    if path.suffix.lower() != ".pdf":
      raise ValueError(f"Expected a .pdf file, got: {path}")
    
    return path
  
  def _extract_metadata(self, pdf:pymupdf.Document, path:Path) -> PaperMetadata:
    """
      Extract basic PDF metadata
      PDF metadata is often incomplete or noisy, so every field should be optional.
    """

    raw_metadata:dict[str, Any] = pdf.metadata or {}

    title = self._clean_optional_text(raw_metadata.get("title"))
    author_text = self._clean_optional_text(raw_metadata.get("author"))

    authors = self._parse_authors(author_text)

    return PaperMetadata(
      paper_id=self._generate_paper_id(path),
      title=title,
      authors=authors,
      source_type=SourceType.PDF,
      source_path=str(path),
      total_pages=pdf.page_count
    )

  def _extract_pages(self, pdf:pymupdf.Document) -> list[PaperPage]:
    """
      Extract text from all pages.
      page numbers are 1-based.
    """

    pages:list[PaperPage] = []

    for page_index in range(pdf.page_count):
      page = pdf.load_page(page_index)
      text = page.get_text("text")
      text = self._normalize_page_text(text)

      pages.append(
        PaperPage(
          page_number=page_index + 1,
          text=text
        )
      )
    return pages
  
  def _generate_paper_id(self, path:Path) -> str:
    """
      generate a stable paper id from file path and file content.
      the content hash makes the ID more stable than just using filename.
    """

    hasher = hashlib.sha256()
    with path.open("rb") as file:
      while chunk := file.read(1024*1024):
        hasher.update(chunk)
    
    digest = hasher.hexdigest()[:16]
    return f"paper_{digest}"


  def _parse_authors(self, author_text:str|None) -> list[str]:
    """
      prase author string from pdf metadata.
      pdf metadata author fields are inconsistent. this is only a lightweight parser
    """

    if not author_text:
      return []
    
    seperators = [";", ",", " and "]

    authors = [author_text]

    for sep in seperators:
      if sep in author_text:
        authors = author_text.split(sep)
        break
    return [author.strip() for author in authors if author.strip()]
  

  def _clean_optional_text(self, value:Any) -> str|None:
    """clean optional text metadata"""

    if value is None:
      return None
    text = str(value).strip()
    return text or None
  

  def _normalize_page_text(self, text:str) -> str:
    """
      normalize extracted page text lightly
      avoid aggressive cleaning here because later chunking may need original text structure.
    """

    text = text.replace("\x00", "")
    text = text.strip()

    return text
  

