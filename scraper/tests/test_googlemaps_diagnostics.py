import pytest

from spiders.googlemaps import get_page_diagnostic


@pytest.mark.asyncio
async def test_page_diagnostic_captures_title_url_and_visible_text():
    class Body:
        async def inner_text(self):
            return "Before you continue to Google Maps\nAccept all"

    class Page:
        url = "https://consent.google.com/"

        async def title(self):
            return "Before you continue"

        def locator(self, selector):
            assert selector == "body"
            return Body()

    diagnostic = await get_page_diagnostic(Page())

    assert diagnostic == {
        "url": "https://consent.google.com/",
        "title": "Before you continue",
        "body": "Before you continue to Google Maps\nAccept all",
    }
