"""Normalized ingestion and provenance-preserving AI draft generation."""

import hashlib
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.communications import news_drafting_repository as repository
from app.communications.news_drafting_generator import NewsDraftGenerator
from app.communications.news_drafting_schemas import (
    NewsDraftGenerationRequest,
    NewsIngestionRequest,
)
from app.communications.newsletter_renderer import render_blocks
from app.communications.newsletter_schemas import NewsletterBlock


class NewsDraftingError(ValueError):
    pass


class NewsDraftingNotFoundError(NewsDraftingError):
    pass


class NewsDraftingValidationError(NewsDraftingError):
    pass


class NewsDraftingConflictError(NewsDraftingError):
    pass


def normalize_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    normalized_path = parsed.path.rstrip("/") or "/"

    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            normalized_path,
            parsed.query,
            "",
        )
    )


def article_fingerprint(
    *,
    title: str,
    canonical_url: str,
    publisher: str,
    published_at,
) -> str:
    canonical = normalize_url(canonical_url)
    normalized = "|".join(
        [
            " ".join(title.lower().split()),
            canonical,
            " ".join(publisher.lower().split()),
            published_at.astimezone().isoformat(),
        ]
    )

    return hashlib.sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def ingest_articles(
    *,
    db: Session,
    payload: NewsIngestionRequest,
) -> dict[str, Any]:
    source = repository.get_source(
        db=db,
        source_id=payload.source_id,
    )

    if source is None:
        raise NewsDraftingNotFoundError(
            "News source not found"
        )

    if not source.get("is_active"):
        raise NewsDraftingValidationError(
            "News source is inactive"
        )

    created = 0
    duplicates = 0
    results = []

    try:
        for article in payload.articles:
            canonical_url = normalize_url(
                article.canonical_url
            )
            fingerprint = article_fingerprint(
                title=article.title,
                canonical_url=canonical_url,
                publisher=article.publisher,
                published_at=article.published_at,
            )
            existing = repository.find_article_by_identity(
                db=db,
                source_id=payload.source_id,
                canonical_url=canonical_url,
                fingerprint=fingerprint,
                external_id=article.external_id,
            )

            if existing is not None:
                duplicates += 1
                results.append(
                    {
                        "status": "duplicate",
                        "article_id": existing["id"],
                        "canonical_url": canonical_url,
                    }
                )
                continue

            article_id = repository.create_article(
                db=db,
                source_id=payload.source_id,
                external_id=article.external_id,
                title=article.title,
                canonical_url=canonical_url,
                publisher=article.publisher,
                published_at=article.published_at,
                summary=article.summary,
                content_text=article.content_text,
                fingerprint=fingerprint,
                metadata=article.metadata,
            )
            created += 1
            results.append(
                {
                    "status": "created",
                    "article_id": article_id,
                    "canonical_url": canonical_url,
                }
            )

        run_id = repository.record_ingestion_run(
            db=db,
            source_id=payload.source_id,
            received_count=len(payload.articles),
            created_count=created,
            duplicate_count=duplicates,
            status="completed",
            error_message=None,
        )
        db.commit()

        return {
            "run_id": run_id,
            "source_id": payload.source_id,
            "received": len(payload.articles),
            "created": created,
            "duplicates": duplicates,
            "results": results,
        }

    except Exception:
        db.rollback()
        raise


def build_generation_prompt(
    *,
    newsletter_name: str,
    subject_hint: str | None,
    articles: list[dict[str, Any]],
) -> str:
    evidence = []

    for item in articles:
        evidence.append(
            {
                "article_id": item["id"],
                "title": item["title"],
                "publisher": item["publisher"],
                "published_at": str(item["published_at"]),
                "canonical_url": item["canonical_url"],
                "summary": item.get("summary"),
                "content_text": item.get("content_text"),
            }
        )

    return (
        "Create a concise, factual property-market newsletter draft. "
        "Use only the supplied evidence. Do not invent facts, figures, "
        "quotes, dates, or organizations. Every factual section must be "
        "followed by a link block pointing to its supporting canonical URL. "
        "Return strict JSON with keys subject, preview_text, and blocks. "
        "Blocks may only use kinds heading, paragraph, link, and divider. "
        "Do not include an unsubscribe block; the application appends it. "
        f"Newsletter name: {newsletter_name}. "
        f"Subject hint: {subject_hint or 'none'}. "
        f"Evidence: {json.dumps(evidence, default=str)}"
    )


def _validate_generated_output(
    generated: dict[str, Any],
    articles: list[dict[str, Any]],
) -> tuple[str, str | None, list[NewsletterBlock]]:
    subject = str(generated.get("subject") or "").strip()

    if not subject:
        raise NewsDraftingValidationError(
            "AI generator returned an empty subject"
        )

    raw_blocks = generated.get("blocks")

    if not isinstance(raw_blocks, list) or not raw_blocks:
        raise NewsDraftingValidationError(
            "AI generator returned no content blocks"
        )

    blocks = [
        NewsletterBlock.model_validate(item)
        for item in raw_blocks
    ]
    permitted_urls = {
        normalize_url(item["canonical_url"])
        for item in articles
    }
    linked_urls = {
        normalize_url(block.url)
        for block in blocks
        if block.kind == "link" and block.url
    }

    if not permitted_urls.intersection(linked_urls):
        raise NewsDraftingValidationError(
            "AI draft must preserve source provenance links"
        )

    blocks.append(
        NewsletterBlock(
            kind="divider",
        )
    )
    blocks.append(
        NewsletterBlock(
            kind="link",
            text="Unsubscribe",
            url="{unsubscribe_link}",
        )
    )

    preview_text = generated.get("preview_text")

    if preview_text is not None:
        preview_text = str(preview_text).strip()[:255] or None

    return subject[:255], preview_text, blocks


def generate_newsletter_draft(
    *,
    db: Session,
    payload: NewsDraftGenerationRequest,
    user: CurrentUser,
    generator: NewsDraftGenerator,
) -> dict[str, Any]:
    sender = repository.get_sender_identity(
        db=db,
        sender_identity_id=payload.sender_identity_id,
    )

    if sender is None or not sender.get("is_active"):
        raise NewsDraftingValidationError(
            "Sender identity is unavailable"
        )

    article_map = repository.get_articles_by_ids(
        db=db,
        article_ids=payload.article_ids,
    )
    missing_ids = [
        item
        for item in payload.article_ids
        if item not in article_map
    ]

    if missing_ids:
        rendered = ", ".join(str(item) for item in missing_ids)
        raise NewsDraftingValidationError(
            f"Missing article IDs: {rendered}"
        )

    articles = [
        article_map[item]
        for item in payload.article_ids
    ]
    prompt = build_generation_prompt(
        newsletter_name=payload.newsletter_name,
        subject_hint=payload.subject_hint,
        articles=articles,
    )
    prompt_hash = hashlib.sha256(
        prompt.encode("utf-8")
    ).hexdigest()
    generation_run_id = repository.create_generation_run(
        db=db,
        article_ids=payload.article_ids,
        generator_model=generator.model_name,
        prompt_hash=prompt_hash,
        requested_by=user.id,
    )

    try:
        generated = generator.generate(
            {
                "prompt": prompt,
                "articles": articles,
                "newsletter_name": payload.newsletter_name,
            }
        )
        subject, preview_text, blocks = _validate_generated_output(
            generated,
            articles,
        )
        body_text, body_html = render_blocks(blocks)
        block_values = [
            block.model_dump()
            for block in blocks
        ]
        newsletter_id = repository.create_generated_newsletter(
            db=db,
            name=payload.newsletter_name,
            subject=subject,
            preview_text=preview_text,
            sender_identity_id=payload.sender_identity_id,
            blocks=block_values,
            body_text=body_text,
            body_html=body_html,
            generation_run_id=generation_run_id,
            source_type="ai_assisted",
            status="draft",
            created_by=user.id,
            updated_by=user.id,
            approved_by=None,
            scheduled_for=None,
        )
        repository.link_draft_articles(
            db=db,
            newsletter_id=newsletter_id,
            article_ids=payload.article_ids,
        )
        repository.mark_articles_used(
            db=db,
            article_ids=payload.article_ids,
        )
        repository.complete_generation_run(
            db=db,
            generation_run_id=generation_run_id,
            newsletter_id=newsletter_id,
            status="completed",
            error_message=None,
        )
        db.commit()

        return {
            "generation_run_id": generation_run_id,
            "newsletter_id": newsletter_id,
            "status": "draft",
            "generator_model": generator.model_name,
            "article_ids": payload.article_ids,
        }

    except Exception as exc:
        db.rollback()

        try:
            repository.complete_generation_run(
                db=db,
                generation_run_id=generation_run_id,
                newsletter_id=None,
                status="failed",
                error_message=str(exc)[:2000],
            )
            db.commit()
        except Exception:
            db.rollback()

        raise
