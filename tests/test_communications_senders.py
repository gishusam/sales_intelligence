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


class Result:
    def __init__(self, *, one=None, rows=None):
        self._one = one
        self._rows = rows or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._rows


class FakeDb:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, statement, params=None):
        self.calls.append((str(statement), params or {}))
        return self.responses.pop(0)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def row(**overrides):
    values = {
        "id": 11,
        "display_name": "Nyumba Zetu Sales",
        "email_address": "sales@nyumbazetu.example",
        "reply_to_address": None,
        "provider": "smtp",
        "provider_reference": None,
        "is_default": True,
        "is_active": True,
        "created_by": 3,
        "updated_by": 3,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def user(role="admin"):
    return CurrentUser(3, "Admin User", "admin@example.test", role)


def payload(**overrides):
    from app.communications.schemas import SenderIdentityCreate

    values = {
        "display_name": "Nyumba Zetu Sales",
        "email_address": "sales@nyumbazetu.example",
        "provider": "smtp",
        "is_default": True,
        "is_active": True,
    }
    values.update(overrides)
    return SenderIdentityCreate(**values)


def test_sender_schema_rejects_invalid_email_and_secrets():
    from app.communications.schemas import SenderIdentityCreate

    with pytest.raises(ValidationError, match="Enter a valid email address"):
        SenderIdentityCreate(
            display_name="Sales",
            email_address="not-an-email",
        )

    with pytest.raises(ValidationError):
        SenderIdentityCreate(
            display_name="Sales",
            email_address="sales@nyumbazetu.example",
            smtp_password="never-store-this",
        )


def test_default_sender_must_be_active():
    from app.communications.schemas import SenderIdentityCreate

    with pytest.raises(ValidationError, match="Default sender must be active"):
        SenderIdentityCreate(
            display_name="Sales",
            email_address="sales@nyumbazetu.example",
            is_default=True,
            is_active=False,
        )


def test_create_default_sender_unsets_previous_default():
    from app.communications import sender_repository

    db = FakeDb([Result(), Result(one=row())])
    created = sender_repository.create_sender_identity(
        db=db,
        payload=payload(),
        user=user(),
    )

    assert created["id"] == 11
    assert db.commits == 1
    assert "UPDATE sender_identities" in db.calls[0][0]
    assert "INSERT INTO sender_identities" in db.calls[1][0]


def test_sales_can_list_but_cannot_create(monkeypatch):
    from app.routers import communications

    monkeypatch.setattr(
        communications.sender_repository,
        "list_sender_identities",
        lambda **kwargs: [vars(row())],
    )
    listed = communications.list_sender_identities(
        include_inactive=False,
        db=object(),
        user=user("sales"),
    )
    assert listed[0]["id"] == 11

    with pytest.raises(HTTPException) as error:
        communications.create_sender_identity(
            payload=payload(),
            db=object(),
            user=user("sales"),
        )
    assert error.value.status_code == 403


def test_duplicate_sender_returns_conflict(monkeypatch):
    from app.communications.sender_repository import SenderIdentityConflict
    from app.routers import communications

    def duplicate(**kwargs):
        raise SenderIdentityConflict("Sender email already exists")

    monkeypatch.setattr(
        communications.sender_repository,
        "create_sender_identity",
        duplicate,
    )

    with pytest.raises(HTTPException) as error:
        communications.create_sender_identity(
            payload=payload(),
            db=object(),
            user=user("manager"),
        )
    assert error.value.status_code == 409


def test_update_missing_sender_returns_not_found(monkeypatch):
    from app.communications.schemas import SenderIdentityUpdate
    from app.routers import communications

    monkeypatch.setattr(
        communications.sender_repository,
        "update_sender_identity",
        lambda **kwargs: None,
    )

    with pytest.raises(HTTPException) as error:
        communications.update_sender_identity(
            sender_id=999,
            payload=SenderIdentityUpdate(display_name="Updated"),
            db=object(),
            user=user(),
        )
    assert error.value.status_code == 404
