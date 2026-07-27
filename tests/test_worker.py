import sys
import types
from pathlib import Path
import re

import pytest


# The legacy controller imports requests even though the Cloud Run worker should
# not call the public API. Keep the red test focused on the missing DB contract.
sys.modules.setdefault("requests", types.SimpleNamespace())


def test_worker_database_url_prefers_single_secret(monkeypatch):
    import github_agent

    get_database_url = getattr(github_agent, "get_database_url", None)
    assert callable(get_database_url)

    expected = "postgresql://worker:secret@pooler.example.com:5432/postgres"
    monkeypatch.setenv("DATABASE_URL", expected)
    assert get_database_url() == expected


def test_worker_updates_run_status_directly_in_postgres(monkeypatch):
    import github_agent

    calls = []

    class Cursor:
        def execute(self, sql, params):
            calls.append((sql, params))

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            calls.append(("commit", None))

        def close(self):
            return None

    monkeypatch.setattr(
        github_agent.psycopg2, "connect", lambda *_args, **_kwargs: Connection()
    )

    github_agent.update_run(7, {
        "status": "success",
        "records_found": 3,
        "duration_seconds": 12.5,
    })

    assert "UPDATE scraper_runs" in calls[0][0]
    assert calls[0][1]["id"] == 7
    assert calls[0][1]["status"] == "success"
    assert calls[-1] == ("commit", None)


def test_worker_retries_transient_database_failure_when_updating_run(monkeypatch):
    import github_agent

    attempts = []
    sleeps = []

    class Cursor:
        def execute(self, _sql, _params):
            return None

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            return None

        def close(self):
            return None

    def connect(_url):
        attempts.append(1)
        if len(attempts) < 3:
            raise github_agent.psycopg2.OperationalError(
                "max clients reached"
            )
        return Connection()

    monkeypatch.setattr(github_agent.psycopg2, "connect", connect)
    monkeypatch.setattr(
        github_agent.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )

    github_agent.update_run(7, {
        "status": "failed",
        "error": "scrape failed",
    })

    assert len(attempts) == 3
    assert sleeps == [1.0, 2.0]


def test_developer_worker_does_not_pass_unsupported_areas_flag():
    import github_agent

    build_command = getattr(github_agent, "build_scraper_command", None)
    assert callable(build_command)

    assert build_command("developers", "Kilimani,Westlands") == [
        "python", "scraper/spiders/developers.py",
        "--enrich", "--limit", "20",
    ]


def test_apartment_worker_passes_requested_areas():
    import github_agent

    assert github_agent.build_scraper_command(
        "apartments", "Kilimani,Westlands"
    ) == [
        "python", "scraper/spiders/apartments.py",
        "--areas", "Kilimani,Westlands",
    ]


def test_worker_leaves_time_to_persist_failure_before_job_timeout():
    import github_agent

    assert getattr(github_agent, "SCRAPER_TIMEOUT_SECONDS", None) == 720


def test_worker_rejects_failed_promotion_process():
    import github_agent

    ensure_success = getattr(github_agent, "ensure_command_succeeded", None)
    assert callable(ensure_success)

    failed = types.SimpleNamespace(returncode=1, stderr="schema mismatch")
    with pytest.raises(RuntimeError, match="Promotion failed"):
        ensure_success(failed, "Promotion")


def test_worker_rejects_zero_record_scrape_as_no_work():
    import github_agent

    ensure_records = getattr(github_agent, "ensure_scrape_produced_records", None)
    assert callable(ensure_records)

    with pytest.raises(RuntimeError, match="zero records"):
        ensure_records({"records_found": 0, "imported": 0})


def test_worker_accepts_scrape_that_found_records():
    import github_agent

    github_agent.ensure_scrape_produced_records({
        "records_found": 3,
        "imported": 0,
    })


def test_playwright_package_matches_worker_browser_image():
    root = Path(__file__).resolve().parents[1]
    dockerfile = (root / "worker.Dockerfile").read_text(encoding="utf-8")
    requirements = (root / "scraper" / "requirements.txt").read_text(
        encoding="utf-8"
    )

    image_version = re.search(
        r"playwright/python:v([0-9.]+)-", dockerfile
    ).group(1)
    package_version = re.search(
        r"^playwright==([0-9.]+)$", requirements, re.MULTILINE
    )

    assert package_version is not None, "Playwright must be pinned explicitly"
    assert package_version.group(1) == image_version


def test_worker_logs_scraper_output_for_zero_record_diagnosis(caplog):
    import github_agent

    result = types.SimpleNamespace(
        stdout="scraper summary",
        stderr="no cards found",
    )

    with caplog.at_level("INFO"):
        github_agent.log_subprocess_output(result, "Scraper")

    assert "scraper summary" in caplog.text
    assert "no cards found" in caplog.text


def test_worker_persists_per_run_records_for_ui(monkeypatch):
    import github_agent

    statements = []

    class Cursor:
        rowcount = 11

        def execute(self, sql, params):
            statements.append((sql, params))

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def commit(self):
            statements.append(("commit", None))

        def close(self):
            return None

    monkeypatch.setattr(
        github_agent.psycopg2, "connect", lambda *_args, **_kwargs: Connection()
    )

    assert github_agent.save_run_records(42, "agencies") == 11
    assert "DELETE FROM scraper_run_records" in statements[0][0]
    assert "INSERT INTO scraper_run_records" in statements[1][0]
    assert "google_places_leads" in statements[1][0]
    assert statements[1][1] == {"run_id": 42}
    assert statements[-1] == ("commit", None)


def test_worker_summarizes_metrics_from_ui_run_records(monkeypatch):
    import github_agent

    class Cursor:
        def execute(self, sql, params):
            assert "FROM scraper_run_records" in sql
            assert params == {"run_id": 42}

        def fetchone(self):
            return (11, 11, 9, 1, 1)

        def close(self):
            return None

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            return None

    monkeypatch.setattr(
        github_agent.psycopg2, "connect", lambda *_args, **_kwargs: Connection()
    )

    assert github_agent.summarize_run_records(42) == {
        "records_found": 11,
        "with_contacts": 11,
        "imported": 9,
        "updated": 0,
        "duplicates": 1,
        "rejected": 1,
    }
