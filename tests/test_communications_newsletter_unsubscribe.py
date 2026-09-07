import pytest

import app.routers.communications as communications


class FakeResponse:
    status_code = 200

    def json(self):
        return {"id": "newsletter-test-id"}


class FakeAsyncClient:
    last_json = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, headers=None, json=None):
        FakeAsyncClient.last_json = json
        return FakeResponse()


@pytest.mark.asyncio
async def test_newsletter_does_not_duplicate_unsubscribe_footer(
    monkeypatch,
):
    monkeypatch.setattr(
        communications,
        "_get_resend_key",
        lambda: "test-key",
    )
    monkeypatch.setattr(
        communications,
        "_get_app_url",
        lambda: "https://example.com",
    )
    monkeypatch.setattr(
        communications.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    body = (
        "Newsletter content\n\n"
        "Unsubscribe: "
        "https://example.com/unsubscribe?email=lead@example.com"
    )

    html = (
        "<html><body>"
        '<a href="https://example.com/unsubscribe'
        '?email=lead@example.com">Unsubscribe</a>'
        "</body></html>"
    )

    await communications.send_via_resend(
        to_email="lead@example.com",
        to_name="Lead",
        from_email="onboarding@resend.dev",
        from_name="Nyumba Zetu",
        subject="August update",
        body=body,
        html_body=html,
        append_unsubscribe_footer=False,
    )

    payload = FakeAsyncClient.last_json

    assert payload["text"] == body
    assert payload["html"] == html


@pytest.mark.asyncio
async def test_cold_outreach_still_gets_unsubscribe_footer(
    monkeypatch,
):
    monkeypatch.setattr(
        communications,
        "_get_resend_key",
        lambda: "test-key",
    )
    monkeypatch.setattr(
        communications,
        "_get_app_url",
        lambda: "https://example.com",
    )
    monkeypatch.setattr(
        communications.httpx,
        "AsyncClient",
        FakeAsyncClient,
    )

    await communications.send_via_resend(
        to_email="lead@example.com",
        to_name="Lead",
        from_email="onboarding@resend.dev",
        from_name="Nyumba Zetu",
        subject="Book a demo",
        body="Hello",
    )

    payload = FakeAsyncClient.last_json

    assert payload["text"].startswith("Hello")
    assert payload["text"].count(
        "https://example.com/unsubscribe?email=lead@example.com"
    ) == 1
