from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.dependencies import get_trace_id, require_roles
from app.domain.enums import ReviewStatus, Role
from app.errors import AppError
from app.models import User
from app.schemas import ReviewCaseRead, ReviewDecisionRequest, ReviewListResponse
from app.services.reviews import (
    claim_first_available,
    claim_review_next,
    decide_review,
    get_review,
    list_reviews,
    release_review,
)

router = APIRouter(prefix="/api/v1/reviews", tags=["reviews"])


@router.get("", response_model=ReviewListResponse)
def list_all(
    status: ReviewStatus | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    session: Session = Depends(get_db),
):
    items, total = list_reviews(session, user, status=status, limit=limit, offset=offset)
    return ReviewListResponse(
        items=[ReviewCaseRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/claim-next", response_model=ReviewCaseRead | None)
def claim_next(
    user: User = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    review = claim_first_available(session, user, trace_id)
    return ReviewCaseRead.model_validate(review) if review else None


@router.get("/{review_id}", response_model=ReviewCaseRead)
def detail(
    review_id: UUID,
    user: User = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    session: Session = Depends(get_db),
):
    review = get_review(session, review_id)
    if user.role != Role.ADMIN and review.assignee_id not in {None, user.id}:
        raise AppError("FORBIDDEN", "该审核单已被其他审核员领取", 403)
    return ReviewCaseRead.model_validate(review)


@router.post("/{review_id}/claim", response_model=ReviewCaseRead)
def claim(
    review_id: UUID,
    user: User = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    return ReviewCaseRead.model_validate(claim_review_next(session, review_id, user, trace_id))


@router.post("/{review_id}/release", response_model=ReviewCaseRead)
def release(
    review_id: UUID,
    user: User = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    return ReviewCaseRead.model_validate(release_review(session, review_id, user, trace_id))


@router.post("/{review_id}/decision", response_model=ReviewCaseRead)
def decide(
    review_id: UUID,
    payload: ReviewDecisionRequest,
    user: User = Depends(require_roles(Role.REVIEWER, Role.ADMIN)),
    session: Session = Depends(get_db),
    trace_id: str = Depends(get_trace_id),
):
    return ReviewCaseRead.model_validate(
        decide_review(
            session,
            review_id,
            user,
            decision=payload.decision,
            comment=payload.comment,
            trace_id=trace_id,
        )
    )
