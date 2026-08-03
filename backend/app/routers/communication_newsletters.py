"""Newsletter API routes."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import newsletter_repository
from app.communications import newsletter_service
from app.communications.newsletter_schemas import (
    NewsletterAudienceRequest,
    NewsletterCreate,
    NewsletterResponse,
    NewsletterStatus,
    NewsletterTestSendRequest,
    NewsletterUpdate,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications/newsletters",
    tags=["communication-newsletters"],
)


def require_editor(user: CurrentUser) -> None:
    if user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or admin access required",
        )


def translate_error(exc):
    if isinstance(
        exc,
        newsletter_service.NewsletterNotFoundError,
    ):
        return HTTPException(404, str(exc))

    if isinstance(
        exc,
        newsletter_service.NewsletterStateError,
    ):
        return HTTPException(409, str(exc))

    return HTTPException(422, str(exc))


@router.get("", response_model=list[NewsletterResponse])
def list_newsletters(
    status_filter: NewsletterStatus | None = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return newsletter_repository.list_newsletters(
        db=db,
        status_filter=status_filter,
    )


@router.get("/{newsletter_id:int}", response_model=NewsletterResponse)
def get_newsletter(
    newsletter_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    item = newsletter_repository.get_newsletter(
        db=db,
        newsletter_id=newsletter_id,
    )

    if item is None:
        raise HTTPException(404, "Newsletter not found")

    return item


@router.post(
    "",
    response_model=NewsletterResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_newsletter(
    payload: NewsletterCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_service.create_newsletter(
            db=db,
            payload=payload,
            user=user,
        )
    except newsletter_service.NewsletterError as exc:
        raise translate_error(exc) from exc


@router.put(
    "/{newsletter_id}",
    response_model=NewsletterResponse,
)
def update_newsletter(
    newsletter_id: int,
    payload: NewsletterUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_service.update_newsletter(
            db=db,
            newsletter_id=newsletter_id,
            payload=payload,
            user=user,
        )
    except newsletter_service.NewsletterError as exc:
        raise translate_error(exc) from exc


@router.post("/{newsletter_id}/review")
def review_newsletter(
    newsletter_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_service.submit_for_review(
            db=db,
            newsletter_id=newsletter_id,
            user=user,
        )
    except newsletter_service.NewsletterError as exc:
        raise translate_error(exc) from exc


@router.post("/{newsletter_id}/approve")
def approve_newsletter(
    newsletter_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_service.approve_newsletter(
            db=db,
            newsletter_id=newsletter_id,
            user=user,
        )
    except newsletter_service.NewsletterError as exc:
        raise translate_error(exc) from exc


@router.post("/{newsletter_id}/audience")
def add_audience(
    newsletter_id: int,
    payload: NewsletterAudienceRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_service.snapshot_audience(
            db=db,
            newsletter_id=newsletter_id,
            payload=payload,
            user=user,
        )
    except newsletter_service.NewsletterError as exc:
        raise translate_error(exc) from exc


@router.get("/{newsletter_id}/recipients")
def list_recipients(
    newsletter_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return newsletter_repository.list_recipients(
        db=db,
        newsletter_id=newsletter_id,
    )


@router.post("/{newsletter_id}/test-send")
def test_send(
    newsletter_id: int,
    payload: NewsletterTestSendRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return newsletter_service.test_send(
            db=db,
            newsletter_id=newsletter_id,
            payload=payload,
            user=user,
        )
    except newsletter_service.NewsletterError as exc:
        raise translate_error(exc) from exc


@router.get("/unsubscribe/{token}")
def unsubscribe(
    token: str,
    db: Session = Depends(get_db),
):
    try:
        return newsletter_service.unsubscribe(
            db=db,
            token=token,
        )
    except newsletter_service.NewsletterError as exc:
        raise translate_error(exc) from exc
