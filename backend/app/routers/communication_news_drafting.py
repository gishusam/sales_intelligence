"""News-source management, normalized ingestion, and AI draft routes."""

import os

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import news_drafting_repository
from app.communications import news_drafting_service
from app.communications.news_drafting_generator import (
    NewsDraftGeneratorError,
    build_default_generator,
)
from app.communications.news_drafting_schemas import (
    NewsArticleStatusUpdate,
    NewsDraftGenerationRequest,
    NewsIngestionRequest,
    NewsSourceCreate,
    NewsSourceUpdate,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications",
    tags=["communication-news-drafting"],
)


def require_editor(user: CurrentUser) -> None:
    if user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or admin access required",
        )


def require_worker_token(token: str | None) -> None:
    expected = os.getenv(
        "COMMUNICATIONS_WORKER_TOKEN",
        "",
    )

    if not expected or token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker token",
        )


def translate_error(exc: Exception) -> HTTPException:
    if isinstance(
        exc,
        news_drafting_service.NewsDraftingNotFoundError,
    ):
        return HTTPException(404, str(exc))

    if isinstance(
        exc,
        news_drafting_service.NewsDraftingConflictError,
    ):
        return HTTPException(409, str(exc))

    if isinstance(exc, NewsDraftGeneratorError):
        return HTTPException(503, str(exc))

    return HTTPException(422, str(exc))


@router.get("/news-sources")
def list_news_sources(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return news_drafting_repository.list_sources(
        db=db,
        include_inactive=include_inactive,
    )


@router.post(
    "/news-sources",
    status_code=status.HTTP_201_CREATED,
)
def create_news_source(
    payload: NewsSourceCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return news_drafting_repository.create_source(
            db=db,
            payload=payload,
            user=user,
        )
    except news_drafting_repository.NewsDraftingConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.put("/news-sources/{source_id}")
def update_news_source(
    source_id: int,
    payload: NewsSourceUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        item = news_drafting_repository.update_source(
            db=db,
            source_id=source_id,
            payload=payload,
            user=user,
        )
    except news_drafting_repository.NewsDraftingConflict as exc:
        raise HTTPException(409, str(exc)) from exc

    if item is None:
        raise HTTPException(404, "News source not found")

    return item


@router.post("/internal/news/ingest")
def ingest_news_articles(
    payload: NewsIngestionRequest,
    x_worker_token: str | None = Header(
        default=None,
        alias="X-Worker-Token",
    ),
    db: Session = Depends(get_db),
):
    require_worker_token(x_worker_token)

    try:
        return news_drafting_service.ingest_articles(
            db=db,
            payload=payload,
        )
    except news_drafting_service.NewsDraftingError as exc:
        raise translate_error(exc) from exc


@router.get("/news-articles")
def list_news_articles(
    source_id: int | None = Query(default=None, gt=0),
    status_filter: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return news_drafting_repository.list_articles(
        db=db,
        source_id=source_id,
        status_filter=status_filter,
        limit=limit,
    )


@router.put("/news-articles/{article_id}/status")
def update_news_article_status(
    article_id: int,
    payload: NewsArticleStatusUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)
    item = news_drafting_repository.update_article_status(
        db=db,
        article_id=article_id,
        status=payload.status,
    )

    if item is None:
        raise HTTPException(404, "News article not found")

    return item


@router.post("/news-drafting/generate")
def generate_newsletter_draft(
    payload: NewsDraftGenerationRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        generator = build_default_generator()
        return news_drafting_service.generate_newsletter_draft(
            db=db,
            payload=payload,
            user=user,
            generator=generator,
        )
    except (
        news_drafting_service.NewsDraftingError,
        NewsDraftGeneratorError,
    ) as exc:
        raise translate_error(exc) from exc


@router.get("/news-drafting/runs")
def list_news_drafting_runs(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return news_drafting_repository.list_generation_runs(
        db=db,
        limit=limit,
    )
