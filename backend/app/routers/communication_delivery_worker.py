"""Protected endpoint for bounded Communications worker runs."""

import os

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.communications.delivery_worker import run_delivery_worker
from app.communications.delivery_worker_schemas import WorkerRunRequest, WorkerRunResult
from app.database import get_db


router = APIRouter(
    prefix="/api/communications/internal/delivery",
    tags=["communication-delivery-worker"],
)


def require_worker_token(supplied_token: str | None) -> None:
    expected = os.getenv("COMMUNICATIONS_WORKER_TOKEN", "")
    if not expected or supplied_token is None or supplied_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid worker token",
        )


@router.post("/run", response_model=WorkerRunResult)
def run_worker(
    payload: WorkerRunRequest,
    x_worker_token: str | None = Header(default=None, alias="X-Worker-Token"),
    db: Session = Depends(get_db),
):
    require_worker_token(x_worker_token)

    return run_delivery_worker(
        db=db,
        batch_size=payload.batch_size,
        max_attempts=int(os.getenv("COMMUNICATIONS_MAX_ATTEMPTS", "5")),
        lock_timeout_minutes=int(
            os.getenv("COMMUNICATIONS_LOCK_TIMEOUT_MINUTES", "15")
        ),
    )
