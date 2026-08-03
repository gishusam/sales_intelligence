"""Fail-closed environment and database readiness checks."""

import os
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy.orm import Session

from app.communications import communications_schema_audit


_PROTECTED_ENVIRONMENTS = {
    "staging",
    "production",
    "prod",
}
_TRUE_VALUES = {
    "1",
    "true",
    "yes",
    "on",
}


def environment_issues(
    environ: Mapping[str, str],
) -> list[str]:
    environment = environ.get(
        "ENVIRONMENT",
        "development",
    ).strip().lower()

    if environment not in _PROTECTED_ENVIRONMENTS:
        return []

    issues = []

    for key in (
        "COMMUNICATIONS_WORKER_TOKEN",
        "EMAIL_WEBHOOK_SECRET",
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        "SMTP_HOST",
        "SMTP_USER",
        "SMTP_PASSWORD",
    ):
        if not environ.get(key, "").strip():
            issues.append(f"{key} is required")

    public_base_url = environ.get(
        "PUBLIC_API_BASE_URL",
        "",
    ).strip()

    if not public_base_url.startswith("https://"):
        issues.append(
            "PUBLIC_API_BASE_URL must use HTTPS "
            "in staging/production"
        )

    smtp_mock = environ.get(
        "COMMUNICATIONS_SMTP_MOCK",
        "false",
    ).strip().lower()

    if smtp_mock in _TRUE_VALUES:
        issues.append(
            "COMMUNICATIONS_SMTP_MOCK must be disabled "
            "in staging/production"
        )

    return issues


def build_readiness_report(
    *,
    db: Session,
    environ: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict:
    values = environ or os.environ
    environment = values.get(
        "ENVIRONMENT",
        "development",
    ).strip().lower()
    env_issues = environment_issues(values)

    try:
        schema = communications_schema_audit.inspect_schema(
            db=db,
        )
        schema_issues = [
            *(
                f"Missing table: {item}"
                for item in schema["missing_tables"]
            ),
            *(
                f"Missing column: {item}"
                for item in schema["missing_columns"]
            ),
            *(
                f"Missing index: {item}"
                for item in schema["missing_indexes"]
            ),
            *(
                f"Schema mismatch: {item}"
                for item in schema.get(
                    "mismatched_constraints",
                    [],
                )
            ),
        ]
    except Exception as exc:
        schema = {
            "ready": False,
            "missing_tables": [],
            "missing_columns": [],
            "missing_indexes": [],
            "mismatched_constraints": [],
            "error": type(exc).__name__,
        }
        schema_issues = [
            "Communications database schema could not be inspected"
        ]

    issues = [*env_issues, *schema_issues]
    checked_at = now or datetime.now(timezone.utc)

    return {
        "ready": not issues,
        "environment": environment,
        "issues": issues,
        "schema": schema,
        "checked_at": checked_at.isoformat(),
    }
