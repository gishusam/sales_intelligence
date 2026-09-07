from app.routers.communications import build_resend_payload


def test_resend_payload_includes_html_for_newsletter():
    payload = build_resend_payload(
        to_email="customer@example.com",
        from_email="sales@example.com",
        from_name="Nyumba Zetu",
        subject="August update",
        body="Plain text fallback",
        html_body="<html><body><h1>August update</h1></body></html>",
    )

    assert payload["text"] == "Plain text fallback"
    assert payload["html"] == (
        "<html><body><h1>August update</h1></body></html>"
    )


def test_resend_payload_omits_html_for_cold_outreach():
    payload = build_resend_payload(
        to_email="customer@example.com",
        from_email="sales@example.com",
        from_name="Nyumba Zetu",
        subject="Book a demo",
        body="Hi there",
    )

    assert payload["text"] == "Hi there"
    assert "html" not in payload
