from collections import Counter
from pathlib import Path
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as DefusedET
from defusedxml.common import DefusedXmlException

from app.domain.enums import TaskType
from app.domain.errors import PermanentProcessingError
from app.parsers.base import ParseResult

PARSER_VERSION = "svg-1"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_svg(path: Path) -> ParseResult:
    try:
        root = DefusedET.parse(str(path)).getroot()
        tag_counts = Counter(_local_name(element.tag) for element in root.iter())
        script_count = tag_counts.get("script", 0)
        external_refs = 0
        for element in root.iter():
            for attribute, value in element.attrib.items():
                key = _local_name(attribute)
                if (
                    key in {"href", "src"}
                    and isinstance(value, str)
                    and value.startswith(("http://", "https://", "//", "file:"))
                ):
                    external_refs += 1
        width = root.attrib.get("width")
        height = root.attrib.get("height")
        view_box = root.attrib.get("viewBox")
        metrics = {
            "element_count": sum(tag_counts.values()),
            "script_count": script_count,
            "external_ref_count": external_refs,
            "has_viewbox": bool(view_box),
            "has_dimensions": bool(width and height),
            "text_element_count": tag_counts.get("text", 0),
        }
        summary = {
            "root": _local_name(root.tag),
            "width": width,
            "height": height,
            "viewBox": view_box,
            "element_counts": dict(tag_counts.most_common(20)),
        }
        return ParseResult(TaskType.SVG, PARSER_VERSION, metrics, summary)
    except (ParseError, DefusedXmlException, OSError, ValueError) as exc:
        raise PermanentProcessingError(f"SVG 解析或安全检查失败: {exc}") from exc
