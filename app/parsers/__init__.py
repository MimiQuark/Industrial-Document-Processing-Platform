from pathlib import Path

from app.domain.enums import TaskType
from app.domain.errors import FileValidationError
from app.parsers.base import ParseResult
from app.parsers.gerber import parse_gerber
from app.parsers.pdf import parse_pdf
from app.parsers.svg import parse_svg

EXTENSION_MAP: dict[str, TaskType] = {
    ".pdf": TaskType.PDF,
    ".svg": TaskType.SVG,
    ".gerber": TaskType.GERBER,
    ".gbr": TaskType.GERBER,
}


def infer_task_type(filename: str) -> TaskType:
    suffix = Path(filename).suffix.lower()
    try:
        return EXTENSION_MAP[suffix]
    except KeyError as exc:
        raise FileValidationError(f"不支持的文件扩展名: {suffix or '无扩展名'}") from exc


def validate_signature(task_type: TaskType, content: bytes) -> None:
    if task_type == TaskType.PDF and not content.lstrip().startswith(b"%PDF-"):
        raise FileValidationError("PDF 文件签名无效")
    if task_type == TaskType.SVG and b"<svg" not in content[:4096].lower():
        raise FileValidationError("SVG 根元素无效")
    if task_type == TaskType.GERBER and b"\x00" in content[:4096]:
        raise FileValidationError("Gerber 文件不能包含二进制内容")


def parse_file(task_type: TaskType, path: Path) -> ParseResult:
    if task_type == TaskType.PDF:
        return parse_pdf(path)
    if task_type == TaskType.SVG:
        return parse_svg(path)
    if task_type == TaskType.GERBER:
        return parse_gerber(path)
    raise FileValidationError(f"不支持的任务类型: {task_type}")
