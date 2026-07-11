from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile


class UploadValidationError(ValueError):
    pass


@dataclass(frozen=True)
class SavedUpload:
    path: Path
    sha256: str
    size: int


def save_validated_pdf(file: UploadFile, destination: Path, max_bytes: int) -> SavedUpload:
    if not file.filename:
        raise UploadValidationError("Uploaded file has no filename.")
    if not file.filename.lower().endswith(".pdf"):
        raise UploadValidationError("Only PDF files are supported.")
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise UploadValidationError("Unsupported PDF content type.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    size = 0
    header = b""
    try:
        with destination.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                if not header:
                    header = chunk[:5]
                    if header != b"%PDF-":
                        raise UploadValidationError("File content is not a valid PDF.")
                size += len(chunk)
                if size > max_bytes:
                    raise UploadValidationError("Uploaded PDF exceeds the configured size limit.")
                digest.update(chunk)
                target.write(chunk)
        if not size:
            raise UploadValidationError("Uploaded PDF is empty.")
        return SavedUpload(destination, digest.hexdigest(), size)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        file.file.close()


def deduplication_key(pdf_sha256: str, query: str, language: str,
                      report_configuration: dict | None = None) -> str:
    import json
    normalized = " ".join(query.split()).casefold()
    config = json.dumps(report_configuration or {}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{pdf_sha256}\n{normalized}\n{language}\n{config}".encode()).hexdigest()
