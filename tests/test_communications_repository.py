import os

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")

import json
from types import SimpleNamespace

from app.auth import CurrentUser
from app.communications.schemas import TemplateCreate
from app.communications import template_repository


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


def template_row(**overrides):
    values = {
        "id": 7,
        "slug": "agency-introduction",
        "name": "Agency introduction",
        "template_type": "cold",
        "channel": "email",
        "subject": "A better workflow for {company_name}",
        "body_text": (
            "Hi {contact_name},\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
        "body_html": None,
        "required_placeholders": [
            "company_name",
            "contact_name",
            "rep_email",
            "rep_name",
        ],
        "version": 1,
        "is_default": False,
        "is_active": True,
        "created_by": 3,
        "updated_by": 3,
        "created_at": None,
        "updated_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_create_template_persists_validated_placeholders():
    db = FakeDb([Result(one=template_row())])
    user = CurrentUser(
        id=3,
        name="Admin User",
        email="admin@example.test",
        role="admin",
    )
    payload = TemplateCreate(
        slug="agency-introduction",
        name="Agency introduction",
        template_type="cold",
        subject="A better workflow for {company_name}",
        body_text=(
            "Hi {contact_name},\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
    )

    created = template_repository.create_template(
        db=db,
        payload=payload,
        user=user,
    )

    assert created["id"] == 7
    assert db.commits == 1
    assert len(db.calls) == 1

    sql, params = db.calls[0]

    assert "INSERT INTO communication_templates" in sql
    assert json.loads(params["required_placeholders"]) == [
        "company_name",
        "contact_name",
        "rep_email",
        "rep_name",
    ]


def test_list_templates_applies_type_and_active_filters():
    db = FakeDb([Result(rows=[template_row()])])

    rows = template_repository.list_templates(
        db=db,
        template_type="cold",
        include_inactive=False,
    )

    assert len(rows) == 1

    sql, params = db.calls[0]

    assert "t.template_type = :template_type" in sql
    assert "t.is_active = TRUE" in sql
    assert params == {"template_type": "cold"}
