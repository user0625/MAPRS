from __future__ import annotations
from enum import Enum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator  #数据验证


class SourceType(str, Enum):
  """ Supported paper input source types"""

  PDF= "pdf"
  ARXIV = "arxiv"
  URL = "url"


class PaperInput(BaseModel):
  """
    User-provided paper input.

    This object represents what the user gives to the system.
    It is not the parsed paper itself.
  """

  #当模型字段的类型是枚举（Enum）时，将模型实例中该字段的值存储为枚举成员的值（.value），而不是枚举成员本身
  model_config = ConfigDict( use_enum_values=True )  

  source_type : SourceType = Field(
    default=SourceType.PDF,
    description=" 输入类型应为论文PDF、arxiv或url链接"
  )

  source_path : str = Field(
    ..., #必填且没有默认值
    min_length = 1,
    description = " 本地论文pdf，arxiv ID，arxiv url或paper url"
  )

  user_query : str = Field(
    default = "分析这篇论文，生成结构化的阅读报告",
    description = "用户对于论文分析的要求"
  )

  #剥离非空文本
  @field_validator("source_path", "user_query")
  @classmethod
  def strip_non_empty_text(cls, value:str) -> str:
    value = value.strip()
    if not value:
      raise ValueError("输入不能为空")
    return value



class PaperMetadata(BaseModel):
  """
    Basic metadata of a paper.

    This metadata may come from PDF parsing, arXiv API, user input,
    or LLM-assisted extraction in later stages.
  """

  model_config = ConfigDict(use_enum_values=True)

  paper_id : str | None = Field(
    default=None,
    description="内部稳定的论文ID，可由文件哈希或元数据生成"
  )
  title: str | None = Field(default=None, description="论文标题")
  authors: list[str] | None = Field(default_factory=list, description="Author list")
  
  abstract: str | None = Field(default=None, description="Paper abstract")
  year:int | None = Field(default=None, ge=1900, le=2100)
  venue:str | None = Field(default=None, description="会议、期刊、出版社")
  doi:str | None = Field(default=None)
  arxiv_id:str | None = Field(default=None)
  source_type:SourceType | None = Field(default=None)
  source_path:str | None = Field(default=None, description="源文件路径或标识")
  total_pages:int | None = Field(default=None, ge=0)
  language:str | None = Field(default=None, description="检测或假定语言")
  keywords:list[str] = Field(default_factory=list)

  @field_validator("title", "abstract", "venue", "doi", "arxiv_id", "source_path", "language")
  @classmethod
  def strip_optional_text(cls, value:str | None) -> str|None:
    if value is None:
      return None
    value = value.strip()
    return value or None
  
  @field_validator("authors", "keywords")
  @classmethod
  def clean_string_list(cls, values:list[str]) -> list[str]:
    cleaned = []
    for item in values:
      item = item.strip()
      if item:
        cleaned.append(item)
    return cleaned
  

class PaperPage(BaseModel):
  """
    Text extracted from one page of a paper
    
    Page numbers are 1-based, because they are user-facing and easier
    to cite in reports.
  """

  page_number: int = Field(..., ge=1, description="1-based page number")
  text: str = Field(default="", description="从这页中提取文本")

  @field_validator("text")
  @classmethod
  def normalize_text(cls, value: str) -> str:
    return value.strip()
  
class PaperSection(BaseModel):
  """
    Optional structured section of a paper
    Section extraction can be added later. For MVP, this can remain unused.
  """

  section_id: str = Field(..., min_length=1)
  name: str = Field(..., min_length=1, description="章节名，如：引言，方法等")
  page_start: int | None = Field(default=None, ge=1)
  page_end: int|None = Field(default=None, ge=1)
  text: str = Field(default="")

  @field_validator("section_id", "name")
  @classmethod
  def strip_required_text(cls, value:str) -> str:
    value = value.strip()
    if not value:
      raise ValueError("Field cannot be empty")
    return value
  
  @field_validator("text")
  @classmethod
  def strip_text(cls, value:str)->str:
    return value.strip()
  
  @model_validator(mode="after")
  def validate_page_range(self) -> "PaperSection":
    if self.page_start is not None and self.page_end is not None:
      if self.page_end < self.page_start:
        raise ValueError("page end number must be greater than or equal to pagestart")
      return self
    

class PaperChunk(BaseModel):
  """
    Smallest retrieval unit used by RAG.
    A chunk should be preserve enough location information so that later 
    Agent outputs can cite where the evidence came from.
  """

  chunk_id: str = Field(..., min_length=1, description="Stable chunk ID")
  paper_id: str|None = Field(default=None)
  text: str = Field(..., min_length=1)

  page_start: int | None = Field(default=None, ge=1)
  page_end: int|None = Field(default=None, ge=1)
  section: str|None = Field(default=None)

  char_start: int|None = Field(default=None, ge=0)
  char_end: int|None = Field(default=None, ge=0)
  
  metadata: dict[str, Any] = Field(default_factory=dict)

  @field_validator("chunk_id", "text")
  @classmethod
  def strip_required_text(cls, value:str) -> str:
    value = value.strip()
    if not value:
      raise ValueError("Field cannot be empty")
    return value
  
  @field_validator("paper_id", "text")
  @classmethod
  def strip_optional_text(cls, value:str | None) -> str|None:
    if value is None:
      return None
    value = value.strip()
    return value or None
  
  @model_validator(mode="after")
  def validate_ranges(self)-> "PaperChunk":
    if self.page_start is not None and self.page_end is not None:
      if self.page_end < self.page_start:
        raise ValueError("page end number must be greater than or equal to pagestart")
    if self.char_start is not None and self.char_end is not None:
      if self.char_end < self.char_start:
        raise ValueError("chunk end number must be greater than or equal to chunk start")
      
    return self
  
class PaperDocument(BaseModel):
  """
    Aggregated representation of a parsed paper.
    This is the main object passed from document parsing to later modules.
  """

  metadata: PaperMetadata = Field(default_factory=PaperMetadata)
  pages: list[PaperPage] = Field(default_factory=list)
  sections: list[PaperSection] = Field(default_factory=list)
  chunks: list[PaperChunk] = Field(default_factory=list)

  def full_text(self)-> str:
    """Return concatenated page text"""
    return "\n\n".join(page.text for page in self.pages if page.text.strip())
  
  def page_count(self) -> int:
    """"Return parsed page count"""
    return len(self.pages)
  
  def has_chunks(self)->bool:
    """check whether the document has been chunked."""
    return len(self.chunks) > 0




   
   

