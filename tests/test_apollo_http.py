import importlib
import sys

from fastapi.testclient import TestClient

import app.database
from app.config import settings


def test_apollo_health_endpoint_returns_safe_status(monkeypatch):
    monkeypatch.setattr(
        app.database.Base.metadata,
        "create_all",
        lambda *args, **kwargs: None,
    )

    monkeypatch.setattr(
        settings,
        "APOLLO_API_KEY",
        "",
    )

    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")

    client = TestClient(main.app)

    response = client.get("/api/apollo/health")

    assert response.status_code == 200
    assert response.json() == {
        "configured": False,
        "connected": False,
    }
