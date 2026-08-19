import importlib

from app import database


def test_email_settings_route_is_registered(monkeypatch):
    # Route registration should not require a real database connection.
    monkeypatch.setattr(
        database.Base.metadata,
        "create_all",
        lambda *args, **kwargs: None,
    )

    main = importlib.import_module("app.main")
    paths = {route.path for route in main.app.routes}

    assert "/api/settings/email" in paths
