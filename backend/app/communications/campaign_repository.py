"""Database access for draft campaigns, steps, and recipients."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications.campaign_schemas import (
    CampaignCreate,
    CampaignStepCreate,
    CampaignUpdate,
)


_CAMPAIGN_FIELDS = (
    "id",
    "name",
    "description",
    "campaign_type",
    "sender_identity_id",
    "status",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)

_STEP_FIELDS = (
    "id",
    "campaign_id",
    "step_order",
    "delay_days",
    "template_id",
    "subject_override",
    "body_text_override",
    "created_at",
    "updated_at",
)

_RECIPIENT_FIELDS = (
    "id",
    "campaign_id",
    "lead_id",
    "recipient_email",
    "recipient_name",
    "status",
    "suppression_reason",
    "enrolled_by",
    "enrolled_at",
    "created_at",
    "updated_at",
)

_CAMPAIGN_SELECT = """
    SELECT
        c.id,
        c.name,
        c.description,
        c.campaign_type,
        c.sender_identity_id,
        c.status,
        c.created_by,
        c.updated_by,
        c.created_at,
        c.updated_at
    FROM campaigns c
"""

_STEP_SELECT = """
    SELECT
        s.id,
        s.campaign_id,
        s.step_order,
        s.delay_days,
        s.template_id,
        s.subject_override,
        s.body_text_override,
        s.created_at,
        s.updated_at
    FROM campaign_steps s
"""

_RECIPIENT_SELECT = """
    SELECT
        r.id,
        r.campaign_id,
        r.lead_id,
        r.recipient_email,
        r.recipient_name,
        r.status,
        r.suppression_reason,
        r.enrolled_by,
        r.enrolled_at,
        r.created_at,
        r.updated_at
    FROM campaign_recipients r
"""


class CampaignConflict(Exception):
    """Raised when a campaign uniqueness rule is violated."""


def _row_to_dict(
    row: Any,
    fields: tuple[str, ...],
) -> dict[str, Any] | None:
    if row is None:
        return None

    if isinstance(row, dict):
        return dict(row)

    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)

    return {
        field: getattr(row, field, None)
        for field in fields
    }


def list_campaigns(
    *,
    db: Session,
    status_filter: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    where_clause = ""

    if status_filter:
        where_clause = "WHERE c.status = :status"
        params["status"] = status_filter

    rows = db.execute(
        text(
            f"""
            {_CAMPAIGN_SELECT}
            {where_clause}
            ORDER BY c.created_at DESC, c.id DESC
            """
        ),
        params,
    ).fetchall()

    return [
        _row_to_dict(row, _CAMPAIGN_FIELDS)
        for row in rows
        if row is not None
    ]


def get_campaign(
    *,
    db: Session,
    campaign_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""
            {_CAMPAIGN_SELECT}
            WHERE c.id = :campaign_id
            """
        ),
        {"campaign_id": campaign_id},
    ).fetchone()

    return _row_to_dict(row, _CAMPAIGN_FIELDS)


def get_sender_identity(
    *,
    db: Session,
    sender_identity_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                display_name,
                email_address,
                reply_to_address,
                provider,
                is_active
            FROM sender_identities
            WHERE id = :sender_identity_id
            """
        ),
        {"sender_identity_id": sender_identity_id},
    ).fetchone()

    if row is None:
        return None

    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)

    return {
        "id": getattr(row, "id", None),
        "display_name": getattr(row, "display_name", None),
        "email_address": getattr(row, "email_address", None),
        "reply_to_address": getattr(
            row,
            "reply_to_address",
            None,
        ),
        "provider": getattr(row, "provider", None),
        "is_active": getattr(row, "is_active", False),
    }


def create_campaign(
    *,
    db: Session,
    payload: CampaignCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    try:
        row = db.execute(
            text(
                """
                INSERT INTO campaigns (
                    name,
                    description,
                    campaign_type,
                    sender_identity_id,
                    status,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :name,
                    :description,
                    :campaign_type,
                    :sender_identity_id,
                    'draft',
                    :created_by,
                    :updated_by,
                    NOW(),
                    NOW()
                )
                RETURNING
                    id,
                    name,
                    description,
                    campaign_type,
                    sender_identity_id,
                    status,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                """
            ),
            {
                "name": payload.name,
                "description": payload.description,
                "campaign_type": payload.campaign_type,
                "sender_identity_id": (
                    payload.sender_identity_id
                ),
                "created_by": user.id,
                "updated_by": user.id,
            },
        ).fetchone()

        db.commit()
        return (
            _row_to_dict(row, _CAMPAIGN_FIELDS)
            or {}
        )

    except IntegrityError as exc:
        db.rollback()
        raise CampaignConflict(
            "Campaign could not be created"
        ) from exc


def update_campaign(
    *,
    db: Session,
    campaign_id: int,
    payload: CampaignUpdate,
    user: CurrentUser,
) -> dict[str, Any] | None:
    current = get_campaign(
        db=db,
        campaign_id=campaign_id,
    )

    if current is None:
        return None

    changes = payload.model_dump(exclude_unset=True)

    row = db.execute(
        text(
            """
            UPDATE campaigns
            SET
                name = :name,
                description = :description,
                sender_identity_id = :sender_identity_id,
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :campaign_id
            RETURNING
                id,
                name,
                description,
                campaign_type,
                sender_identity_id,
                status,
                created_by,
                updated_by,
                created_at,
                updated_at
            """
        ),
        {
            "name": changes.get(
                "name",
                current["name"],
            ),
            "description": changes.get(
                "description",
                current["description"],
            ),
            "sender_identity_id": changes.get(
                "sender_identity_id",
                current["sender_identity_id"],
            ),
            "updated_by": user.id,
            "campaign_id": campaign_id,
        },
    ).fetchone()

    db.commit()
    return _row_to_dict(row, _CAMPAIGN_FIELDS)


def get_template(
    *,
    db: Session,
    template_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                template_type,
                subject,
                body_text,
                is_active
            FROM communication_templates
            WHERE id = :template_id
            """
        ),
        {"template_id": template_id},
    ).fetchone()

    if row is None:
        return None

    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)

    return {
        "id": getattr(row, "id", None),
        "template_type": getattr(
            row,
            "template_type",
            None,
        ),
        "subject": getattr(row, "subject", None),
        "body_text": getattr(row, "body_text", None),
        "is_active": getattr(row, "is_active", False),
    }


def step_order_exists(
    *,
    db: Session,
    campaign_id: int,
    step_order: int,
) -> bool:
    row = db.execute(
        text(
            """
            SELECT TRUE AS exists
            FROM campaign_steps
            WHERE
                campaign_id = :campaign_id
                AND step_order = :step_order
            LIMIT 1
            """
        ),
        {
            "campaign_id": campaign_id,
            "step_order": step_order,
        },
    ).fetchone()

    return bool(row)


def add_campaign_step(
    *,
    db: Session,
    campaign_id: int,
    payload: CampaignStepCreate,
) -> dict[str, Any]:
    try:
        row = db.execute(
            text(
                """
                INSERT INTO campaign_steps (
                    campaign_id,
                    step_order,
                    delay_days,
                    template_id,
                    subject_override,
                    body_text_override,
                    created_at,
                    updated_at
                )
                VALUES (
                    :campaign_id,
                    :step_order,
                    :delay_days,
                    :template_id,
                    :subject_override,
                    :body_text_override,
                    NOW(),
                    NOW()
                )
                RETURNING
                    id,
                    campaign_id,
                    step_order,
                    delay_days,
                    template_id,
                    subject_override,
                    body_text_override,
                    created_at,
                    updated_at
                """
            ),
            {
                "campaign_id": campaign_id,
                "step_order": payload.step_order,
                "delay_days": payload.delay_days,
                "template_id": payload.template_id,
                "subject_override": (
                    payload.subject_override
                ),
                "body_text_override": (
                    payload.body_text_override
                ),
            },
        ).fetchone()

        db.commit()
        return _row_to_dict(row, _STEP_FIELDS) or {}

    except IntegrityError as exc:
        db.rollback()
        raise CampaignConflict(
            "Campaign step order already exists"
        ) from exc


def list_campaign_steps(
    *,
    db: Session,
    campaign_id: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            f"""
            {_STEP_SELECT}
            WHERE s.campaign_id = :campaign_id
            ORDER BY s.step_order
            """
        ),
        {"campaign_id": campaign_id},
    ).fetchall()

    return [
        _row_to_dict(row, _STEP_FIELDS)
        for row in rows
        if row is not None
    ]


def get_leads_by_ids(
    *,
    db: Session,
    lead_ids: list[int],
) -> dict[int, dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                id,
                name,
                contact_person,
                owner_name,
                email
            FROM leads
            WHERE id = ANY(:lead_ids)
            """
        ),
        {"lead_ids": lead_ids},
    ).fetchall()

    result: dict[int, dict[str, Any]] = {}

    for row in rows:
        if getattr(row, "_mapping", None) is not None:
            item = dict(row._mapping)
        else:
            item = {
                "id": getattr(row, "id", None),
                "name": getattr(row, "name", None),
                "contact_person": getattr(
                    row,
                    "contact_person",
                    None,
                ),
                "owner_name": getattr(
                    row,
                    "owner_name",
                    None,
                ),
                "email": getattr(row, "email", None),
            }

        result[item["id"]] = item

    return result


def is_recipient_suppressed(
    *,
    db: Session,
    email_address: str,
) -> bool:
    normalized = email_address.strip().lower()

    row = db.execute(
        text(
            """
            SELECT TRUE AS suppressed
            FROM suppression_list
            WHERE
                LOWER(email_address) = :email_address
                AND is_active = TRUE
            LIMIT 1
            """
        ),
        {"email_address": normalized},
    ).fetchone()

    return bool(row)


def recipient_exists(
    *,
    db: Session,
    campaign_id: int,
    lead_id: int,
    email_address: str,
) -> bool:
    normalized = email_address.strip().lower()

    row = db.execute(
        text(
            """
            SELECT TRUE AS exists
            FROM campaign_recipients
            WHERE
                campaign_id = :campaign_id
                AND (
                    lead_id = :lead_id
                    OR LOWER(recipient_email) = :email_address
                )
            LIMIT 1
            """
        ),
        {
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "email_address": normalized,
        },
    ).fetchone()

    return bool(row)


def enroll_recipient(
    *,
    db: Session,
    campaign_id: int,
    lead_id: int,
    recipient_email: str,
    recipient_name: str | None,
    enrolled_by: int,
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO campaign_recipients (
                campaign_id,
                lead_id,
                recipient_email,
                recipient_name,
                status,
                suppression_reason,
                enrolled_by,
                enrolled_at,
                created_at,
                updated_at
            )
            VALUES (
                :campaign_id,
                :lead_id,
                :recipient_email,
                :recipient_name,
                'enrolled',
                NULL,
                :enrolled_by,
                NOW(),
                NOW(),
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "campaign_id": campaign_id,
            "lead_id": lead_id,
            "recipient_email": (
                recipient_email.strip().lower()
            ),
            "recipient_name": recipient_name,
            "enrolled_by": enrolled_by,
        },
    ).fetchone()

    return int(row.id)


def list_campaign_recipients(
    *,
    db: Session,
    campaign_id: int,
    status_filter: str | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "campaign_id": campaign_id,
    }
    status_clause = ""

    if status_filter:
        status_clause = "AND r.status = :status"
        params["status"] = status_filter

    rows = db.execute(
        text(
            f"""
            {_RECIPIENT_SELECT}
            WHERE r.campaign_id = :campaign_id
            {status_clause}
            ORDER BY r.enrolled_at DESC, r.id DESC
            """
        ),
        params,
    ).fetchall()

    return [
        _row_to_dict(row, _RECIPIENT_FIELDS)
        for row in rows
        if row is not None
    ]
