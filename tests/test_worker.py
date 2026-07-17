import sys
import types

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
