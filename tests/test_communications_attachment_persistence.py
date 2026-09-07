from app.routers.communications import campaign_attachment_update_values


def test_campaign_attachment_values_are_ready_for_database_storage():
    content = b"nyumba brochure"

    values = campaign_attachment_update_values(
        filename="brochure.pdf",
        content=content,
        content_type="application/pdf",
    )

    assert values == {
        "attachment_name": "brochure.pdf",
        "attachment_content": content,
        "attachment_mime_type": "application/pdf",
        "attachment_size": len(content),
    }


def test_campaign_attachment_can_be_cleared():
    values = campaign_attachment_update_values(
        filename=None,
        content=None,
        content_type=None,
    )

    assert values == {
        "attachment_name": None,
        "attachment_content": None,
        "attachment_mime_type": None,
        "attachment_size": None,
    }
