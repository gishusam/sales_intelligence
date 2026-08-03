"""Authenticated operational Communications overview."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import communications_overview_repository
from app.communications.provider_event_schemas import (
    CommunicationsOverviewResponse,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications",
    tags=["communication-overview"],
)


@router.get(
    "/overview",
    response_model=CommunicationsOverviewResponse,
)
def get_communications_overview(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return communications_overview_repository.get_overview(
        db=db,
    )
