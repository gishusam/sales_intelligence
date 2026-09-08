from app.config import settings
from app.routers.apollo import get_apollo_health


def test_apollo_health_reports_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "APOLLO_API_KEY", "")

    response = get_apollo_health()

    assert response == {
        "configured": False,
        "connected": False,
    }


def test_apollo_health_reports_connected(monkeypatch):
    from app.routers import apollo as apollo_router

    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )

    captured = {}

    class FakeApolloClient:
        def __init__(self, api_key):
            captured["api_key"] = api_key

        def health(self):
            return {"is_logged_in": True}

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FakeApolloClient,
        raising=False,
    )

    response = get_apollo_health()

    assert captured["api_key"] == "test-apollo-key"
    assert response == {
        "configured": True,
        "connected": True,
    }


def test_apollo_health_handles_connection_failure(monkeypatch):
    import httpx
    from app.routers import apollo as apollo_router

    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "test-apollo-key",
    )

    class FailingApolloClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def health(self):
            request = httpx.Request(
                "GET",
                "https://api.apollo.io/api/v1/auth/health",
            )
            raise httpx.ConnectTimeout(
                "Apollo unavailable",
                request=request,
            )

    monkeypatch.setattr(
        apollo_router,
        "ApolloClient",
        FailingApolloClient,
    )

    response = get_apollo_health()

    assert response == {
        "configured": True,
        "connected": False,
    }
