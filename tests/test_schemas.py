import pytest
from pydantic import ValidationError

from app.domain.enums import ReviewDecision
from app.schemas import ReviewDecisionRequest


def test_reject_requires_comment():
    with pytest.raises(ValidationError):
        ReviewDecisionRequest(decision=ReviewDecision.REJECT)
    request = ReviewDecisionRequest(decision=ReviewDecision.REJECT, comment="尺寸不满足要求")
    assert request.comment == "尺寸不满足要求"


def test_rule_config_rejects_unknown_operator_and_missing_value():
    from app.domain.enums import RuleSeverity
    from app.schemas import RuleCreate

    with pytest.raises(ValidationError):
        RuleCreate(code="BAD_OP", name="bad", severity=RuleSeverity.WARNING,
                   config={"metric": "page_count", "operator": "between"})
    with pytest.raises(ValidationError):
        RuleCreate(code="BAD_VALUE", name="bad", severity=RuleSeverity.WARNING,
                   config={"metric": "page_count", "operator": "gt"})
    rule = RuleCreate(code="PAGE_GT", name="pages", severity=RuleSeverity.WARNING,
                      config={"metric": "page_count", "operator": "gt", "value": 10})
    assert rule.config["value"] == 10
