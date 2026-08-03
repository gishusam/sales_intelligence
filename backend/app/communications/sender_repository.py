"""Database access for outbound sender identities."""

from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications.schemas import SenderIdentityCreate, SenderIdentityUpdate


FIELDS = (
    "id", "display_name", "email_address", "reply_to_address", "provider",
    "provider_reference", "is_default", "is_active", "created_by",
    "updated_by", "created_at", "updated_at",
)

SELECT = """
    SELECT s.id, s.display_name, s.email_address, s.reply_to_address,
           s.provider, s.provider_reference, s.is_default, s.is_active,
           s.created_by, s.updated_by, s.created_at, s.updated_at
    FROM sender_identities s
"""


class SenderIdentityConflict(Exception):
    pass


def _dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    if getattr(row, "_mapping", None) is not None:
        return dict(row._mapping)
    return {field: getattr(row, field, None) for field in FIELDS}


def list_sender_identities(*, db: Session, include_inactive: bool):
    where = "" if include_inactive else "WHERE s.is_active = TRUE"
    rows = db.execute(
        text(f"{SELECT} {where} ORDER BY s.is_default DESC, s.display_name"),
        {},
    ).fetchall()
    return [_dict(row) for row in rows]


def get_sender_identity(*, db: Session, sender_id: int):
    row = db.execute(
        text(f"{SELECT} WHERE s.id = :sender_id"),
        {"sender_id": sender_id},
    ).fetchone()
    return _dict(row)


def _unset_default(*, db: Session, exclude_id: int | None = None):
    params = {}
    exclusion = ""
    if exclude_id is not None:
        exclusion = "AND id <> :exclude_id"
        params["exclude_id"] = exclude_id
    db.execute(
        text(
            "UPDATE sender_identities SET is_default = FALSE, updated_at = NOW() "
            f"WHERE is_default = TRUE {exclusion}"
        ),
        params,
    )


def create_sender_identity(*, db: Session, payload: SenderIdentityCreate, user: CurrentUser):
    try:
        if payload.is_default:
            _unset_default(db=db)
        row = db.execute(
            text("""
                INSERT INTO sender_identities (
                    display_name, email_address, reply_to_address, provider,
                    provider_reference, is_default, is_active, created_by, updated_by
                ) VALUES (
                    :display_name, :email_address, :reply_to_address, :provider,
                    :provider_reference, :is_default, :is_active, :created_by, :updated_by
                ) RETURNING id, display_name, email_address, reply_to_address,
                    provider, provider_reference, is_default, is_active,
                    created_by, updated_by, created_at, updated_at
            """),
            {
                **payload.model_dump(),
                "created_by": user.id,
                "updated_by": user.id,
            },
        ).fetchone()
        db.commit()
        return _dict(row) or {}
    except IntegrityError as exc:
        db.rollback()
        raise SenderIdentityConflict(
            f"Sender email '{payload.email_address}' already exists"
        ) from exc


def update_sender_identity(*, db: Session, sender_id: int, payload: SenderIdentityUpdate, user: CurrentUser):
    current = get_sender_identity(db=db, sender_id=sender_id)
    if current is None:
        return None

    merged = dict(current)
    merged.update(payload.model_dump(exclude_unset=True))
    if merged["is_default"] and not merged["is_active"]:
        raise ValueError("Default sender must be active")

    try:
        if merged["is_default"]:
            _unset_default(db=db, exclude_id=sender_id)
        row = db.execute(
            text("""
                UPDATE sender_identities SET
                    display_name = :display_name,
                    email_address = :email_address,
                    reply_to_address = :reply_to_address,
                    provider = :provider,
                    provider_reference = :provider_reference,
                    is_default = :is_default,
                    is_active = :is_active,
                    updated_by = :updated_by,
                    updated_at = NOW()
                WHERE id = :sender_id
                RETURNING id, display_name, email_address, reply_to_address,
                    provider, provider_reference, is_default, is_active,
                    created_by, updated_by, created_at, updated_at
            """),
            {
                "sender_id": sender_id,
                "display_name": merged["display_name"],
                "email_address": merged["email_address"],
                "reply_to_address": merged["reply_to_address"],
                "provider": merged["provider"],
                "provider_reference": merged["provider_reference"],
                "is_default": merged["is_default"],
                "is_active": merged["is_active"],
                "updated_by": user.id,
            },
        ).fetchone()
        db.commit()
        return _dict(row)
    except IntegrityError as exc:
        db.rollback()
        raise SenderIdentityConflict(
            f"Sender email '{merged['email_address']}' already exists"
        ) from exc
