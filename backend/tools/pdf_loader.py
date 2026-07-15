from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

import pymupdf

from backend.schemas.paper import (
  MetadataCandidate, MetadataSource, PaperDocument, PaperMetadata, PaperPage, PaperSection,
  PaperTextBlock, SourceType,
)


LAYOUT_VERSION = "pymupdf-layout-v1"


@dataclass
class _LayoutBlock:
  source_index: int
  bbox: tuple[float, float, float, float]
  text: str
  font_sizes: list[float] = field(default_factory=list)
  rotation: int = 0
  dehyphenations: int = 0


@dataclass
class _PageDraft:
  page_number: int
  width: float
  height: float
  legacy_text: str
  blocks: list[_LayoutBlock] = field(default_factory=list)
  rotated_blocks_ignored: int = 0
  extraction_error: str | None = None

class PDFLoadError(Exception):
  """Raise when pdf loading fails"""

class PDFLoader:
  """
    load a local pdf file and convert it into a PaperDocument.

    This class only handles pdf parsing. It does not perform chunking,
    embedding, retrieval, or LLM-based analysis.
  """

  def __init__(self, layout_mode: str = "auto") -> None:
    if layout_mode not in {"auto", "legacy"}:
      raise ValueError("layout_mode must be 'auto' or 'legacy'")
    self.layout_mode = layout_mode

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
      metadata.set_field("title", title, MetadataSource.PDF, .9, force=True)
    else:
      metadata.title = None
    if authors:
      metadata.set_field("authors", authors, MetadataSource.PDF, .85, force=True)
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
    """Extract pages in legacy mode or with deterministic layout analysis."""
    if self.layout_mode == "legacy":
      return self._extract_legacy_pages(pdf)

    drafts = [self._extract_page_draft(pdf, index) for index in range(pdf.page_count)]
    repeated = self._repeated_margin_signatures(drafts)
    retained = [
      block
      for draft in drafts
      for block in draft.blocks
      if self._margin_signature(block, draft) not in repeated
    ]
    body_font_size = self._body_font_median(retained)
    return [self._finalize_layout_page(draft, repeated, body_font_size) for draft in drafts]

  def _extract_legacy_pages(self, pdf: pymupdf.Document) -> list[PaperPage]:
    pages: list[PaperPage] = []
    for page_index in range(pdf.page_count):
      page = pdf.load_page(page_index)
      text = self._normalize_page_text(page.get_text("text"))
      pages.append(PaperPage(
        page_number=page_index + 1,
        text=text,
        width=float(page.rect.width),
        height=float(page.rect.height),
        extraction_method="legacy" if text else "empty",
        layout_metadata={
          "layout_version": "legacy",
          "column_layout": "unknown" if text else "empty",
          "header_footer_blocks_removed": 0,
          "dehyphenations": 0,
        },
      ))
    return pages

  def _extract_page_draft(self, pdf: pymupdf.Document, page_index: int) -> _PageDraft:
    page = pdf.load_page(page_index)
    width = float(page.rect.width)
    height = float(page.rect.height)
    try:
      legacy_text = self._normalize_page_text(page.get_text("text"))
    except Exception:
      legacy_text = ""
    draft = _PageDraft(
      page_number=page_index + 1,
      width=width,
      height=height,
      legacy_text=legacy_text,
    )
    try:
      raw = page.get_text("dict")
      raw_blocks = raw.get("blocks")
      if not isinstance(raw_blocks, list):
        raise ValueError("invalid text block data")
      for source_index, raw_block in enumerate(raw_blocks):
        parsed, rotated = self._parse_layout_block(raw_block, source_index)
        draft.rotated_blocks_ignored += rotated
        if parsed is not None:
          draft.blocks.append(parsed)
      if not draft.blocks and legacy_text:
        draft.extraction_error = "no_valid_horizontal_text_blocks"
    except Exception as exc:
      draft.blocks = []
      draft.extraction_error = type(exc).__name__
    return draft

  def _parse_layout_block(
    self, raw_block: dict[str, Any], source_index: int,
  ) -> tuple[_LayoutBlock | None, int]:
    if raw_block.get("type", 0) != 0:
      return None, 0
    raw_lines = raw_block.get("lines", [])
    if not isinstance(raw_lines, list):
      raise ValueError("invalid text line data")
    lines: list[str] = []
    line_boxes: list[tuple[float, float, float, float]] = []
    font_sizes: list[float] = []
    rotations: list[int] = []
    rotated_ignored = 0
    for line in raw_lines:
      direction = line.get("dir", (1.0, 0.0))
      if not isinstance(direction, (list, tuple)) or len(direction) < 2:
        raise ValueError("invalid text direction")
      dx, dy = float(direction[0]), float(direction[1])
      if abs(dx - 1.0) >= .05 or abs(dy) >= .05:
        rotated_ignored += 1
        continue
      spans = line.get("spans", [])
      if not isinstance(spans, list):
        raise ValueError("invalid text span data")
      line_text = "".join(str(span.get("text", "")) for span in spans).strip()
      if not line_text:
        continue
      bbox = self._valid_bbox(line.get("bbox"))
      lines.append(line_text)
      line_boxes.append(bbox)
      rotations.append(round(math.degrees(math.atan2(dy, dx))))
      for span in spans:
        size = float(span.get("size", 0) or 0)
        if size > 0:
          font_sizes.extend([size] * max(1, len(str(span.get("text", "")).strip())))
    if not lines:
      return None, rotated_ignored
    text, dehyphenations = self._join_block_lines(lines)
    if not text:
      return None, rotated_ignored
    return _LayoutBlock(
      source_index=source_index,
      bbox=(
        min(box[0] for box in line_boxes),
        min(box[1] for box in line_boxes),
        max(box[2] for box in line_boxes),
        max(box[3] for box in line_boxes),
      ),
      text=text,
      font_sizes=font_sizes,
      rotation=round(median(rotations)) if rotations else 0,
      dehyphenations=dehyphenations,
    ), rotated_ignored

  @staticmethod
  def _valid_bbox(value: object) -> tuple[float, float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
      raise ValueError("invalid text bbox")
    bbox = tuple(float(item) for item in value)
    if not all(math.isfinite(item) for item in bbox):
      raise ValueError("non-finite text bbox")
    if bbox[2] < bbox[0] or bbox[3] < bbox[1]:
      raise ValueError("negative text bbox")
    return bbox

  @staticmethod
  def _join_block_lines(lines: list[str]) -> tuple[str, int]:
    text = lines[0].strip()
    dehyphenations = 0
    for next_line in lines[1:]:
      next_line = next_line.strip()
      if not next_line:
        continue
      if re.search(r"[A-Za-z]-$", text) and re.match(r"[a-z]", next_line):
        text = text[:-1] + next_line
        dehyphenations += 1
      else:
        text += " " + next_line
    return " ".join(text.split()), dehyphenations

  def _repeated_margin_signatures(
    self, drafts: list[_PageDraft],
  ) -> set[tuple[str, str]]:
    occurrences: dict[tuple[str, str], set[int]] = {}
    for draft in drafts:
      for block in draft.blocks:
        signature = self._margin_signature(block, draft)
        if signature is not None:
          occurrences.setdefault(signature, set()).add(draft.page_number)
    page_count = len(drafts)
    return {
      signature
      for signature, page_numbers in occurrences.items()
      if len(page_numbers) >= 3 and page_count and len(page_numbers) / page_count >= .3
    }

  def _margin_signature(
    self, block: _LayoutBlock, draft: _PageDraft,
  ) -> tuple[str, str] | None:
    vertical_center = (block.bbox[1] + block.bbox[3]) / 2
    if vertical_center <= draft.height * .1:
      zone = "top"
    elif vertical_center >= draft.height * .9:
      zone = "bottom"
    else:
      return None
    normalized = re.sub(r"\d+", "#", block.text.casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return (zone, normalized) if normalized else None

  @staticmethod
  def _body_font_median(blocks: list[_LayoutBlock]) -> float:
    sizes = [size for block in blocks for size in block.font_sizes if size > 0]
    return float(median(sizes)) if sizes else 0.0

  def _finalize_layout_page(
    self,
    draft: _PageDraft,
    repeated: set[tuple[str, str]],
    body_font_size: float,
  ) -> PaperPage:
    if draft.extraction_error:
      return self._fallback_page(draft, draft.extraction_error)
    retained = [
      block for block in draft.blocks
      if self._margin_signature(block, draft) not in repeated
    ]
    removed = len(draft.blocks) - len(retained)
    try:
      ordered, column_layout = self._sort_blocks(retained, draft.width)
      if len({block.source_index for block in ordered}) != len(ordered):
        raise ValueError("duplicate layout block IDs")
      page_blocks: list[PaperTextBlock] = []
      text_parts: list[str] = []
      cursor = 0
      for reading_order, block in enumerate(ordered):
        if text_parts:
          cursor += 2
        start = cursor
        text_parts.append(block.text)
        cursor += len(block.text)
        page_blocks.append(PaperTextBlock(
          block_id=(
            f"page_{draft.page_number:04d}_block_{block.source_index:04d}"
          ),
          page_number=draft.page_number,
          bbox=block.bbox,
          reading_order=reading_order,
          block_type=self._classify_block(block, body_font_size),
          rotation=block.rotation,
          char_start=start,
          char_end=cursor,
          text=block.text,
        ))
      return PaperPage(
        page_number=draft.page_number,
        text="\n\n".join(text_parts),
        width=draft.width,
        height=draft.height,
        blocks=page_blocks,
        extraction_method="layout" if page_blocks else "empty",
        layout_metadata={
          "layout_version": LAYOUT_VERSION,
          "column_layout": column_layout if page_blocks else "empty",
          "header_footer_blocks_removed": removed,
          "dehyphenations": sum(block.dehyphenations for block in retained),
          "rotated_blocks_ignored": draft.rotated_blocks_ignored,
        },
      )
    except Exception as exc:
      return self._fallback_page(draft, f"sorting_{type(exc).__name__}")

  def _fallback_page(self, draft: _PageDraft, reason: str) -> PaperPage:
    return PaperPage(
      page_number=draft.page_number,
      text=draft.legacy_text,
      width=draft.width,
      height=draft.height,
      extraction_method="legacy_fallback" if draft.legacy_text else "empty",
      layout_metadata={
        "layout_version": LAYOUT_VERSION,
        "column_layout": "unknown" if draft.legacy_text else "empty",
        "fallback_reason": reason,
        "header_footer_blocks_removed": 0,
        "dehyphenations": 0,
        "rotated_blocks_ignored": draft.rotated_blocks_ignored,
      },
    )

  def _sort_blocks(
    self, blocks: list[_LayoutBlock], page_width: float,
  ) -> tuple[list[_LayoutBlock], str]:
    if not blocks:
      return [], "empty"
    separators = sorted(
      (block for block in blocks if block.bbox[2] - block.bbox[0] >= page_width * .7),
      key=self._position_key,
    )
    regular = [block for block in blocks if block not in separators]
    ordered: list[_LayoutBlock] = []
    double_column = False
    remaining = list(regular)
    for separator in separators:
      separator_center = (separator.bbox[1] + separator.bbox[3]) / 2
      region = [
        block for block in remaining
        if (block.bbox[1] + block.bbox[3]) / 2 < separator_center
      ]
      remaining = [block for block in remaining if block not in region]
      region_order, is_double = self._sort_region(region, page_width)
      ordered.extend(region_order)
      ordered.append(separator)
      double_column = double_column or is_double
    region_order, is_double = self._sort_region(remaining, page_width)
    ordered.extend(region_order)
    double_column = double_column or is_double
    if len(ordered) != len(blocks):
      raise ValueError("layout sorting lost blocks")
    return ordered, "double_column" if double_column else "single_column"

  def _sort_region(
    self, blocks: list[_LayoutBlock], page_width: float,
  ) -> tuple[list[_LayoutBlock], bool]:
    if not blocks:
      return [], False
    midpoint = page_width / 2
    tolerance = page_width * .04
    left = [
      block for block in blocks
      if (block.bbox[0] + block.bbox[2]) / 2 < midpoint
      and block.bbox[2] <= midpoint + tolerance
    ]
    right = [
      block for block in blocks
      if (block.bbox[0] + block.bbox[2]) / 2 >= midpoint
      and block.bbox[0] >= midpoint - tolerance
    ]
    assigned = {id(block) for block in left + right}
    overlap = (
      min(max(block.bbox[3] for block in left), max(block.bbox[3] for block in right))
      - max(min(block.bbox[1] for block in left), min(block.bbox[1] for block in right))
    ) if left and right else 0
    if left and right and len(assigned) == len(blocks) and overlap > 5:
      return (
        sorted(left, key=self._position_key) + sorted(right, key=self._position_key),
        True,
      )
    return sorted(blocks, key=self._position_key), False

  @staticmethod
  def _position_key(block: _LayoutBlock) -> tuple[float, float, int]:
    return (round(block.bbox[1], 2), round(block.bbox[0], 2), block.source_index)

  @staticmethod
  def _classify_block(block: _LayoutBlock, body_font_size: float) -> str:
    text = block.text.strip()
    if re.match(r"(?i)^(?:fig(?:ure)?\.?|table)\s*\d+|^[图表]\s*[一二三四五六七八九十\d]+", text):
      return "caption"
    heading = re.match(
      r"(?i)^(?:(?:\d+(?:\.\d+)*)\s+)?(?:abstract|摘要|introduction|引言|"
      r"background|背景|methods?|methodology|方法|experiments?|实验|results?|结果|"
      r"discussion|讨论|limitations?|局限|conclusions?|结论|references|参考文献)\b",
      text,
    )
    font_size = median(block.font_sizes) if block.font_sizes else 0
    if heading or (body_font_size and font_size >= body_font_size * 1.18 and len(text) <= 300):
      return "heading"
    if len(text) <= 2 and not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", text):
      return "other"
    return "body"

  @staticmethod
  def summarize_pages(pages: list[PaperPage], mode: str) -> dict[str, int | str]:
    """Build the safe, compact parsing diagnostics persisted with a task."""
    methods = [page.extraction_method for page in pages]
    columns = [page.layout_metadata.get("column_layout") for page in pages]
    return {
      "layout_version": LAYOUT_VERSION if mode == "auto" else "legacy",
      "mode": mode,
      "total_pages": len(pages),
      "layout_pages": methods.count("layout"),
      "legacy_pages": methods.count("legacy"),
      "fallback_pages": methods.count("legacy_fallback"),
      "empty_pages": methods.count("empty"),
      "single_column_pages": columns.count("single_column"),
      "double_column_pages": columns.count("double_column"),
      "blocks_retained": sum(len(page.blocks) for page in pages),
      "header_footer_blocks_removed": sum(
        int(page.layout_metadata.get("header_footer_blocks_removed", 0)) for page in pages
      ),
      "dehyphenations": sum(
        int(page.layout_metadata.get("dehyphenations", 0)) for page in pages
      ),
    }
  
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
  
