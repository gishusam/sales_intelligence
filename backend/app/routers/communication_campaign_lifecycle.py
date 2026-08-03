"""Campaign preparation, scheduling, pause, resume, and cancel routes."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    status,
)
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import campaign_lifecycle as lifecycle_service
from app.communications.campaign_lifecycle_schemas import (
    CampaignPreparationResult,
    CampaignScheduleRequest,
    CampaignScheduleResult,
    CampaignStateResult,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications/campaigns",
    tags=["communication-campaign-lifecycle"],
)


def require_campaign_editor(
    user: CurrentUser,
) -> None:
    if user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or admin access required",
        )


def translate_error(
    exc: lifecycle_service.CampaignLifecycleError,
) -> HTTPException:
    if isinstance(
        exc,
        lifecycle_service.CampaignNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        lifecycle_service.CampaignStateError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=str(exc),
    )


@router.post(
    "/{campaign_id}/prepare",
    response_model=CampaignPreparationResult,
)
def prepare_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return lifecycle_service.prepare_campaign(
            db=db,
            campaign_id=campaign_id,
            user=user,
        )
    except lifecycle_service.CampaignLifecycleError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/{campaign_id}/schedule",
    response_model=CampaignScheduleResult,
)
def schedule_campaign(
    campaign_id: int,
    payload: CampaignScheduleRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return lifecycle_service.schedule_campaign(
            db=db,
            campaign_id=campaign_id,
            payload=payload,
            user=user,
        )
    except lifecycle_service.CampaignLifecycleError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/{campaign_id}/pause",
    response_model=CampaignStateResult,
)
def pause_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return lifecycle_service.pause_campaign(
            db=db,
            campaign_id=campaign_id,
            user=user,
        )
    except lifecycle_service.CampaignLifecycleError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/{campaign_id}/resume",
    response_model=CampaignStateResult,
)
def resume_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return lifecycle_service.resume_campaign(
            db=db,
            campaign_id=campaign_id,
            user=user,
        )
    except lifecycle_service.CampaignLifecycleError as exc:
        raise translate_error(exc) from exc


@router.post(
    "/{campaign_id}/cancel",
    response_model=CampaignStateResult,
)
def cancel_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return lifecycle_service.cancel_campaign(
            db=db,
            campaign_id=campaign_id,
            user=user,
        )
    except lifecycle_service.CampaignLifecycleError as exc:
        raise translate_error(exc) from exc
