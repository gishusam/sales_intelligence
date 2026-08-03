"""Persistence for follow-up rules, draft generation, and execution history."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications.automation_schemas import (
    AutomationRuleCreate,
    AutomationRuleUpdate,
)


_RULE_FIELDS = (
    "id",
    "name",
    "trigger_type",
    "template_id",
    "sender_identity_id",
    "inactivity_days",
    "max_drafts_per_lead",
    "is_active",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)


class AutomationConflict(Exception):
    pass


def _row_to_dict(
    row: Any,
    fields: tuple[str, ...] | None = None,
) -> dict[str, Any] | None:
    if row is None:
        return None

    if isinstance(row, dict):
        return dict(row)

    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)

    if fields is None:
        return dict(vars(row))

    return {
        field: getattr(row, field, None)
        for field in fields
    }


def list_rules(
    *,
    db: Session,
    include_inactive: bool,
) -> list[dict[str, Any]]:
    where_clause = (
        ""
        if include_inactive
        else "WHERE is_active = TRUE"
    )

    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                name,
                trigger_type,
                template_id,
                sender_identity_id,
                inactivity_days,
                max_drafts_per_lead,
                is_active,
                created_by,
                updated_by,
                created_at,
                updated_at
            FROM automation_rules
            {where_clause}
            ORDER BY created_at DESC, id DESC
            """
        ),
        {},
    ).fetchall()

    return [
        _row_to_dict(row, _RULE_FIELDS)
        for row in rows
        if row is not None
    ]


def get_rule(
    *,
    db: Session,
    rule_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                name,
                trigger_type,
                template_id,
                sender_identity_id,
                inactivity_days,
                max_drafts_per_lead,
                is_active,
                created_by,
                updated_by,
                created_at,
                updated_at
            FROM automation_rules
            WHERE id = :rule_id
            """
        ),
        {"rule_id": rule_id},
    ).fetchone()

    return _row_to_dict(row, _RULE_FIELDS)


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
                body_html,
                version,
                is_active
            FROM communication_templates
            WHERE id = :template_id
            """
        ),
        {"template_id": template_id},
    ).fetchone()

    return _row_to_dict(row)


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

    return _row_to_dict(row)


def create_rule(
    *,
    db: Session,
    payload: AutomationRuleCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    try:
        row = db.execute(
            text(
                """
                INSERT INTO automation_rules (
                    name,
                    trigger_type,
                    template_id,
                    sender_identity_id,
                    inactivity_days,
                    max_drafts_per_lead,
                    is_active,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :name,
                    :trigger_type,
                    :template_id,
                    :sender_identity_id,
                    :inactivity_days,
                    :max_drafts_per_lead,
                    :is_active,
                    :created_by,
                    :updated_by,
                    NOW(),
                    NOW()
                )
                RETURNING
                    id,
                    name,
                    trigger_type,
                    template_id,
                    sender_identity_id,
                    inactivity_days,
                    max_drafts_per_lead,
                    is_active,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                """
            ),
            {
                **payload.model_dump(),
                "created_by": user.id,
                "updated_by": user.id,
            },
        ).fetchone()

        db.commit()
        return _row_to_dict(row, _RULE_FIELDS) or {}

    except IntegrityError as exc:
        db.rollback()
        raise AutomationConflict(
            "Automation rule could not be created"
        ) from exc


def update_rule(
    *,
    db: Session,
    rule_id: int,
    payload: AutomationRuleUpdate,
    user: CurrentUser,
) -> dict[str, Any] | None:
    current = get_rule(db=db, rule_id=rule_id)

    if current is None:
        return None

    changes = payload.model_dump(exclude_unset=True)

    row = db.execute(
        text(
            """
            UPDATE automation_rules
            SET
                name = :name,
                template_id = :template_id,
                sender_identity_id = :sender_identity_id,
                inactivity_days = :inactivity_days,
                max_drafts_per_lead = :max_drafts_per_lead,
                is_active = :is_active,
                updated_by = :updated_by,
                updated_at = NOW()
            WHERE id = :rule_id
            RETURNING
                id,
                name,
                trigger_type,
                template_id,
                sender_identity_id,
                inactivity_days,
                max_drafts_per_lead,
                is_active,
                created_by,
                updated_by,
                created_at,
                updated_at
            """
        ),
        {
            "rule_id": rule_id,
            "name": changes.get("name", current["name"]),
            "template_id": changes.get(
                "template_id",
                current["template_id"],
            ),
            "sender_identity_id": changes.get(
                "sender_identity_id",
                current["sender_identity_id"],
            ),
            "inactivity_days": changes.get(
                "inactivity_days",
                current["inactivity_days"],
            ),
            "max_drafts_per_lead": changes.get(
                "max_drafts_per_lead",
                current["max_drafts_per_lead"],
            ),
            "is_active": changes.get(
                "is_active",
                current["is_active"],
            ),
            "updated_by": user.id,
        },
    ).fetchone()

    db.commit()
    return _row_to_dict(row, _RULE_FIELDS)


def list_active_rules(
    *,
    db: Session,
    rule_id: int | None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    clause = "WHERE is_active = TRUE"

    if rule_id is not None:
        clause += " AND id = :rule_id"
        params["rule_id"] = rule_id

    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                name,
                trigger_type,
                template_id,
                sender_identity_id,
                inactivity_days,
                max_drafts_per_lead,
                is_active,
                created_by,
                updated_by,
                created_at,
                updated_at
            FROM automation_rules
            {clause}
            ORDER BY id
            """
        ),
        params,
    ).fetchall()

    return [
        _row_to_dict(row, _RULE_FIELDS)
        for row in rows
        if row is not None
    ]


def list_candidate_leads(
    *,
    db: Session,
    inactivity_days: int,
    batch_size: int,
    now,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                l.id,
                l.name,
                l.contact_person,
                l.owner_name,
                l.email,
                l.area,
                l.status,
                l.last_contacted,
                s.reply_received_at,
                COALESCE(s.automation_paused, FALSE)
                    AS automation_paused
            FROM leads l
            LEFT JOIN lead_communication_state s
                ON s.lead_id = l.id
            WHERE
                l.email IS NOT NULL
                AND BTRIM(l.email) <> ''
                AND (
                    l.last_contacted IS NULL
                    OR l.last_contacted <= (
                        :now
                        - (:inactivity_days * INTERVAL '1 day')
                    )
                )
            ORDER BY
                l.last_contacted NULLS FIRST,
                l.id
            LIMIT :batch_size
            """
        ),
        {
            "inactivity_days": inactivity_days,
            "batch_size": batch_size,
            "now": now,
        },
    ).fetchall()

    return [
        _row_to_dict(row)
        for row in rows
        if row is not None
    ]


def is_suppressed(
    *,
    db: Session,
    email_address: str,
) -> bool:
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
        {
            "email_address": (
                email_address.strip().lower()
            )
        },
    ).fetchone()

    return bool(row)


def count_rule_drafts_for_lead(
    *,
    db: Session,
    rule_id: int,
    lead_id: int,
) -> int:
    row = db.execute(
        text(
            """
            SELECT COUNT(*) AS count
            FROM email_messages
            WHERE
                automation_rule_id = :rule_id
                AND lead_id = :lead_id
                AND message_type = 'automation'
                AND status IN ('draft', 'queued', 'sent')
            """
        ),
        {
            "rule_id": rule_id,
            "lead_id": lead_id,
        },
    ).fetchone()

    return int(getattr(row, "count", 0) or 0)


def find_execution_by_key(
    *,
    db: Session,
    idempotency_key: str,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT
                id,
                rule_id,
                lead_id,
                email_message_id,
                idempotency_key,
                status,
                reason,
                executed_at,
                created_at
            FROM automation_executions
            WHERE idempotency_key = :idempotency_key
            """
        ),
        {"idempotency_key": idempotency_key},
    ).fetchone()

    return _row_to_dict(row)


def create_automation_draft(
    *,
    db: Session,
    automation_rule_id: int,
    lead_id: int,
    template_id: int,
    sender_identity_id: int,
    sent_by: int | None,
    recipient_email: str,
    recipient_name: str | None,
    subject: str,
    body_text: str,
    body_html: str | None,
    idempotency_key: str,
    status: str,
    message_type: str,
) -> int | None:
    row = db.execute(
        text(
            """
            INSERT INTO email_messages (
                automation_rule_id,
                lead_id,
                template_id,
                sender_identity_id,
                sent_by,
                recipient_email,
                recipient_name,
                subject,
                body_text,
                body_html,
                message_type,
                status,
                idempotency_key,
                created_at,
                updated_at
            )
            VALUES (
                :automation_rule_id,
                :lead_id,
                :template_id,
                :sender_identity_id,
                :sent_by,
                :recipient_email,
                :recipient_name,
                :subject,
                :body_text,
                :body_html,
                'automation',
                'draft',
                :idempotency_key,
                NOW(),
                NOW()
            )
            ON CONFLICT (idempotency_key)
                WHERE idempotency_key IS NOT NULL
                DO NOTHING
            RETURNING id
            """
        ),
        {
            "automation_rule_id": automation_rule_id,
            "lead_id": lead_id,
            "template_id": template_id,
            "sender_identity_id": sender_identity_id,
            "sent_by": sent_by,
            "recipient_email": (
                recipient_email.strip().lower()
            ),
            "recipient_name": recipient_name,
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "idempotency_key": idempotency_key,
        },
    ).fetchone()

    return None if row is None else int(row.id)


def record_execution(
    *,
    db: Session,
    rule_id: int,
    lead_id: int,
    email_message_id: int | None,
    idempotency_key: str,
    status: str,
    reason: str | None,
    executed_at,
) -> None:
    db.execute(
        text(
            """
            INSERT INTO automation_executions (
                rule_id,
                lead_id,
                email_message_id,
                idempotency_key,
                status,
                reason,
                executed_at,
                created_at
            )
            VALUES (
                :rule_id,
                :lead_id,
                :email_message_id,
                :idempotency_key,
                :status,
                :reason,
                :executed_at,
                NOW()
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            """
        ),
        {
            "rule_id": rule_id,
            "lead_id": lead_id,
            "email_message_id": email_message_id,
            "idempotency_key": idempotency_key,
            "status": status,
            "reason": reason,
            "executed_at": executed_at,
        },
    )


def list_executions(
    *,
    db: Session,
    rule_id: int | None,
    lead_id: int | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {"limit": limit}

    if rule_id is not None:
        clauses.append("rule_id = :rule_id")
        params["rule_id"] = rule_id

    if lead_id is not None:
        clauses.append("lead_id = :lead_id")
        params["lead_id"] = lead_id

    where_clause = (
        "WHERE " + " AND ".join(clauses)
        if clauses
        else ""
    )

    rows = db.execute(
        text(
            f"""
            SELECT
                id,
                rule_id,
                lead_id,
                email_message_id,
                idempotency_key,
                status,
                reason,
                executed_at,
                created_at
            FROM automation_executions
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        params,
    ).fetchall()

    return [
        _row_to_dict(row)
        for row in rows
        if row is not None
    ]


def upsert_lead_automation_state(
    *,
    db: Session,
    lead_id: int,
    reply_received: bool | None,
    automation_paused: bool | None,
    pause_reason: str | None,
    user_id: int,
) -> dict[str, Any]:
    row = db.execute(
        text(
            """
            INSERT INTO lead_communication_state (
                lead_id,
                reply_received_at,
                automation_paused,
                pause_reason,
                updated_by,
                updated_at
            )
            VALUES (
                :lead_id,
                CASE
                    WHEN :reply_received IS TRUE
                    THEN NOW()
                    ELSE NULL
                END,
                COALESCE(:automation_paused, FALSE),
                :pause_reason,
                :updated_by,
                NOW()
            )
            ON CONFLICT (lead_id)
            DO UPDATE SET
                reply_received_at = CASE
                    WHEN :reply_received IS NULL
                    THEN lead_communication_state.reply_received_at
                    WHEN :reply_received IS TRUE
                    THEN NOW()
                    ELSE NULL
                END,
                automation_paused = COALESCE(
                    :automation_paused,
                    lead_communication_state.automation_paused
                ),
                pause_reason = CASE
                    WHEN :automation_paused IS FALSE
                    THEN NULL
                    WHEN :pause_reason IS NOT NULL
                    THEN :pause_reason
                    ELSE lead_communication_state.pause_reason
                END,
                updated_by = :updated_by,
                updated_at = NOW()
            RETURNING
                lead_id,
                reply_received_at,
                automation_paused,
                pause_reason,
                updated_by,
                updated_at
            """
        ),
        {
            "lead_id": lead_id,
            "reply_received": reply_received,
            "automation_paused": automation_paused,
            "pause_reason": pause_reason,
            "updated_by": user_id,
        },
    ).fetchone()

    db.commit()
    return _row_to_dict(row) or {}
