from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from app.domain.enums import TaskType
from app.domain.errors import PermanentProcessingError
from app.parsers.base import ParseResult

PARSER_VERSION = "pdf-1"


def parse_pdf(path: Path) -> ParseResult:
    try:
        reader = PdfReader(str(path), strict=False)
        encrypted = bool(reader.is_encrypted)
        page_count = len(reader.pages)
        metadata = {str(k): str(v) for k, v in (reader.metadata or {}).items()}
        text = ""
        if not encrypted:
            for page in reader.pages[: min(page_count, 20)]:
                text += page.extract_text() or ""
        metrics = {
            "page_count": page_count,
            "is_encrypted": encrypted,
            "text_char_count": len(text.strip()),
            "metadata_count": len(metadata),
        }
        summary = {
            "page_count": page_count,
            "metadata": metadata,
            "text_preview": text.strip()[:500],
            "encrypted": encrypted,
        }
        return ParseResult(TaskType.PDF, PARSER_VERSION, metrics, summary)
    except (PdfReadError, OSError, ValueError) as exc:
        raise PermanentProcessingError(f"PDF 解析失败: {exc}") from exc
