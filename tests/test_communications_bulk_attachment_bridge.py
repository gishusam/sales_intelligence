from app.routers.communications import campaign_attachment_send_kwargs


def test_saved_campaign_attachment_is_forwarded_to_sender():
    campaign = {
        "attachment_name": "brochure.pdf",
        "attachment_content": b"brochure-bytes",
    }

    assert campaign_attachment_send_kwargs(campaign) == {
        "attachment_name": "brochure.pdf",
        "attachment_content": b"brochure-bytes",
    }


def test_campaign_without_attachment_sends_normally():
    campaign = {
        "attachment_name": None,
        "attachment_content": None,
    }

    assert campaign_attachment_send_kwargs(campaign) == {
        "attachment_name": None,
        "attachment_content": None,
    }
