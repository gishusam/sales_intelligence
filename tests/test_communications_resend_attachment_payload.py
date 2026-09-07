import base64

from app.routers.communications import build_resend_payload


def test_resend_payload_without_attachment_stays_plain():
    payload = build_resend_payload(
        to_email="lead@example.com",
        from_email="onboarding@resend.dev",
        from_name="Nyumba Zetu",
        subject="Demo",
        body="Hello",
        reply_to=None,
        attachment_name=None,
        attachment_content=None,
    )

    assert "attachments" not in payload


def test_resend_payload_contains_saved_campaign_attachment():
    payload = build_resend_payload(
        to_email="lead@example.com",
        from_email="onboarding@resend.dev",
        from_name="Nyumba Zetu",
        subject="Demo",
        body="Hello",
        reply_to="sales@nyumbazetu.com",
        attachment_name="brochure.pdf",
        attachment_content=b"brochure-content",
    )

    assert payload["reply_to"] == "sales@nyumbazetu.com"
    assert payload["attachments"] == [
        {
            "filename": "brochure.pdf",
            "content": base64.b64encode(
                b"brochure-content"
            ).decode("ascii"),
        }
    ]
