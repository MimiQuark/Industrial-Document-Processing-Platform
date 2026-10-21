from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.orm import Session, selectinload

from app.config import get_settings
from app.domain.enums import ReviewDecision, ReviewStatus, Role, TaskStatus
from app.errors import AppError
from app.models import ReviewCase, Task, User
from app.services.audit import append_audit_event
from app.services.tasks import transition_task, utcnow


def list_reviews(
    session: Session,
    user: User,
    *,
    status: ReviewStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[ReviewCase], int]:
    filters = []
    if status:
        filters.append(ReviewCase.status == status)
    if user.role == Role.REVIEWER:
        filters.append(
            or_(ReviewCase.status == ReviewStatus.PENDING, ReviewCase.assignee_id == user.id)
        )
    query = (
        select(ReviewCase)
        .join(Task, ReviewCase.task_id == Task.id)
        .options(selectinload(ReviewCase.task).selectinload(Task.file))
        .where(*filters)
    )
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = session.scalars(
        query.order_by(ReviewCase.created_at.asc()).limit(min(limit, 100)).offset(max(offset, 0))
    ).all()
    return list(items), total


def get_review(session: Session, review_id: UUID) -> ReviewCase:
    review = session.scalar(
        select(ReviewCase)
        .options(selectinload(ReviewCase.task).selectinload(Task.file))
        .where(ReviewCase.id == review_id)
    )
    if review is None:
        raise AppError("REVIEW_NOT_FOUND", "审核单不存在", 404)
    return review


def _check_self_review(review: ReviewCase, user: User) -> None:
    if review.task.owner_id == user.id:
        raise AppError("SELF_REVIEW_FORBIDDEN", "不能审核自己提交的任务", 403)


def claim_review_next(session: Session, review_id: UUID, user: User, trace_id: str) -> ReviewCase:
    review = get_review(session, review_id)
    _check_self_review(review, user)
    now = utcnow()
    lease_expires = now + timedelta(minutes=get_settings().review_lease_minutes)
    if review.status == ReviewStatus.IN_REVIEW and review.assignee_id == user.id:
        return review
    result = session.execute(
        update(ReviewCase)
        .where(
            ReviewCase.id == review.id,
            or_(
                ReviewCase.status == ReviewStatus.PENDING,
                ReviewCase.lease_expires_at < now,
            ),
        )
        .values(
            status=ReviewStatus.IN_REVIEW,
            assignee_id=user.id,
            claimed_at=now,
            lease_expires_at=lease_expires,
            version=ReviewCase.version + 1,
        )
        .returning(ReviewCase.id)
    )
    if result.scalar_one_or_none() is None:
        raise AppError("REVIEW_ALREADY_CLAIMED", "审核单已被其他审核员领取", 409)
    append_audit_event(
        session,
        actor_id=user.id,
        action="review.claimed",
        resource_type="review",
        resource_id=str(review.id),
        trace_id=trace_id,
        after_value={"assignee_id": str(user.id), "lease_expires_at": lease_expires.isoformat()},
    )
    session.commit()
    session.refresh(review)
    return review


def claim_first_available(session: Session, user: User, trace_id: str) -> ReviewCase | None:
    review = session.scalar(
        select(ReviewCase)
        .join(Task, ReviewCase.task_id == Task.id)
        .where(
            or_(
                ReviewCase.status == ReviewStatus.PENDING,
                and_(
                    ReviewCase.status == ReviewStatus.IN_REVIEW,
                    ReviewCase.lease_expires_at < utcnow(),
                ),
            ),
            Task.owner_id != user.id,
        )
        .order_by(ReviewCase.created_at.asc())
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if review is None:
        return None
    now = utcnow()
    review.status = ReviewStatus.IN_REVIEW
    review.assignee_id = user.id
    review.claimed_at = now
    review.lease_expires_at = now + timedelta(minutes=get_settings().review_lease_minutes)
    review.version += 1
    append_audit_event(
        session,
        actor_id=user.id,
        action="review.claimed",
        resource_type="review",
        resource_id=str(review.id),
        trace_id=trace_id,
        after_value={"assignee_id": str(user.id)},
    )
    session.commit()
    session.refresh(review)
    return review


def release_review(session: Session, review_id: UUID, user: User, trace_id: str) -> ReviewCase:
    # 加行锁并重新检查状态：否则 release 与 decide 交错时，晚提交的
    # release 会把已决策的审核单改回 PENDING，覆盖不可覆盖的结论。
    review = session.scalar(
        select(ReviewCase).where(ReviewCase.id == review_id).with_for_update()
    )
    if review is None:
        raise AppError("REVIEW_NOT_FOUND", "审核单不存在", 404)
    if review.status != ReviewStatus.IN_REVIEW:
        raise AppError("INVALID_REVIEW_STATE", "只有审核中的审核单可以释放", 409)
    if user.role != Role.ADMIN and review.assignee_id != user.id:
        raise AppError("FORBIDDEN", "只能释放自己领取的审核单", 403)
    previous_assignee = review.assignee_id
    review.status = ReviewStatus.PENDING
    review.assignee_id = None
    review.claimed_at = None
    review.lease_expires_at = None
    review.version += 1
    append_audit_event(
        session,
        actor_id=user.id,
        action="review.released",
        resource_type="review",
        resource_id=str(review.id),
        trace_id=trace_id,
        before_value={"assignee_id": str(previous_assignee) if previous_assignee else None},
    )
    session.commit()
    session.refresh(review)
    return review


def decide_review(
    session: Session,
    review_id: UUID,
    user: User,
    *,
    decision: ReviewDecision,
    comment: str | None,
    trace_id: str,
) -> ReviewCase:
    review = session.scalar(select(ReviewCase).where(ReviewCase.id == review_id).with_for_update())
    if review is None:
        raise AppError("REVIEW_NOT_FOUND", "审核单不存在", 404)
    task = session.scalar(select(Task).where(Task.id == review.task_id).with_for_update())
    if task is None:
        raise AppError("TASK_NOT_FOUND", "任务不存在", 404)
    review.task = task
    _check_self_review(review, user)
    if review.status != ReviewStatus.IN_REVIEW:
        raise AppError("INVALID_REVIEW_STATE", "审核单当前不可决策", 409)
    if user.role != Role.ADMIN and review.assignee_id != user.id:
        raise AppError("FORBIDDEN", "只能处理自己领取的审核单", 403)
    if (
        user.role != Role.ADMIN
        and review.lease_expires_at is not None
        and review.lease_expires_at < utcnow()
    ):
        raise AppError("REVIEW_LEASE_EXPIRED", "审核租约已过期，请重新领取", 409)
    if decision == ReviewDecision.REJECT and not (comment and comment.strip()):
        raise AppError("REVIEW_COMMENT_REQUIRED", "驳回时必须填写审核意见", 400)

    target = TaskStatus.SUCCEEDED if decision == ReviewDecision.APPROVE else TaskStatus.FAILED
    transition_task(
        session,
        task,
        target=target,
        progress=100,
        trace_id=trace_id,
        event_type="review.approved" if target == TaskStatus.SUCCEEDED else "review.rejected",
        message=comment,
    )
    if target == TaskStatus.FAILED:
        task.failure_code = "REVIEW_REJECTED"
        task.failure_message = comment
    review.status = (
        ReviewStatus.APPROVED if decision == ReviewDecision.APPROVE else ReviewStatus.REJECTED
    )
    review.decision = decision
    review.comment = comment.strip() if comment else None
    review.decided_at = utcnow()
    review.lease_expires_at = None
    review.version += 1
    append_audit_event(
        session,
        actor_id=user.id,
        action="review.decided",
        resource_type="review",
        resource_id=str(review.id),
        trace_id=trace_id,
        before_value={"status": ReviewStatus.IN_REVIEW.value},
        after_value={"status": review.status.value, "decision": decision.value, "comment": comment},
    )
    session.commit()
    session.refresh(review)
    return review
