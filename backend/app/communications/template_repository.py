"""Database access for reusable communication templates."""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications.schemas import (
    TemplateCreate,
    TemplateUpdate,
)
from app.communications.templates import validate_template


_TEMPLATE_FIELDS = (
    "id",
    "slug",
    "name",
    "template_type",
    "channel",
    "subject",
    "body_text",
    "body_html",
    "required_placeholders",
    "version",
    "is_default",
    "is_active",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)

_TEMPLATE_SELECT = """
    SELECT
        t.id,
        t.slug,
        t.name,
        t.template_type,
        t.channel,
        t.subject,
        t.body_text,
        t.body_html,
        t.required_placeholders,
        t.version,
        t.is_default,
        t.is_active,
        t.created_by,
        t.updated_by,
        t.created_at,
        t.updated_at
    FROM communication_templates t
"""


class TemplateSlugConflict(Exception):
    """Raised when a template slug is already in use."""


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None

    if isinstance(row, dict):
        data = dict(row)
    elif getattr(row, "_mapping", None) is not None:
        data = dict(row._mapping)
    else:
        data = {
            field: getattr(row, field, None)
            for field in _TEMPLATE_FIELDS
        }

    placeholders = data.get("required_placeholders") or []

    if isinstance(placeholders, str):
        placeholders = json.loads(placeholders)

    data["required_placeholders"] = list(placeholders)
    return data


def list_templates(
    *,
    db: Session,
    template_type: str | None,
    include_inactive: bool,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: dict[str, Any] = {}

    if template_type:
        conditions.append(
            "t.template_type = :template_type"
        )
        params["template_type"] = template_type

    if not include_inactive:
        conditions.append("t.is_active = TRUE")

    where_clause = (
        f"WHERE {' AND '.join(conditions)}"
        if conditions
        else ""
    )

    rows = db.execute(
        text(
            f"""
            {_TEMPLATE_SELECT}
            {where_clause}
            ORDER BY
                t.template_type,
                t.is_default DESC,
                t.name
            """
        ),
        params,
    ).fetchall()

    return [
        _row_to_dict(row)
        for row in rows
        if row is not None
    ]


def get_template(
    *,
    db: Session,
    template_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""
            {_TEMPLATE_SELECT}
            WHERE t.id = :template_id
            """
        ),
        {"template_id": template_id},
    ).fetchone()

    return _row_to_dict(row)


def _unset_existing_default(
    *,
    db: Session,
    template_type: str,
    exclude_id: int | None = None,
) -> None:
    params: dict[str, Any] = {
        "template_type": template_type,
    }
    exclusion = ""

    if exclude_id is not None:
        exclusion = "AND id <> :exclude_id"
        params["exclude_id"] = exclude_id

    db.execute(
        text(
            f"""
            UPDATE communication_templates
            SET
                is_default = FALSE,
                updated_at = NOW()
            WHERE
                template_type = :template_type
                AND is_default = TRUE
                {exclusion}
            """
        ),
        params,
    )


def create_template(
    *,
    db: Session,
    payload: TemplateCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    placeholders = sorted(
        validate_template(
            template_type=payload.template_type,
            subject=payload.subject,
            body=payload.body_text,
        )
    )

    try:
        if payload.is_default:
            _unset_existing_default(
                db=db,
                template_type=payload.template_type,
            )

        row = db.execute(
            text(
                """
                INSERT INTO communication_templates (
                    slug,
                    name,
                    template_type,
                    channel,
                    subject,
                    body_text,
                    body_html,
                    required_placeholders,
                    version,
                    is_default,
                    is_active,
                    created_by,
                    updated_by
                )
                VALUES (
                    :slug,
                    :name,
                    :template_type,
                    'email',
                    :subject,
                    :body_text,
                    :body_html,
                    CAST(:required_placeholders AS JSONB),
                    1,
                    :is_default,
                    :is_active,
                    :created_by,
                    :updated_by
                )
                RETURNING
                    id,
                    slug,
                    name,
                    template_type,
                    channel,
                    subject,
                    body_text,
                    body_html,
                    required_placeholders,
                    version,
                    is_default,
                    is_active,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                """
            ),
            {
                "slug": payload.slug,
                "name": payload.name,
                "template_type": payload.template_type,
                "subject": payload.subject,
                "body_text": payload.body_text,
                "body_html": payload.body_html,
                "required_placeholders": json.dumps(
                    placeholders
                ),
                "is_default": payload.is_default,
                "is_active": payload.is_active,
                "created_by": user.id,
                "updated_by": user.id,
            },
        ).fetchone()

        db.commit()
        return _row_to_dict(row) or {}

    except IntegrityError as exc:
        db.rollback()
        raise TemplateSlugConflict(
            f"Template slug '{payload.slug}' already exists"
        ) from exc


def update_template(
    *,
    db: Session,
    template_id: int,
    payload: TemplateUpdate,
    user: CurrentUser,
) -> dict[str, Any] | None:
    current = get_template(
        db=db,
        template_id=template_id,
    )

    if current is None:
        return None

    changes = payload.model_dump(exclude_unset=True)

    merged = {
        "slug": changes.get("slug", current["slug"]),
        "name": changes.get("name", current["name"]),
        "template_type": changes.get(
            "template_type",
            current["template_type"],
        ),
        "subject": changes.get(
            "subject",
            current["subject"],
        ),
        "body_text": changes.get(
            "body_text",
            current["body_text"],
        ),
        "body_html": changes.get(
            "body_html",
            current["body_html"],
        ),
        "is_default": changes.get(
            "is_default",
            current["is_default"],
        ),
        "is_active": changes.get(
            "is_active",
            current["is_active"],
        ),
    }

    placeholders = sorted(
        validate_template(
            template_type=merged["template_type"],
            subject=merged["subject"],
            body=merged["body_text"],
        )
    )

    try:
        if merged["is_default"]:
            _unset_existing_default(
                db=db,
                template_type=merged["template_type"],
                exclude_id=template_id,
            )

        row = db.execute(
            text(
                """
                UPDATE communication_templates
                SET
                    slug = :slug,
                    name = :name,
                    template_type = :template_type,
                    subject = :subject,
                    body_text = :body_text,
                    body_html = :body_html,
                    required_placeholders =
                        CAST(:required_placeholders AS JSONB),
                    version = version + 1,
                    is_default = :is_default,
                    is_active = :is_active,
                    updated_by = :updated_by,
                    updated_at = NOW()
                WHERE id = :template_id
                RETURNING
                    id,
                    slug,
                    name,
                    template_type,
                    channel,
                    subject,
                    body_text,
                    body_html,
                    required_placeholders,
                    version,
                    is_default,
                    is_active,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                """
            ),
            {
                **merged,
                "required_placeholders": json.dumps(
                    placeholders
                ),
                "updated_by": user.id,
                "template_id": template_id,
            },
        ).fetchone()

        db.commit()
        return _row_to_dict(row)

    except IntegrityError as exc:
        db.rollback()
        raise TemplateSlugConflict(
            f"Template slug '{merged['slug']}' already exists"
        ) from exc
