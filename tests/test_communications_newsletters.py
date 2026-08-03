import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")

from app.auth import CurrentUser
from app.communications.delivery import ProviderResult


def user(role="admin"):
    return CurrentUser(
        id=3,
        name="Admin User",
        email="admin@example.test",
        role=role,
    )


def newsletter(**overrides):
    value = {
        "id": 71,
        "name": "August property brief",
        "subject": "August property brief",
        "preview_text": "Latest property insights",
        "status": "draft",
        "sender_identity_id": 11,
        "blocks": [
            {"kind": "heading", "text": "August property brief"},
            {
                "kind": "paragraph",
                "text": "Market activity continues to evolve.",
            },
            {
                "kind": "link",
                "text": "Unsubscribe",
                "url": "{unsubscribe_link}",
            },
        ],
        "body_text": (
            "August property brief\n\n"
            "Market activity continues to evolve.\n\n"
            "Unsubscribe: {unsubscribe_link}"
        ),
        "body_html": (
            "<h2>August property brief</h2>"
            "<p>Market activity continues to evolve.</p>"
            '<p><a href="{unsubscribe_link}">Unsubscribe</a></p>'
        ),
        "created_by": 3,
        "updated_by": 3,
        "reviewed_by": None,
        "approved_by": None,
        "reviewed_at": None,
        "approved_at": None,
        "created_at": None,
        "updated_at": None,
    }
    value.update(overrides)
    return value


def sender(**overrides):
    value = {
        "id": 11,
        "display_name": "Nyumba Zetu Sales",
        "email_address": "sales@nyumbazetu.example",
        "reply_to_address": None,
        "provider": "smtp",
        "is_active": True,
    }
    value.update(overrides)
    return value


class FakeProvider:
    def __init__(self):
        self.calls = []

    def send(self, message):
        self.calls.append(message)
        return ProviderResult(
            accepted=True,
            provider_message_id="provider-test-1",
        )


def test_newsletter_requires_unsubscribe_placeholder():
    from app.communications.newsletter_schemas import NewsletterCreate

    with pytest.raises(
        ValidationError,
        match=r"unsubscribe_link",
    ):
        NewsletterCreate(
            name="August brief",
            subject="August brief",
            sender_identity_id=11,
            blocks=[
                {
                    "kind": "paragraph",
                    "text": "Market activity continues.",
                }
            ],
        )


def test_link_block_requires_url():
    from app.communications.newsletter_schemas import NewsletterBlock

    with pytest.raises(ValidationError):
        NewsletterBlock(
            kind="link",
            text="Read more",
        )


def test_renderer_escapes_html_and_preserves_unsubscribe_token():
    from app.communications.newsletter_renderer import render_blocks
    from app.communications.newsletter_schemas import NewsletterBlock

    body_text, body_html = render_blocks(
        [
            NewsletterBlock(
                kind="heading",
                text="<Market Update>",
            ),
            NewsletterBlock(
                kind="paragraph",
                text="Rents & yields",
            ),
            NewsletterBlock(
                kind="link",
                text="Unsubscribe",
                url="{unsubscribe_link}",
            ),
        ]
    )

    assert "<Market Update>" in body_text
    assert "&lt;Market Update&gt;" in body_html
    assert "Rents &amp; yields" in body_html
    assert 'href="{unsubscribe_link}"' in body_html


def test_only_draft_newsletters_can_be_edited(monkeypatch):
    from app.communications import newsletter_repository as repository
    from app.communications.newsletter_schemas import NewsletterUpdate
    from app.communications.newsletter_service import (
        NewsletterStateError,
        update_newsletter,
    )

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="in_review"),
    )

    with pytest.raises(
        NewsletterStateError,
        match=r"Only draft newsletters can be edited",
    ):
        update_newsletter(
            db=object(),
            newsletter_id=71,
            payload=NewsletterUpdate(name="Renamed"),
            user=user(),
        )


def test_review_and_approval_state_flow(monkeypatch):
    from app.communications import newsletter_repository as repository
    from app.communications.newsletter_service import (
        approve_newsletter,
        submit_for_review,
    )

    events = []
    db = SimpleNamespace(commit=lambda: events.append("commit"))

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="draft"),
    )
    monkeypatch.setattr(
        repository,
        "mark_in_review",
        lambda **kwargs: events.append("review"),
    )

    assert submit_for_review(
        db=db,
        newsletter_id=71,
        user=user("manager"),
    )["status"] == "in_review"

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(status="in_review"),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "mark_approved",
        lambda **kwargs: events.append("approved"),
    )

    assert approve_newsletter(
        db=db,
        newsletter_id=71,
        user=user("admin"),
    )["status"] == "approved"
    assert "approved" in events


def test_audience_snapshot_filters_missing_suppressed_and_duplicates(
    monkeypatch,
):
    from app.communications import newsletter_repository as repository
    from app.communications.newsletter_schemas import NewsletterAudienceRequest
    from app.communications.newsletter_service import snapshot_audience

    leads = {
        1: {
            "id": 1,
            "name": "Alpha Properties",
            "contact_person": "Alice",
            "owner_name": None,
            "email": "alice@example.test",
        },
        2: {
            "id": 2,
            "name": "No Email",
            "contact_person": "No Email",
            "owner_name": None,
            "email": None,
        },
        3: {
            "id": 3,
            "name": "Blocked",
            "contact_person": "Blocked",
            "owner_name": None,
            "email": "blocked@example.test",
        },
        4: {
            "id": 4,
            "name": "Duplicate",
            "contact_person": "Duplicate",
            "owner_name": None,
            "email": "duplicate@example.test",
        },
    }
    created = []

    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(),
    )
    monkeypatch.setattr(
        repository,
        "get_leads_by_ids",
        lambda **kwargs: leads,
    )
    monkeypatch.setattr(
        repository,
        "is_suppressed",
        lambda **kwargs: (
            kwargs["email_address"] == "blocked@example.test"
        ),
    )
    monkeypatch.setattr(
        repository,
        "recipient_exists",
        lambda **kwargs: kwargs["lead_id"] == 4,
    )
    monkeypatch.setattr(
        repository,
        "create_recipient",
        lambda **kwargs: created.append(kwargs) or 801,
    )

    db = SimpleNamespace(
        commit=lambda: None,
        rollback=lambda: None,
    )

    result = snapshot_audience(
        db=db,
        newsletter_id=71,
        payload=NewsletterAudienceRequest(
            lead_ids=[1, 1, 2, 3, 4, 999],
        ),
        user=user(),
    )

    assert result["requested"] == 6
    assert result["unique_requested"] == 5
    assert result["added"] == 1
    assert result["missing_email"] == 1
    assert result["suppressed"] == 1
    assert result["duplicates"] == 1
    assert result["missing_leads"] == 1
    assert created[0]["lead_id"] == 1


def test_test_send_replaces_unsubscribe_link_and_records_send(
    monkeypatch,
):
    from app.communications import newsletter_repository as repository
    from app.communications.newsletter_schemas import NewsletterTestSendRequest
    from app.communications.newsletter_service import test_send

    events = []
    monkeypatch.setenv(
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        "newsletter-secret",
    )
    monkeypatch.setattr(
        repository,
        "get_newsletter",
        lambda **kwargs: newsletter(),
    )
    monkeypatch.setattr(
        repository,
        "get_sender_identity",
        lambda **kwargs: sender(),
    )
    monkeypatch.setattr(
        repository,
        "create_test_message",
        lambda **kwargs: events.append(("create", kwargs)) or 901,
    )
    monkeypatch.setattr(
        repository,
        "mark_test_sent",
        lambda **kwargs: events.append(("sent", kwargs)),
    )

    provider = FakeProvider()
    db = SimpleNamespace(commit=lambda: None)

    result = test_send(
        db=db,
        newsletter_id=71,
        payload=NewsletterTestSendRequest(
            to_email="reviewer@example.test",
        ),
        user=user(),
        provider=provider,
    )

    assert result["status"] == "sent"
    assert len(provider.calls) == 1
    outbound = provider.calls[0]
    assert "{unsubscribe_link}" not in outbound.body_text
    assert "/unsubscribe/" in outbound.body_text
    assert any(event[0] == "sent" for event in events)


def test_unsubscribe_token_roundtrip_and_suppression(monkeypatch):
    from app.communications import newsletter_repository as repository
    from app.communications.newsletter_service import (
        create_unsubscribe_token,
        unsubscribe,
    )

    token = create_unsubscribe_token(
        "Alice@Example.Test",
        secret="newsletter-secret",
    )
    events = []

    monkeypatch.setenv(
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        "newsletter-secret",
    )
    monkeypatch.setattr(
        repository,
        "suppress_email",
        lambda **kwargs: events.append(kwargs),
    )

    result = unsubscribe(
        db=SimpleNamespace(commit=lambda: None),
        token=token,
    )

    assert result == {
        "email_address": "alice@example.test",
        "status": "unsubscribed",
    }
    assert events[0]["email_address"] == "alice@example.test"


def test_sales_can_read_but_cannot_create(monkeypatch):
    from app.communications.newsletter_schemas import NewsletterCreate
    from app.routers import communication_newsletters

    monkeypatch.setattr(
        communication_newsletters.newsletter_repository,
        "list_newsletters",
        lambda **kwargs: [newsletter()],
    )

    assert communication_newsletters.list_newsletters(
        status_filter=None,
        db=object(),
        user=user("sales"),
    )[0]["id"] == 71

    with pytest.raises(HTTPException) as error:
        communication_newsletters.create_newsletter(
            payload=NewsletterCreate(
                name="August brief",
                subject="August brief",
                sender_identity_id=11,
                blocks=[
                    {
                        "kind": "paragraph",
                        "text": "Market activity.",
                    },
                    {
                        "kind": "link",
                        "text": "Unsubscribe",
                        "url": "{unsubscribe_link}",
                    },
                ],
            ),
            db=object(),
            user=user("sales"),
        )

    assert error.value.status_code == 403


def test_main_mounts_newsletter_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_newsletters "
        "as communication_newsletters_router"
    ) in source
    assert (
        "app.include_router("
        "communication_newsletters_router.router"
        ")"
    ) in source
