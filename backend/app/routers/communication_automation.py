"""Follow-up rule, execution, stop-state, and internal run routes."""

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
from app.communications import automation_repository
from app.communications import automation_service
from app.communications.automation_schemas import (
    AutomationExecutionResponse,
    AutomationRuleCreate,
    AutomationRuleResponse,
    AutomationRuleUpdate,
    AutomationRunRequest,
    AutomationRunResult,
    LeadAutomationStateResponse,
    LeadAutomationStateUpdate,
)
from app.database import get_db


router = APIRouter(
    prefix="/api/communications",
    tags=["communication-automation"],
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


def translate_error(
    exc: automation_service.AutomationError,
) -> HTTPException:
    if isinstance(
        exc,
        automation_service.AutomationNotFoundError,
    ):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )

    if isinstance(
        exc,
        automation_service.AutomationConflictError,
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
    "/automation-rules",
    response_model=list[AutomationRuleResponse],
)
def list_automation_rules(
    include_inactive: bool = Query(False),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return automation_repository.list_rules(
        db=db,
        include_inactive=include_inactive,
    )


@router.post(
    "/automation-rules",
    response_model=AutomationRuleResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_automation_rule(
    payload: AutomationRuleCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return automation_service.create_rule(
            db=db,
            payload=payload,
            user=user,
        )
    except automation_service.AutomationError as exc:
        raise translate_error(exc) from exc


@router.put(
    "/automation-rules/{rule_id}",
    response_model=AutomationRuleResponse,
)
def update_automation_rule(
    rule_id: int,
    payload: AutomationRuleUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    require_editor(user)

    try:
        return automation_service.update_rule(
            db=db,
            rule_id=rule_id,
            payload=payload,
            user=user,
        )
    except automation_service.AutomationError as exc:
        raise translate_error(exc) from exc


@router.get(
    "/automation-executions",
    response_model=list[AutomationExecutionResponse],
)
def list_automation_executions(
    rule_id: int | None = Query(default=None, gt=0),
    lead_id: int | None = Query(default=None, gt=0),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return automation_repository.list_executions(
        db=db,
        rule_id=rule_id,
        lead_id=lead_id,
        limit=limit,
    )


@router.put(
    "/leads/{lead_id}/automation-state",
    response_model=LeadAutomationStateResponse,
)
def update_lead_automation_state(
    lead_id: int,
    payload: LeadAutomationStateUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    return automation_repository.upsert_lead_automation_state(
        db=db,
        lead_id=lead_id,
        reply_received=payload.reply_received,
        automation_paused=payload.automation_paused,
        pause_reason=payload.pause_reason,
        user_id=user.id,
    )


@router.post(
    "/internal/automation/run",
    response_model=AutomationRunResult,
)
def run_automation_rules(
    payload: AutomationRunRequest,
    x_worker_token: str | None = Header(
        default=None,
        alias="X-Worker-Token",
    ),
    db: Session = Depends(get_db),
):
    require_worker_token(x_worker_token)

    try:
        return automation_service.run_active_rules(
            db=db,
            rule_id=payload.rule_id,
            batch_size=payload.batch_size,
        )
    except automation_service.AutomationError as exc:
        raise translate_error(exc) from exc
