"""Authenticated and worker-token Communications readiness endpoints."""

import os
import secrets

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications.communications_readiness import (
    build_readiness_report,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications",
    tags=["communication-readiness"],
)


def require_worker_token(token: str | None) -> None:
    expected = os.getenv(
        "COMMUNICATIONS_WORKER_TOKEN",
        "",
    )

    if (
        not expected
        or not token
        or not secrets.compare_digest(token, expected)
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker token",
        )


@router.get("/readiness")
def get_readiness(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return build_readiness_report(db=db)


@router.get("/internal/readiness")
def get_internal_readiness(
    x_worker_token: str | None = Header(
        default=None,
        alias="X-Worker-Token",
    ),
    db: Session = Depends(get_db),
):
    require_worker_token(x_worker_token)
    return build_readiness_report(db=db)
