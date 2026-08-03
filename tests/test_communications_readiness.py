import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.test")

from app.auth import CurrentUser


def user(role="admin"):
    return CurrentUser(
        id=3,
        name="Admin User",
        email="admin@example.test",
        role=role,
    )


def test_development_environment_does_not_require_delivery_secrets():
    from app.communications.communications_readiness import (
        environment_issues,
    )

    issues = environment_issues(
        {
            "ENVIRONMENT": "development",
        }
    )

    assert issues == []


def test_staging_fails_closed_without_communications_secrets():
    from app.communications.communications_readiness import (
        environment_issues,
    )

    issues = environment_issues(
        {
            "ENVIRONMENT": "staging",
            "PUBLIC_API_BASE_URL": "http://staging.example.test",
            "COMMUNICATIONS_SMTP_MOCK": "true",
        }
    )

    assert "COMMUNICATIONS_WORKER_TOKEN is required" in issues
    assert "EMAIL_WEBHOOK_SECRET is required" in issues
    assert "NEWSLETTER_UNSUBSCRIBE_SECRET is required" in issues
    assert (
        "PUBLIC_API_BASE_URL must use HTTPS in staging/production"
        in issues
    )
    assert (
        "COMMUNICATIONS_SMTP_MOCK must be disabled in staging/production"
        in issues
    )


def test_staging_accepts_complete_safe_environment():
    from app.communications.communications_readiness import (
        environment_issues,
    )

    issues = environment_issues(
        {
            "ENVIRONMENT": "staging",
            "COMMUNICATIONS_WORKER_TOKEN": "worker-secret",
            "EMAIL_WEBHOOK_SECRET": "webhook-secret",
            "NEWSLETTER_UNSUBSCRIBE_SECRET": "unsubscribe-secret",
            "PUBLIC_API_BASE_URL": "https://staging.example.test",
            "COMMUNICATIONS_SMTP_MOCK": "false",
        }
    )

    assert issues == []


def test_readiness_report_combines_environment_and_schema(monkeypatch):
    from app.communications import communications_schema_audit
    from app.communications.communications_readiness import (
        build_readiness_report,
    )

    monkeypatch.setattr(
        communications_schema_audit,
        "inspect_schema",
        lambda **kwargs: {
            "ready": False,
            "missing_tables": ["email_events"],
            "missing_columns": [],
            "missing_indexes": [],
        },
    )

    result = build_readiness_report(
        db=object(),
        environ={
            "ENVIRONMENT": "development",
        },
        now=datetime(
            2026,
            8,
            3,
            12,
            0,
            tzinfo=timezone.utc,
        ),
    )

    assert result["ready"] is False
    assert result["environment"] == "development"
    assert "Missing table: email_events" in result["issues"]
    assert result["checked_at"] == "2026-08-03T12:00:00+00:00"


def test_internal_readiness_requires_worker_token(monkeypatch):
    from app.routers import communication_readiness

    monkeypatch.setenv(
        "COMMUNICATIONS_WORKER_TOKEN",
        "worker-secret",
    )

    with pytest.raises(HTTPException) as error:
        communication_readiness.get_internal_readiness(
            x_worker_token="wrong-token",
            db=object(),
        )

    assert error.value.status_code == 401


def test_sales_can_read_authenticated_readiness(monkeypatch):
    from app.routers import communication_readiness

    monkeypatch.setattr(
        communication_readiness,
        "build_readiness_report",
        lambda **kwargs: {
            "ready": True,
            "environment": "development",
            "issues": [],
            "schema": {},
            "checked_at": "2026-08-03T12:00:00+00:00",
        },
    )

    result = communication_readiness.get_readiness(
        db=object(),
        user=user("sales"),
    )

    assert result["ready"] is True


def test_main_mounts_readiness_router():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[1]
        / "backend"
        / "app"
        / "main.py"
    ).read_text(encoding="utf-8")

    assert (
        "from app.routers import communication_readiness "
        "as communication_readiness_router"
    ) in source
    assert (
        "app.include_router("
        "communication_readiness_router.router"
        ")"
    ) in source
