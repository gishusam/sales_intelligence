from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile

from app.auth import CurrentUser
from app.routers.communications import (
    upload_campaign_attachment,
    delete_campaign_attachment,
)


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeDB:
    def __init__(self, campaign):
        self.campaign = campaign
        self.executed = []
        self.committed = False

    def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params or {}))

        if "SELECT id, status" in sql:
            return Result(self.campaign)

        return Result()

    def commit(self):
        self.committed = True


USER = CurrentUser(
    id=1,
    name="Samuel Ngugi",
    email="sam@example.com",
    role="sales",
)


@pytest.mark.asyncio
async def test_upload_campaign_attachment_persists_file():
    db = FakeDB(
        SimpleNamespace(id=41, status="draft"),
    )

    upload = UploadFile(
        filename="brochure.pdf",
        file=BytesIO(b"nyumba brochure"),
    )
    upload.headers = {
        "content-type": "application/pdf",
    }

    result = await upload_campaign_attachment(
        campaign_id=41,
        attachment=upload,
        db=db,
        user=USER,
    )

    assert result["attachment_name"] == "brochure.pdf"
    assert result["attachment_size"] == len(b"nyumba brochure")

    update_calls = [
        (sql, params)
        for sql, params in db.executed
        if "UPDATE campaigns" in sql
    ]

    assert len(update_calls) == 1

    _, params = update_calls[0]

    assert params["attachment_name"] == "brochure.pdf"
    assert params["attachment_content"] == b"nyumba brochure"
    assert params["attachment_mime_type"] == "application/pdf"
    assert params["attachment_size"] == len(b"nyumba brochure")

    assert db.committed is True


def test_delete_campaign_attachment_clears_saved_file():
    db = FakeDB(
        SimpleNamespace(id=41, status="reviewed"),
    )

    result = delete_campaign_attachment(
        campaign_id=41,
        db=db,
        user=USER,
    )

    update_calls = [
        (sql, params)
        for sql, params in db.executed
        if "UPDATE campaigns" in sql
    ]

    assert len(update_calls) == 1

    sql, _ = update_calls[0]

    assert "attachment_name = NULL" in sql
    assert "attachment_content = NULL" in sql
    assert "attachment_mime_type = NULL" in sql
    assert "attachment_size = NULL" in sql

    assert db.committed is True
    assert result["attachment_name"] is None


@pytest.mark.asyncio
async def test_upload_rejects_campaign_after_send_started():
    db = FakeDB(
        SimpleNamespace(id=41, status="sending"),
    )

    upload = UploadFile(
        filename="brochure.pdf",
        file=BytesIO(b"nyumba brochure"),
    )

    with pytest.raises(HTTPException) as exc:
        await upload_campaign_attachment(
            campaign_id=41,
            attachment=upload,
            db=db,
            user=USER,
        )

    assert exc.value.status_code == 400
