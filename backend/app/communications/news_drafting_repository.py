"""Persistence for sources, articles, ingestion, and generated drafts."""

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications.news_drafting_schemas import (
    NewsSourceCreate,
    NewsSourceUpdate,
)


_SOURCE_FIELDS = (
    "id",
    "name",
    "source_type",
    "url",
    "publisher",
    "trust_tier",
    "is_active",
    "created_by",
    "updated_by",
    "created_at",
    "updated_at",
)


class NewsDraftingConflict(Exception):
    pass


def row_dict(
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


def list_sources(
    *,
    db: Session,
    include_inactive: bool,
) -> list[dict[str, Any]]:
    clause = (
        ""
        if include_inactive
        else "WHERE is_active = TRUE"
    )
    rows = db.execute(
        text(
            f"""
            SELECT {", ".join(_SOURCE_FIELDS)}
            FROM newsletter_sources
            {clause}
            ORDER BY name, id
            """
        ),
        {},
    ).fetchall()

    return [
        row_dict(row, _SOURCE_FIELDS)
        for row in rows
        if row is not None
    ]


def get_source(
    *,
    db: Session,
    source_id: int,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            f"""
            SELECT {", ".join(_SOURCE_FIELDS)}
            FROM newsletter_sources
            WHERE id = :source_id
            """
        ),
        {"source_id": source_id},
    ).fetchone()

    return row_dict(row, _SOURCE_FIELDS)


def create_source(
    *,
    db: Session,
    payload: NewsSourceCreate,
    user: CurrentUser,
) -> dict[str, Any]:
    try:
        row = db.execute(
            text(
                f"""
                INSERT INTO newsletter_sources (
                    name,
                    source_type,
                    url,
                    publisher,
                    trust_tier,
                    is_active,
                    created_by,
                    updated_by,
                    created_at,
                    updated_at
                )
                VALUES (
                    :name,
                    :source_type,
                    :url,
                    :publisher,
                    :trust_tier,
                    :is_active,
                    :created_by,
                    :updated_by,
                    NOW(),
                    NOW()
                )
                RETURNING {", ".join(_SOURCE_FIELDS)}
                """
            ),
            {
                **payload.model_dump(),
                "created_by": user.id,
                "updated_by": user.id,
            },
        ).fetchone()
        db.commit()
        return row_dict(row, _SOURCE_FIELDS) or {}
    except IntegrityError as exc:
        db.rollback()
        raise NewsDraftingConflict(
            "A news source with that URL already exists"
        ) from exc


def update_source(
    *,
    db: Session,
    source_id: int,
    payload: NewsSourceUpdate,
    user: CurrentUser,
) -> dict[str, Any] | None:
    current = get_source(db=db, source_id=source_id)

    if current is None:
        return None

    changes = payload.model_dump(exclude_unset=True)

    try:
        row = db.execute(
            text(
                f"""
                UPDATE newsletter_sources
                SET
                    name = :name,
                    source_type = :source_type,
                    url = :url,
                    publisher = :publisher,
                    trust_tier = :trust_tier,
                    is_active = :is_active,
                    updated_by = :updated_by,
                    updated_at = NOW()
                WHERE id = :source_id
                RETURNING {", ".join(_SOURCE_FIELDS)}
                """
            ),
            {
                "source_id": source_id,
                "name": changes.get("name", current["name"]),
                "source_type": changes.get(
                    "source_type",
                    current["source_type"],
                ),
                "url": changes.get("url", current["url"]),
                "publisher": changes.get(
                    "publisher",
                    current["publisher"],
                ),
                "trust_tier": changes.get(
                    "trust_tier",
                    current["trust_tier"],
                ),
                "is_active": changes.get(
                    "is_active",
                    current["is_active"],
                ),
                "updated_by": user.id,
            },
        ).fetchone()
        db.commit()
        return row_dict(row, _SOURCE_FIELDS)
    except IntegrityError as exc:
        db.rollback()
        raise NewsDraftingConflict(
            "A news source with that URL already exists"
        ) from exc


def find_article_by_identity(
    *,
    db: Session,
    source_id: int,
    canonical_url: str,
    fingerprint: str,
    external_id: str | None,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            SELECT id, canonical_url, fingerprint
            FROM newsletter_articles
            WHERE
                fingerprint = :fingerprint
                OR LOWER(canonical_url) = :canonical_url
                OR (
                    source_id = :source_id
                    AND external_id IS NOT NULL
                    AND external_id = :external_id
                )
            LIMIT 1
            """
        ),
        {
            "source_id": source_id,
            "canonical_url": canonical_url.strip().lower(),
            "fingerprint": fingerprint,
            "external_id": external_id,
        },
    ).fetchone()

    return row_dict(row)


def create_article(
    *,
    db: Session,
    source_id: int,
    external_id: str | None,
    title: str,
    canonical_url: str,
    publisher: str,
    published_at,
    summary: str | None,
    content_text: str | None,
    fingerprint: str,
    metadata: dict[str, Any],
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO newsletter_articles (
                source_id,
                external_id,
                title,
                canonical_url,
                publisher,
                published_at,
                summary,
                content_text,
                fingerprint,
                status,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                :source_id,
                :external_id,
                :title,
                :canonical_url,
                :publisher,
                :published_at,
                :summary,
                :content_text,
                :fingerprint,
                'new',
                CAST(:metadata AS JSONB),
                NOW(),
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "source_id": source_id,
            "external_id": external_id,
            "title": title,
            "canonical_url": canonical_url.strip(),
            "publisher": publisher,
            "published_at": published_at,
            "summary": summary,
            "content_text": content_text,
            "fingerprint": fingerprint,
            "metadata": json.dumps(metadata),
        },
    ).fetchone()

    return int(row.id)


def record_ingestion_run(
    *,
    db: Session,
    source_id: int,
    received_count: int,
    created_count: int,
    duplicate_count: int,
    status: str,
    error_message: str | None,
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO newsletter_ingestion_runs (
                source_id,
                received_count,
                created_count,
                duplicate_count,
                status,
                error_message,
                started_at,
                completed_at,
                created_at
            )
            VALUES (
                :source_id,
                :received_count,
                :created_count,
                :duplicate_count,
                :status,
                :error_message,
                NOW(),
                NOW(),
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "source_id": source_id,
            "received_count": received_count,
            "created_count": created_count,
            "duplicate_count": duplicate_count,
            "status": status,
            "error_message": error_message,
        },
    ).fetchone()

    return int(row.id)


def list_articles(
    *,
    db: Session,
    source_id: int | None,
    status_filter: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {"limit": limit}

    if source_id is not None:
        clauses.append("source_id = :source_id")
        params["source_id"] = source_id

    if status_filter is not None:
        clauses.append("status = :status")
        params["status"] = status_filter

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
                source_id,
                external_id,
                title,
                canonical_url,
                publisher,
                published_at,
                summary,
                content_text,
                fingerprint,
                status,
                metadata,
                created_at,
                updated_at
            FROM newsletter_articles
            {where_clause}
            ORDER BY published_at DESC, id DESC
            LIMIT :limit
            """
        ),
        params,
    ).fetchall()

    return [
        row_dict(row)
        for row in rows
        if row is not None
    ]


def update_article_status(
    *,
    db: Session,
    article_id: int,
    status: str,
) -> dict[str, Any] | None:
    row = db.execute(
        text(
            """
            UPDATE newsletter_articles
            SET
                status = :status,
                updated_at = NOW()
            WHERE id = :article_id
            RETURNING
                id,
                source_id,
                title,
                canonical_url,
                publisher,
                published_at,
                summary,
                status,
                created_at,
                updated_at
            """
        ),
        {"article_id": article_id, "status": status},
    ).fetchone()

    db.commit()
    return row_dict(row)


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

    return row_dict(row)


def get_articles_by_ids(
    *,
    db: Session,
    article_ids: list[int],
) -> dict[int, dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                id,
                source_id,
                external_id,
                title,
                canonical_url,
                publisher,
                published_at,
                summary,
                content_text,
                fingerprint,
                status,
                metadata,
                created_at,
                updated_at
            FROM newsletter_articles
            WHERE id = ANY(:article_ids)
            """
        ),
        {"article_ids": article_ids},
    ).fetchall()

    result = {}

    for row in rows:
        item = row_dict(row) or {}
        result[item["id"]] = item

    return result


def create_generation_run(
    *,
    db: Session,
    article_ids: list[int],
    generator_model: str,
    prompt_hash: str,
    requested_by: int,
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO newsletter_generation_runs (
                article_ids,
                generator_model,
                prompt_hash,
                status,
                requested_by,
                started_at,
                created_at
            )
            VALUES (
                :article_ids,
                :generator_model,
                :prompt_hash,
                'running',
                :requested_by,
                NOW(),
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "article_ids": article_ids,
            "generator_model": generator_model,
            "prompt_hash": prompt_hash,
            "requested_by": requested_by,
        },
    ).fetchone()

    return int(row.id)


def create_generated_newsletter(
    *,
    db: Session,
    name: str,
    subject: str,
    preview_text: str | None,
    sender_identity_id: int,
    blocks: list[dict[str, Any]],
    body_text: str,
    body_html: str,
    generation_run_id: int,
    source_type: str,
    status: str,
    created_by: int,
    updated_by: int,
    approved_by: None,
    scheduled_for: None,
) -> int:
    row = db.execute(
        text(
            """
            INSERT INTO newsletter_drafts (
                name,
                subject,
                preview_text,
                status,
                sender_identity_id,
                blocks,
                body_text,
                body_html,
                generation_run_id,
                source_type,
                created_by,
                updated_by,
                approved_by,
                scheduled_for,
                created_at,
                updated_at
            )
            VALUES (
                :name,
                :subject,
                :preview_text,
                'draft',
                :sender_identity_id,
                CAST(:blocks AS JSONB),
                :body_text,
                :body_html,
                :generation_run_id,
                :source_type,
                :created_by,
                :updated_by,
                NULL,
                NULL,
                NOW(),
                NOW()
            )
            RETURNING id
            """
        ),
        {
            "name": name,
            "subject": subject,
            "preview_text": preview_text,
            "sender_identity_id": sender_identity_id,
            "blocks": json.dumps(blocks),
            "body_text": body_text,
            "body_html": body_html,
            "generation_run_id": generation_run_id,
            "source_type": source_type,
            "created_by": created_by,
            "updated_by": updated_by,
        },
    ).fetchone()

    return int(row.id)


def link_draft_articles(
    *,
    db: Session,
    newsletter_id: int,
    article_ids: list[int],
) -> None:
    for position, article_id in enumerate(article_ids, start=1):
        db.execute(
            text(
                """
                INSERT INTO newsletter_draft_articles (
                    newsletter_id,
                    article_id,
                    position,
                    created_at
                )
                VALUES (
                    :newsletter_id,
                    :article_id,
                    :position,
                    NOW()
                )
                ON CONFLICT (
                    newsletter_id,
                    article_id
                ) DO NOTHING
                """
            ),
            {
                "newsletter_id": newsletter_id,
                "article_id": article_id,
                "position": position,
            },
        )


def mark_articles_used(
    *,
    db: Session,
    article_ids: list[int],
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_articles
            SET
                status = 'used',
                updated_at = NOW()
            WHERE id = ANY(:article_ids)
            """
        ),
        {"article_ids": article_ids},
    )


def complete_generation_run(
    *,
    db: Session,
    generation_run_id: int,
    newsletter_id: int | None,
    status: str,
    error_message: str | None,
) -> None:
    db.execute(
        text(
            """
            UPDATE newsletter_generation_runs
            SET
                newsletter_id = :newsletter_id,
                status = :status,
                error_message = :error_message,
                completed_at = NOW()
            WHERE id = :generation_run_id
            """
        ),
        {
            "generation_run_id": generation_run_id,
            "newsletter_id": newsletter_id,
            "status": status,
            "error_message": error_message,
        },
    )


def list_generation_runs(
    *,
    db: Session,
    limit: int,
) -> list[dict[str, Any]]:
    rows = db.execute(
        text(
            """
            SELECT
                id,
                article_ids,
                newsletter_id,
                generator_model,
                prompt_hash,
                status,
                error_message,
                requested_by,
                started_at,
                completed_at,
                created_at
            FROM newsletter_generation_runs
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).fetchall()

    return [
        row_dict(row)
        for row in rows
        if row is not None
    ]
