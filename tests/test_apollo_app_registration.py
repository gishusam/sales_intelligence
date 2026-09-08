import importlib
import sys

import app.database


def test_main_registers_apollo_health_route(monkeypatch):
    # Avoid opening a database connection while testing route registration.
    monkeypatch.setattr(
        app.database.Base.metadata,
        "create_all",
        lambda *args, **kwargs: None,
    )

    sys.modules.pop("app.main", None)
    main = importlib.import_module("app.main")

    paths = {
        route.path
        for route in main.app.routes
    }

    assert "/api/apollo/health" in paths
