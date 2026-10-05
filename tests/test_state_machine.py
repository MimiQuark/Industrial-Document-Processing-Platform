import pytest

from app.domain.enums import TaskStatus
from app.domain.state_machine import InvalidTransition, can_transition, ensure_transition


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.QUEUED, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.REVIEW_PENDING),
        (TaskStatus.RUNNING, TaskStatus.RETRYING),
        (TaskStatus.RETRYING, TaskStatus.DEAD),
        (TaskStatus.REVIEW_PENDING, TaskStatus.SUCCEEDED),
        (TaskStatus.FAILED, TaskStatus.QUEUED),
    ],
)
def test_valid_transitions(current, target):
    assert can_transition(current, target)
    ensure_transition(current, target)


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.QUEUED, TaskStatus.SUCCEEDED),
        (TaskStatus.SUCCEEDED, TaskStatus.RUNNING),
        (TaskStatus.REVIEW_PENDING, TaskStatus.QUEUED),
        (TaskStatus.CANCELED, TaskStatus.QUEUED),
    ],
)
def test_invalid_transitions(current, target):
    assert not can_transition(current, target)
    with pytest.raises(InvalidTransition):
        ensure_transition(current, target)
