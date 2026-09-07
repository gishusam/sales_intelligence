import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session
from svix.webhooks import Webhook, WebhookVerificationError

from app.config import settings
from app.database import get_db


router = APIRouter(
    prefix="/api/comms/webhooks",
    tags=["communications"],
)


TRACKED_EVENTS = {
    "email.delivered",
    "email.opened",
    "email.clicked",
    "email.bounced",
    "email.failed",
}


def _parse_event_time(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)

    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def process_resend_event(
    db: Session,
    svix_id: str,
    payload: dict,
) -> dict:
    event_type = payload.get("type", "")
    data = payload.get("data") or {}

    resend_id = data.get("email_id")
    event_time = _parse_event_time(
        payload.get("created_at")
    )

    # Store every webhook once.
    # Resend may retry delivery, so svix_id protects our counters.
    inserted = db.execute(
        text("""
            INSERT INTO resend_webhook_events (
                svix_id,
                event_type,
                resend_id,
                event_created_at,
                payload
            )
            VALUES (
                :svix_id,
                :event_type,
                :resend_id,
                :event_created_at,
                CAST(:payload AS JSONB)
            )
            ON CONFLICT (svix_id) DO NOTHING
            RETURNING id
        """),
        {
            "svix_id": svix_id,
            "event_type": event_type,
            "resend_id": resend_id,
            "event_created_at": event_time,
            "payload": json.dumps(payload),
        },
    ).fetchone()

    if inserted is None:
        return {
            "status": "duplicate",
            "event_type": event_type,
        }

    if event_type not in TRACKED_EVENTS:
        db.commit()
        return {
            "status": "ignored",
            "event_type": event_type,
        }

    if not resend_id:
        db.commit()
        return {
            "status": "ignored",
            "reason": "missing_email_id",
        }

    params = {
        "resend_id": resend_id,
        "event_time": event_time,
    }

    if event_type == "email.delivered":
        result = db.execute(
            text("""
                UPDATE campaign_recipients
                SET
                    delivered_at = LEAST(
                        COALESCE(delivered_at, :event_time),
                        :event_time
                    ),
                    status = CASE
                        WHEN status IN (
                            'bounced',
                            'failed',
                            'unsubscribed'
                        )
                        THEN status
                        ELSE 'delivered'
                    END,
                    last_event_at = GREATEST(
                        COALESCE(last_event_at, :event_time),
                        :event_time
                    )
                WHERE resend_id = :resend_id
            """),
            params,
        )

    elif event_type == "email.opened":
        result = db.execute(
            text("""
                UPDATE campaign_recipients
                SET
                    opened_at = LEAST(
                        COALESCE(opened_at, :event_time),
                        :event_time
                    ),
                    open_count = open_count + 1,
                    last_event_at = GREATEST(
                        COALESCE(last_event_at, :event_time),
                        :event_time
                    )
                WHERE resend_id = :resend_id
            """),
            params,
        )

    elif event_type == "email.clicked":
        result = db.execute(
            text("""
                UPDATE campaign_recipients
                SET
                    clicked_at = LEAST(
                        COALESCE(clicked_at, :event_time),
                        :event_time
                    ),
                    click_count = click_count + 1,
                    last_event_at = GREATEST(
                        COALESCE(last_event_at, :event_time),
                        :event_time
                    )
                WHERE resend_id = :resend_id
            """),
            params,
        )

    elif event_type == "email.bounced":
        reason = (
            data.get("bounce", {}).get("message")
            or "Email bounced"
        )

        result = db.execute(
            text("""
                UPDATE campaign_recipients
                SET
                    bounced_at = LEAST(
                        COALESCE(bounced_at, :event_time),
                        :event_time
                    ),
                    bounce_reason = :reason,
                    status = CASE
                        WHEN status = 'unsubscribed'
                        THEN status
                        ELSE 'bounced'
                    END,
                    last_event_at = GREATEST(
                        COALESCE(last_event_at, :event_time),
                        :event_time
                    )
                WHERE resend_id = :resend_id
            """),
            {
                **params,
                "reason": reason,
            },
        )

    else:  # email.failed
        reason = (
            data.get("failed", {}).get("reason")
            or "Email delivery failed"
        )

        result = db.execute(
            text("""
                UPDATE campaign_recipients
                SET
                    failed_at = LEAST(
                        COALESCE(failed_at, :event_time),
                        :event_time
                    ),
                    error = :reason,
                    status = CASE
                        WHEN status IN ('bounced', 'unsubscribed')
                        THEN status
                        ELSE 'failed'
                    END,
                    last_event_at = GREATEST(
                        COALESCE(last_event_at, :event_time),
                        :event_time
                    )
                WHERE resend_id = :resend_id
            """),
            {
                **params,
                "reason": reason,
            },
        )

    matched = result.rowcount > 0

    db.commit()

    return {
        "status": "processed" if matched else "unmatched",
        "event_type": event_type,
        "resend_id": resend_id,
    }


@router.post("/resend")
async def receive_resend_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    secret = settings.RESEND_WEBHOOK_SECRET.strip()

    if not secret:
        raise HTTPException(
            status_code=503,
            detail="Resend webhook secret is not configured",
        )

    raw_body = await request.body()

    headers = {
        "svix-id": request.headers.get("svix-id", ""),
        "svix-timestamp": request.headers.get(
            "svix-timestamp", ""
        ),
        "svix-signature": request.headers.get(
            "svix-signature", ""
        ),
    }

    if not all(headers.values()):
        raise HTTPException(
            status_code=400,
            detail="Missing Resend webhook headers",
        )

    try:
        Webhook(secret).verify(
            raw_body,
            headers,
        )
    except WebhookVerificationError:
        raise HTTPException(
            status_code=400,
            detail="Invalid Resend webhook signature",
        )

    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Invalid webhook JSON",
        )

    return process_resend_event(
        db=db,
        svix_id=headers["svix-id"],
        payload=payload,
    )