import pytest

import app.routers.communications as communications


class FakeResponse:
    status_code = 200

    def json(self):
        return {"id": "resend-test-id"}


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
async def test_send_via_resend_includes_campaign_attachment(monkeypatch):
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

    result = await communications.send_via_resend(
        to_email="lead@example.com",
        to_name="Lead",
        from_email="onboarding@resend.dev",
        from_name="Nyumba Zetu",
        subject="Demo",
        body="Hello",
        attachment_name="brochure.pdf",
        attachment_content=b"brochure",
    )

    assert result["id"] == "resend-test-id"

    payload = FakeAsyncClient.last_json

    assert payload["attachments"] == [
        communications.build_resend_attachment(
            filename="brochure.pdf",
            content=b"brochure",
        )
    ]

    assert "unsubscribe" in payload["text"].lower()
