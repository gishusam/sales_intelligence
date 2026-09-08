from app.config import settings


def test_apollo_api_key_setting_exists():
    assert hasattr(settings, "APOLLO_API_KEY")


def test_apollo_api_key_defaults_to_empty_string():
    assert settings.APOLLO_API_KEY == ""
