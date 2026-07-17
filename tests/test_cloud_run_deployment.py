import os


os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "localhost")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret")
os.environ.setdefault("ALLOWED_ORIGINS", "https://example.com")


def test_cloud_run_job_request_builder_exists():
    from app.routers import scraper

    assert callable(getattr(scraper, "build_cloud_run_job_request", None))


def test_cloud_run_job_request_contains_one_bounded_task():
    from app.routers.scraper import build_cloud_run_job_request

    assert build_cloud_run_job_request(
        42, "apartments", ["Kilimani", "Westlands"]
    ) == {
        "overrides": {
            "containerOverrides": [{
                "args": [
                    "--run-id", "42",
                    "--scraper-type", "apartments",
                    "--areas", "Kilimani,Westlands",
                ]
            }],
            "taskCount": 1,
            "timeout": "900s",
        }
    }


def test_execute_cloud_run_job_uses_metadata_identity():
    from app.routers import scraper

    execute = getattr(scraper, "execute_cloud_run_job", None)
    assert callable(execute)

    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Client:
        def get(self, url, **kwargs):
            calls.append(("GET", url, kwargs))
            return Response({"access_token": "metadata-token"})

        def post(self, url, **kwargs):
            calls.append(("POST", url, kwargs))
            return Response({"name": "operations/job-123"})

    payload = {"overrides": {"taskCount": 1}}
    operation = execute(
        "sales-intelligens", "europe-west1", "sales-scraper",
        payload, http_client=Client(),
    )

    assert operation == "operations/job-123"
    assert calls[0][0:2] == (
        "GET",
        "http://metadata.google.internal/computeMetadata/v1/instance/"
        "service-accounts/default/token",
    )
    assert calls[1][1] == (
        "https://run.googleapis.com/v2/projects/sales-intelligens/"
        "locations/europe-west1/jobs/sales-scraper:run"
    )
    assert calls[1][2]["headers"]["Authorization"] == "Bearer metadata-token"
    assert calls[1][2]["json"] == payload


def test_default_allowed_origins_is_a_string(monkeypatch):
    from app.config import Settings

    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    configured = Settings(
        _env_file=None,
        POSTGRES_USER="test",
        POSTGRES_PASSWORD="test",
        POSTGRES_DB="test",
        POSTGRES_HOST="localhost",
        SECRET_KEY="app-secret",
        JWT_SECRET_KEY="jwt-secret",
    )

    assert isinstance(configured.ALLOWED_ORIGINS, str)


def test_authentication_has_no_insecure_jwt_fallback():
    import inspect
    from app import auth

    source = inspect.getsource(auth)
    assert "change-this-in-production-please" not in source
    assert "settings.JWT_SECRET_KEY" in source
