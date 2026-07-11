from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pymupdf

from backend.schemas.paper import (
  MetadataCandidate, MetadataSource, PaperDocument, PaperMetadata, PaperPage, PaperSection,
  SourceType,
)

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
        self._enrich_metadata(pdf, pages, metadata, path)
        sections = self._extract_sections(pdf, pages)

      return PaperDocument(metadata=metadata, pages=pages, sections=sections)
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

    metadata = PaperMetadata(
      paper_id=self._generate_paper_id(path),
      title=title,
      authors=authors,
      source_type=SourceType.PDF,
      source_path=str(path),
      total_pages=pdf.page_count
    )
    if title and not self._is_placeholder(title, path):
      metadata.fields["title"] = {"value": title, "source": MetadataSource.PDF, "confidence": 0.9}
    else:
      metadata.title = None
    if authors:
      metadata.fields["authors"] = {"value": authors, "source": MetadataSource.PDF, "confidence": 0.85}
    return metadata

  def _enrich_metadata(self, pdf: pymupdf.Document, pages: list[PaperPage],
                       metadata: PaperMetadata, path: Path) -> None:
    first = pages[0].text if pages else ""
    blocks = pdf[0].get_text("dict").get("blocks", []) if pdf.page_count else []
    lines: list[tuple[str, float, float]] = []
    for block in blocks:
      for line in block.get("lines", []):
        spans = line.get("spans", [])
        text = " ".join(str(s.get("text", "")).strip() for s in spans).strip()
        direction = line.get("dir", (1.0, 0.0))
        # Rotated arXiv stamps and sidebars often use a larger font than the title.
        if text:
          bbox = tuple(float(value) for value in line.get("bbox", [0, 0, 0, 0]))
          rotation = 0 if abs(float(direction[0]) - 1.0) < .05 else 90
          if bbox[1] < pdf[0].rect.height * .62:
            metadata.candidates.append(MetadataCandidate(
              text=text, page=1, bbox=bbox,
              font_size=max((float(s.get("size", 0)) for s in spans), default=0),
              rotation=rotation, kind="first_page_block"))
        if text and abs(float(direction[0]) - 1.0) < .05 and abs(float(direction[1])) < .05:
          lines.append((text, max((float(s.get("size", 0)) for s in spans), default=0),
                        float(line.get("bbox", [0, 0, 0, 0])[1])))
    candidates = [item for item in lines if len(item[0]) >= 8 and item[2] < pdf[0].rect.height * .55]
    if candidates:
      max_size = max(item[1] for item in candidates)
      largest = sorted((item for item in candidates if item[1] >= max_size * .92),
                       key=lambda item: item[2])
      title_group: list[tuple[str, float, float]] = []
      for item in largest:
        if not title_group or item[2] - title_group[-1][2] <= max_size * 1.8:
          title_group.append(item)
        elif title_group:
          break
      title_lines = [item[0] for item in title_group[:3]]
      title = " ".join(title_lines)
      if not self._is_placeholder(title, path):
        metadata.set_field("title", title, MetadataSource.LAYOUT, .82)
      title_end_y = max((item[2] for item in title_group), default=0)
      after_title = [item[0] for item in sorted(candidates, key=lambda value: value[2])
                     if item[2] > title_end_y and item[1] < max_size]
      if not metadata.authors and after_title:
        author_line = next((x for x in after_title if self._looks_like_authors(x)), None)
        if author_line:
          metadata.set_field("authors", self._parse_authors(author_line), MetadataSource.LAYOUT, .72)

    abstract_match = re.search(
      r"(?is)(?:^|\n)\s*(?:abstract|摘要)\s*[:—-]?\s*(.{80,}?)"
      r"(?=\n\s*(?:keywords?|关键词|index terms|1\.?\s+introduction|一[、.]?\s*引言|引言)\b)",
      "\n" + first,
    )
    if abstract_match:
      metadata.set_field("abstract", " ".join(abstract_match.group(1).split()), MetadataSource.TEXT, .88)
    joined = "\n".join(page.text for page in pages[:3])
    doi = re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", joined, re.I)
    arxiv = re.search(r"(?:arXiv\s*:\s*)?(\d{4}\.\d{4,5}(?:v\d+)?)", joined, re.I)
    year = re.search(r"\b((?:19|20)\d{2})\b", joined[:6000])
    keywords = re.search(r"(?im)^\s*(?:keywords?|关键词)\s*[:—-]\s*(.+)$", joined)
    if doi:
      metadata.set_field("doi", doi.group(0).rstrip(".,"), MetadataSource.TEXT, .96)
    if arxiv:
      metadata.set_field("arxiv_id", arxiv.group(1), MetadataSource.TEXT, .95)
    if year:
      metadata.set_field("year", int(year.group(1)), MetadataSource.TEXT, .62)
    if keywords:
      values = [x.strip() for x in re.split(r"[,;，；]", keywords.group(1)) if x.strip()]
      metadata.set_field("keywords", values[:20], MetadataSource.TEXT, .8)
    language = "zh" if len(re.findall(r"[\u4e00-\u9fff]", first)) > max(10, len(first) * .05) else "en"
    metadata.set_field("language", language, MetadataSource.TEXT, .85)

  def _extract_sections(self, pdf: pymupdf.Document, pages: list[PaperPage]) -> list[PaperSection]:
    candidates: list[tuple[str, int]] = []
    for item in pdf.get_toc(simple=True):
      if len(item) >= 3 and str(item[1]).strip() and int(item[2]) >= 1:
        candidates.append((str(item[1]).strip(), min(int(item[2]), len(pages))))
    if not candidates:
      heading = re.compile(
        r"(?im)^\s*((?:\d+(?:\.\d+)*|[一二三四五六七八九十]+)[.、]?\s+[^\n]{2,90}|"
        r"(?:abstract|摘要|introduction|引言|background|背景|method(?:ology)?|方法|"
        r"experiments?|实验|results?|结果|discussion|讨论|limitations?|局限|"
        r"conclusion|结论|references|参考文献))\s*$")
      for page in pages:
        for match in heading.finditer(page.text):
          name = " ".join(match.group(1).split())
          if not candidates or candidates[-1] != (name, page.page_number):
            candidates.append((name, page.page_number))
    sections: list[PaperSection] = []
    for index, (name, start) in enumerate(candidates):
      end = (candidates[index + 1][1] if index + 1 < len(candidates) else len(pages))
      if index + 1 < len(candidates) and end > start:
        end -= 1
      text = "\n\n".join(p.text for p in pages if start <= p.page_number <= end)
      sections.append(PaperSection(section_id=f"section_{index + 1}", name=name,
                                   page_start=start, page_end=max(start, end), text=text))
    return sections

  @staticmethod
  def _looks_like_authors(text: str) -> bool:
    lowered = text.lower()
    return (len(text) < 250 and not any(word in lowered for word in
            ("abstract", "university", "department", "arxiv", "keywords", "introduction"))
            and ("," in text or " and " in lowered or len(text.split()) in range(2, 9)))

  @staticmethod
  def _is_placeholder(value: str, path: Path) -> bool:
    compact = value.strip().lower()
    return (compact in {"unknown", "untitled", "none", "microsoft word", path.stem.lower()}
            or len(compact) < 4 or compact.startswith("arxiv:")
            or bool(re.fullmatch(r"(?:19|20)\d{2}[-_/]\d{1,2}[-_/]\d{1,2}", compact)))

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
    cleaned: list[str] = []
    for author in authors:
      author = re.sub(r"^and\s+", "", author.strip(), flags=re.I)
      author = re.sub(r"(?:\s*[\d*\u2020\u2021]+\s*)+$", "", author).strip()
      author = " ".join(author.split())
      if author:
        cleaned.append(author)
    return cleaned
  

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
  
