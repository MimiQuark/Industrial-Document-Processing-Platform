from app.domain.enums import RuleSeverity, TaskStatus
from app.domain.rules import _compare, determine_status
from app.parsers.base import Finding


def test_rule_comparison_operators():
    assert _compare(11, "gt", 10)
    assert _compare(True, "truthy", True)
    assert _compare(False, "falsy", True)
    assert not _compare("mm", "eq", "inch")


def test_status_decision_priority():
    assert determine_status([]) == TaskStatus.SUCCEEDED
    assert determine_status([Finding("W", RuleSeverity.WARNING, "warning")]) == (
        TaskStatus.REVIEW_PENDING
    )
    assert (
        determine_status(
            [
                Finding("W", RuleSeverity.WARNING, "warning"),
                Finding("C", RuleSeverity.CRITICAL, "critical"),
            ]
        )
        == TaskStatus.FAILED
    )
