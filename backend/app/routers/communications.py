"""Communications API routes."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    status,
)
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.communications import template_repository as repository
from app.communications.schemas import (
    TemplateCreate,
    TemplateResponse,
    TemplateType,
    TemplateUpdate,
)
from app.communications.template_repository import (
    TemplateSlugConflict,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications",
    tags=["communications"],
)


def require_template_editor(user: CurrentUser) -> None:
    """Restrict global template changes to managers and admins."""

    if user.role not in ("admin", "manager"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager or admin access required",
        )


@router.get(
    "/templates",
    response_model=list[TemplateResponse],
)
def list_communication_templates(
    template_type: TemplateType | None = Query(None),
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """List reusable templates available to the signed-in user."""

    return repository.list_templates(
        db=db,
        template_type=template_type,
        include_inactive=include_inactive,
    )


@router.get(
    "/templates/{template_id}",
    response_model=TemplateResponse,
)
def get_communication_template(
    template_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Return one reusable communication template."""

    template = repository.get_template(
        db=db,
        template_id=template_id,
    )

    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )

    return template


@router.post(
    "/templates",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_communication_template(
    payload: TemplateCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Create a reusable template."""

    require_template_editor(user)

    try:
        return repository.create_template(
            db=db,
            payload=payload,
            user=user,
        )
    except TemplateSlugConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc


@router.put(
    "/templates/{template_id}",
    response_model=TemplateResponse,
)
def update_communication_template(
    template_id: int,
    payload: TemplateUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Update and version a reusable template."""

    require_template_editor(user)

    try:
        updated = repository.update_template(
            db=db,
            template_id=template_id,
            payload=payload,
            user=user,
        )
    except TemplateSlugConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Template not found",
        )

    return updated
