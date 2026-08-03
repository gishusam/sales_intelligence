from pathlib import Path


def source() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "2026_08_communications.sql"
    ).read_text(encoding="utf-8").lower()


def test_news_drafting_schema_contract():
    migration = source()

    for item in {
        "newsletter_sources",
        "newsletter_articles",
        "newsletter_ingestion_runs",
        "newsletter_generation_runs",
        "newsletter_draft_articles",
        "fingerprint",
        "canonical_url",
        "prompt_hash",
        "generation_run_id",
        "source_type",
    }:
        assert item in migration

    assert "uq_newsletter_articles_fingerprint" in migration
    assert "uq_newsletter_articles_canonical_url" in migration
    assert "uq_newsletter_draft_articles" in migration
