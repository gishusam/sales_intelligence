import pytest
from scrapy.http import HtmlResponse, Request

from spiders.buyrentkenya import BuyRentKenyaSpider


FIXTURE_LISTING = """
<html>
<body>
  <h1 class="listing-title">Kilimani 3BR Apartment</h1>
  <span class="listing-location">Kilimani, Nairobi</span>
  <span class="listing-price">KES 85,000/mo</span>
  <span class="listing-beds">3 Bedrooms</span>
  <span class="listing-type">Apartment</span>
  <span class="listing-by">Listed by Owner</span>
  <span class="agent-phone">+254 700 123 456</span>
</body>
</html>
"""

# Fixture matches selector: a[href*='houses-apartments']
FIXTURE_INDEX = """
<html>
<body>
  <a class="listing-card" href="/houses-apartments-for-rent/kilimani/12345">Listing 1</a>
  <a class="listing-card" href="/houses-apartments-for-rent/westlands/67890">Listing 2</a>
  <a href="/something-else/99999">Ignore this</a>
</body>
</html>
"""

FIXTURE_EMPTY = "<html><body></body></html>"


def make_response(url, body, spider=None):
    request = Request(url=url)
    return HtmlResponse(url=url, body=body, encoding="utf-8", request=request)


def test_parse_index_yields_requests():
    spider = BuyRentKenyaSpider()
    url = "https://www.buyrentkenya.com/houses-apartments-for-rent/nairobi"
    response = make_response(url, FIXTURE_INDEX)
    results = list(spider.parse(response))
    # Should yield 2 Requests for listing URLs, ignore /something-else/
    requests = [r for r in results if hasattr(r, "url")]
    assert len(requests) == 2
    assert all("houses-apartments" in r.url for r in requests)


def test_parse_listing_extracts_fields():
    spider = BuyRentKenyaSpider()
    url = "https://www.buyrentkenya.com/houses-apartments-for-rent/kilimani/12345"
    response = make_response(url, FIXTURE_LISTING)
    results = list(spider.parse_listing(response))
    assert len(results) == 1
    item = results[0]
    assert item["source"] == "buyrentkenya"
    assert item["raw_location"] == "Kilimani, Nairobi"
    assert item["owner_type"] == "owner"
    assert item["unit_count"] == 3
    assert item["source_url"] == url
    assert item["phone"] == "+254700123456"
    assert item["source_id"] == "12345"


def test_parse_listing_skips_empty():
    spider = BuyRentKenyaSpider()
    url = "https://www.buyrentkenya.com/listing/empty"
    response = make_response(url, FIXTURE_EMPTY)
    results = list(spider.parse_listing(response))
    assert len(results) == 0


def test_parse_index_ignores_non_listing_links():
    spider = BuyRentKenyaSpider()
    url = "https://www.buyrentkenya.com/houses-apartments-for-rent/nairobi"
    # Only non-listing links — should yield nothing
    body = """
    <html><body>
      <a href="/about">About</a>
      <a href="/contact">Contact</a>
    </body></html>
    """
    response = make_response(url, body)
    results = list(spider.parse(response))
    assert len(results) == 0
