import pytest

from spiders.google_consent import dismiss_google_consent


@pytest.mark.asyncio
async def test_dismiss_google_consent_clicks_reject_all():
    calls = []

    class Button:
        @property
        def first(self):
            return self

        async def count(self):
            return 1

        async def click(self):
            calls.append("click")

    class Page:
        url = "https://consent.google.com/m?continue=maps"

        def get_by_role(self, role, name):
            calls.append((role, name.pattern))
            return Button()

        async def wait_for_load_state(self, state):
            calls.append(("wait", state))

    assert await dismiss_google_consent(Page()) is True
    assert calls == [
        ("button", "Reject all"),
        "click",
        ("wait", "domcontentloaded"),
    ]


@pytest.mark.asyncio
async def test_dismiss_google_consent_is_noop_on_maps_results():
    class Page:
        url = "https://www.google.com/maps/search/test"

    assert await dismiss_google_consent(Page()) is False
