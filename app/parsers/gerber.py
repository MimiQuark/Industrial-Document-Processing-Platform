import re
from pathlib import Path

from app.domain.enums import TaskType
from app.domain.errors import PermanentProcessingError
from app.parsers.base import ParseResult

PARSER_VERSION = "gerber-1"
EXTENDED_RE = re.compile(r"%((?:FS|MO|AD|AM|LP|LM|LR|LS|TF|TA|TO|TD))([^%]*)%")
OPERATION_RE = re.compile(r"(?:G0?[123])?.*D0?([123])\*")


def parse_gerber(path: Path) -> ParseResult:
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PermanentProcessingError("Gerber 必须使用 UTF-8/ASCII 文本编码") from exc
    except OSError as exc:
        raise PermanentProcessingError(f"Gerber 读取失败: {exc}") from exc

    lines = text.splitlines()
    extended_commands: list[str] = []
    aperture_definitions: set[str] = set()
    operation_counts = {"draw": 0, "move": 0, "flash": 0}
    unknown_lines = 0
    has_end = False

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("G04"):
            continue
        for match in EXTENDED_RE.finditer(line):
            command = match.group(1)
            extended_commands.append(command)
            if command == "AD":
                aperture_definitions.add(match.group(2).strip())
        if "M02*" in line:
            has_end = True
        for match in OPERATION_RE.finditer(line):
            operation = match.group(1)
            if operation == "1":
                operation_counts["draw"] += 1
            elif operation == "2":
                operation_counts["move"] += 1
            elif operation == "3":
                operation_counts["flash"] += 1
        if not line.startswith("%") and not re.search(r"[DGXM]\d|\*", line):
            unknown_lines += 1

    has_format = "FS" in extended_commands
    has_units = "MO" in extended_commands
    metrics = {
        "has_format": has_format,
        "has_units": has_units,
        "has_end": has_end,
        "unknown_line_count": unknown_lines,
        "aperture_count": len(aperture_definitions),
        **{f"operation_{key}": value for key, value in operation_counts.items()},
    }
    summary = {
        "line_count": len(lines),
        "extended_commands": sorted(set(extended_commands)),
        "apertures": sorted(aperture_definitions),
        "operations": operation_counts,
        "format": has_format,
        "units": has_units,
        "ended": has_end,
    }
    return ParseResult(TaskType.GERBER, PARSER_VERSION, metrics, summary)
