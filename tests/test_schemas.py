import pytest
from pydantic import ValidationError

from app.domain.enums import ReviewDecision
from app.schemas import ReviewDecisionRequest


def test_reject_requires_comment():
    with pytest.raises(ValidationError):
        ReviewDecisionRequest(decision=ReviewDecision.REJECT)
    request = ReviewDecisionRequest(decision=ReviewDecision.REJECT, comment="尺寸不满足要求")
    assert request.comment == "尺寸不满足要求"
