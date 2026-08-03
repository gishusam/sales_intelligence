import os

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")

from pathlib import Path

import pytest
from fastapi import HTTPException

from app.auth import CurrentUser
from app.communications.schemas import TemplateCreate, TemplateUpdate
from app.communications.template_repository import TemplateSlugConflict
from app.routers import communications


def user(role: str) -> CurrentUser:
    return CurrentUser(
        id=5,
        name="Example User",
        email="user@example.test",
        role=role,
    )


def valid_create_payload() -> TemplateCreate:
    return TemplateCreate(
        slug="agency-introduction",
        name="Agency introduction",
        template_type="cold",
        subject="A better workflow for {company_name}",
        body_text=(
            "Hi {contact_name},\n"
            "We support teams in {area}.\n"
            "Regards,\n{rep_name}\n{rep_email}"
        ),
    )


def template_record() -> dict:
    return {
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
        "created_by": 5,
        "updated_by": 5,
        "created_at": None,
        "updated_at": None,
    }


def test_sales_user_can_list_templates(monkeypatch):
    monkeypatch.setattr(
        communications.repository,
        "list_templates",
        lambda **kwargs: [template_record()],
    )

    response = communications.list_communication_templates(
        template_type="cold",
        include_inactive=False,
        db=object(),
        user=user("sales"),
    )

    assert response[0]["slug"] == "agency-introduction"


def test_sales_user_cannot_create_templates(monkeypatch):
    called = False

    def fake_create(**kwargs):
        nonlocal called
        called = True
        return template_record()

    monkeypatch.setattr(
        communications.repository,
        "create_template",
        fake_create,
    )

    with pytest.raises(HTTPException) as error:
        communications.create_communication_template(
            payload=valid_create_payload(),
            db=object(),
            user=user("sales"),
        )

    assert error.value.status_code == 403
    assert called is False


def test_admin_user_can_create_templates(monkeypatch):
    monkeypatch.setattr(
        communications.repository,
        "create_template",
        lambda **kwargs: template_record(),
    )

    response = communications.create_communication_template(
        payload=valid_create_payload(),
        db=object(),
        user=user("admin"),
    )

    assert response["id"] == 7


def test_duplicate_template_slug_returns_conflict(monkeypatch):
    def duplicate(**kwargs):
        raise TemplateSlugConflict("Template slug already exists")

    monkeypatch.setattr(
        communications.repository,
        "create_template",
        duplicate,
    )

    with pytest.raises(HTTPException) as error:
        communications.create_communication_template(
            payload=valid_create_payload(),
            db=object(),
            user=user("manager"),
        )

    assert error.value.status_code == 409


def test_update_missing_template_returns_not_found(monkeypatch):
    monkeypatch.setattr(
        communications.repository,
        "update_template",
        lambda **kwargs: None,
    )

    with pytest.raises(HTTPException) as error:
        communications.update_communication_template(
            template_id=999,
            payload=TemplateUpdate(name="Updated name"),
            db=object(),
            user=user("admin"),
        )

    assert error.value.status_code == 404


def test_main_mounts_communications_router():
    main_path = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    )
    source = main_path.read_text(encoding="utf-8")

    assert (
        "from app.routers import communications as communications_router"
        in source
    )
    assert "app.include_router(communications_router.router)" in source
