import pytest
from fastapi import HTTPException

from app.routers.communications import ensure_campaign_attachment_editable


@pytest.mark.parametrize("status", ["draft", "reviewed"])
def test_attachment_can_be_changed_before_send(status):
    ensure_campaign_attachment_editable(status)


@pytest.mark.parametrize(
    "status",
    ["sending", "sent", "sent_with_issues", "failed"],
)
def test_attachment_cannot_be_changed_after_send_starts(status):
    with pytest.raises(HTTPException) as exc:
        ensure_campaign_attachment_editable(status)

    assert exc.value.status_code == 400
    assert "attachment" in str(exc.value.detail).lower()
