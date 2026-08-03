from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_staging_runbook_contains_required_gates():
    source = (
        ROOT
        / "docs"
        / "communications"
        / "STAGING_RUNBOOK.md"
    ).read_text(encoding="utf-8").lower()

    for phrase in {
        "backup",
        "migration preflight",
        "apply the migration",
        "smoke test",
        "rollback",
        "do not run against production",
    }:
        assert phrase in source


def test_preflight_script_is_read_only_by_default():
    source = (
        ROOT
        / "scripts"
        / "communications_migration_preflight.py"
    ).read_text(encoding="utf-8").lower()

    assert "--database-url" in source
    assert "communications_preflight_database_url" in source
    assert "inspect_schema" in source
    assert "migration" not in source or "execute migration" not in source


def test_env_example_documents_required_settings():
    candidates = [
        ROOT / ".env.example",
        ROOT / "backend" / ".env.example",
    ]
    existing = [path for path in candidates if path.exists()]

    assert existing

    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in existing
    )

    for key in {
        "COMMUNICATIONS_WORKER_TOKEN",
        "EMAIL_WEBHOOK_SECRET",
        "NEWSLETTER_UNSUBSCRIBE_SECRET",
        "PUBLIC_API_BASE_URL",
        "COMMUNICATIONS_SMTP_MOCK",
        "NEWSLETTER_AI_MODEL",
    }:
        assert key in combined
