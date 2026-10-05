from app.domain.enums import TaskStatus

TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.QUEUED: {TaskStatus.RUNNING, TaskStatus.CANCELED},
    TaskStatus.RUNNING: {
        TaskStatus.SUCCEEDED,
        TaskStatus.REVIEW_PENDING,
        TaskStatus.RETRYING,
        TaskStatus.FAILED,
        TaskStatus.CANCELING,
    },
    TaskStatus.RETRYING: {
        TaskStatus.QUEUED,
        TaskStatus.RUNNING,
        TaskStatus.CANCELED,
        TaskStatus.DEAD,
    },
    TaskStatus.CANCELING: {TaskStatus.CANCELED},
    TaskStatus.REVIEW_PENDING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED},
    TaskStatus.FAILED: {TaskStatus.QUEUED},
    TaskStatus.DEAD: {TaskStatus.QUEUED},
}


class InvalidTransition(ValueError):
    def __init__(self, current: TaskStatus, target: TaskStatus) -> None:
        super().__init__(f"不允许从 {current.value} 转换到 {target.value}")
        self.current = current
        self.target = target


def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
    if current == target:
        return True
    return target in TRANSITIONS.get(current, set())


def ensure_transition(current: TaskStatus, target: TaskStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
