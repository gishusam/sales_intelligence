"""Draft campaign, step, and recipient enrolment routes."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import campaign_repository
from app.communications import campaign_service
from app.communications.campaign_schemas import (
    CampaignCreate,
    CampaignEnrollmentRequest,
    CampaignEnrollmentResult,
    CampaignResponse,
    CampaignStatus,
    CampaignStepCreate,
    CampaignStepResponse,
    CampaignUpdate,
    CampaignRecipientResponse,
    RecipientStatus,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications/campaigns",
    tags=["communication-campaigns"],
)


def require_campaign_editor(
    user: CurrentUser,
) -> None:
    if user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or admin access required",
        )


def _translate_campaign_error(
    exc: campaign_service.CampaignError,
) -> HTTPException:
    if isinstance(
        exc,
        campaign_service.CampaignNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        campaign_service.CampaignConflictError,
    ):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )

    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=str(exc),
    )


@router.get(
    "",
    response_model=list[CampaignResponse],
)
def list_campaigns(
    status_filter: CampaignStatus | None = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return campaign_repository.list_campaigns(
        db=db,
        status_filter=status_filter,
    )


@router.get(
    "/{campaign_id}",
    response_model=CampaignResponse,
)
def get_campaign(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = campaign_repository.get_campaign(
        db=db,
        campaign_id=campaign_id,
    )

    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign


@router.post(
    "",
    response_model=CampaignResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_campaign(
    payload: CampaignCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return campaign_service.create_campaign(
            db=db,
            payload=payload,
            user=user,
        )
    except campaign_service.CampaignError as exc:
        raise _translate_campaign_error(exc) from exc


@router.put(
    "/{campaign_id}",
    response_model=CampaignResponse,
)
def update_campaign(
    campaign_id: int,
    payload: CampaignUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return campaign_service.update_campaign(
            db=db,
            campaign_id=campaign_id,
            payload=payload,
            user=user,
        )
    except campaign_service.CampaignError as exc:
        raise _translate_campaign_error(exc) from exc


@router.get(
    "/{campaign_id}/steps",
    response_model=list[CampaignStepResponse],
)
def list_campaign_steps(
    campaign_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = campaign_repository.get_campaign(
        db=db,
        campaign_id=campaign_id,
    )

    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign_repository.list_campaign_steps(
        db=db,
        campaign_id=campaign_id,
    )


@router.post(
    "/{campaign_id}/steps",
    response_model=CampaignStepResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_campaign_step(
    campaign_id: int,
    payload: CampaignStepCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return campaign_service.add_campaign_step(
            db=db,
            campaign_id=campaign_id,
            payload=payload,
            user=user,
        )
    except campaign_service.CampaignError as exc:
        raise _translate_campaign_error(exc) from exc


@router.post(
    "/{campaign_id}/recipients/enroll",
    response_model=CampaignEnrollmentResult,
)
def enroll_campaign_recipients(
    campaign_id: int,
    payload: CampaignEnrollmentRequest,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_campaign_editor(user)

    try:
        return campaign_service.enroll_campaign_leads(
            db=db,
            campaign_id=campaign_id,
            payload=payload,
            user=user,
        )
    except campaign_service.CampaignError as exc:
        raise _translate_campaign_error(exc) from exc


@router.get(
    "/{campaign_id}/recipients",
    response_model=list[CampaignRecipientResponse],
)
def list_campaign_recipients(
    campaign_id: int,
    status_filter: RecipientStatus | None = Query(None),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    campaign = campaign_repository.get_campaign(
        db=db,
        campaign_id=campaign_id,
    )

    if campaign is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Campaign not found",
        )

    return campaign_repository.list_campaign_recipients(
        db=db,
        campaign_id=campaign_id,
        status_filter=status_filter,
    )
