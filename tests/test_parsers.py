from pathlib import Path

from app.domain.enums import TaskType
from app.parsers.gerber import parse_gerber
from app.parsers.svg import parse_svg


def test_svg_parser_detects_script_and_external_reference(tmp_path: Path):
    svg = tmp_path / "sample.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
        '<script>alert(1)</script><image href="https://example.com/a.png"/></svg>',
        encoding="utf-8",
    )
    result = parse_svg(svg)
    assert result.task_type == TaskType.SVG
    assert result.metrics["script_count"] == 1
    assert result.metrics["external_ref_count"] == 1


def test_gerber_parser_reads_core_commands(tmp_path: Path):
    gerber = tmp_path / "sample.gbr"
    gerber.write_text(
        "G04 sample*\n%FSLAX24Y24*%\n%MOMM*%\n%ADD10C,0.5*%\nD10*\nX0Y0D02*\nX100Y100D01*\nM02*\n",
        encoding="utf-8",
    )
    result = parse_gerber(gerber)
    assert result.task_type == TaskType.GERBER
    assert result.metrics["has_format"] is True
    assert result.metrics["has_units"] is True
    assert result.metrics["has_end"] is True
    assert result.metrics["operation_draw"] == 1
