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
        if encrypted:
            # 加密 PDF 无法读取页面/元数据；先返回可解释的结果，
            # 让 PDF_ENCRYPTED 规则生效，而不是在 len(reader.pages) 处崩溃。
            metrics = {
                "page_count": 0,
                "is_encrypted": True,
                "text_char_count": 0,
                "metadata_count": 0,
            }
            summary = {
                "page_count": 0,
                "metadata": {},
                "text_preview": "",
                "encrypted": True,
            }
            return ParseResult(TaskType.PDF, PARSER_VERSION, metrics, summary)
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
