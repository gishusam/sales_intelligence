import base64
import pytest
from fastapi import HTTPException

from app.routers.communications import (
    validate_campaign_attachment,
    build_resend_attachment,
)


def test_valid_campaign_attachment_is_accepted():
    data = b"nyumba-zetu-brochure"

    result = validate_campaign_attachment(
        filename="brochure.pdf",
        content=data,
        content_type="application/pdf",
    )

    assert result["filename"] == "brochure.pdf"
    assert result["size"] == len(data)
    assert result["content_type"] == "application/pdf"


def test_attachment_larger_than_5mb_is_rejected():
    content = b"x" * (5 * 1024 * 1024 + 1)

    with pytest.raises(HTTPException) as exc:
        validate_campaign_attachment(
            filename="brochure.pdf",
            content=content,
            content_type="application/pdf",
        )

    assert exc.value.status_code == 400
    assert "5MB" in str(exc.value.detail)


def test_unsupported_attachment_type_is_rejected():
    with pytest.raises(HTTPException) as exc:
        validate_campaign_attachment(
            filename="software.exe",
            content=b"bad",
            content_type="application/octet-stream",
        )

    assert exc.value.status_code == 400


def test_resend_attachment_uses_base64_content():
    attachment = build_resend_attachment(
        filename="brochure.pdf",
        content=b"hello",
    )

    assert attachment == {
        "filename": "brochure.pdf",
        "content": base64.b64encode(b"hello").decode("ascii"),
    }
