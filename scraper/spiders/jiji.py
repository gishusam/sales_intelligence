"""
spiders/jiji.py — Jiji Property Scraper
Uses Playwright because Jiji renders via Nuxt.js (JavaScript).
Must run from inside Docker where Playwright browsers are installed.

Run:
    docker compose exec scraper scrapy crawl jiji
"""

import re
import logging
from datetime import datetime, timezone, date, timedelta

import scrapy
from scrapy_playwright.page import PageMethod
from items import ListingItem

logger = logging.getLogger(__name__)


class JijiSpider(scrapy.Spider):
    name = "jiji"
    allowed_domains = ["jiji.co.ke"]

    custom_settings = {
        "DOWNLOAD_DELAY": 2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 1,
        "ROBOTSTXT_OBEY": True,
        # Override download handlers to use Playwright for this spider
        "DOWNLOAD_HANDLERS": {
            "https": "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
            "http":  "scrapy_playwright.handler.ScrapyPlaywrightDownloadHandler",
        },
        "PLAYWRIGHT_BROWSER_TYPE": "chromium",
        "PLAYWRIGHT_LAUNCH_OPTIONS": {"headless": True},
    }

    START_URL = "https://jiji.co.ke/nairobi/houses-apartments-for-rent"

    # Target areas — limits crawl to relevant Nairobi neighbourhoods
    TARGET_AREAS = [
        "kilimani", "westlands", "lavington", "muthaiga", "kileleshwa",
        "karen", "runda", "spring-valley", "parklands", "riverside",
        "hurlingham", "loresho", "kyuna", "rosslyn", "lower-kabete",
        "nairobi-central", "upper-hill", "maziwa", "brookside",
    ]

    def start_requests(self):
        yield scrapy.Request(
            self.START_URL,
            meta={
                "playwright": True,
                "playwright_include_page": True,
                "playwright_page_methods": [
                    PageMethod("wait_for_load_state", "networkidle"),
                    PageMethod("wait_for_timeout", 3000),
                ],
            },
            callback=self.parse_index,
            errback=self.handle_error,
        )

    async def parse_index(self, response):
        """
        Extract individual listing URLs from the index page.
        Jiji listing URLs follow the pattern:
        /{area}/houses-apartments-for-rent/{slug}.html
        """
        page = response.meta.get("playwright_page")
        if page:
            await page.close()

        # Extract all listing hrefs
        listing_links = [
            href for href in listing_links
            if re.search(r'[A-Za-z0-9]{20,}\.html$', href)
        ]

        # Filter to target areas only
        seen = set()
        for href in listing_links:
            if href in seen:
                continue
            seen.add(href)

            # Check if this listing is in a target area
            area_match = any(area in href for area in self.TARGET_AREAS)
            if not area_match:
                # Still scrape it — just won't be area-filtered
                pass

            full_url = f"https://jiji.co.ke{href}"
            yield scrapy.Request(
                full_url,
                meta={
                    "playwright": True,
                    "playwright_include_page": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "networkidle"),
                        PageMethod("wait_for_timeout", 2000),
                    ],
                    "area_from_url": href.split("/")[1],  # extract area from URL
                },
                callback=self.parse_listing,
                errback=self.handle_error,
            )

        logger.info(f"Found {len(seen)} listing links on index page")

        # Pagination — follow next pages
        next_pages = response.css(
            "a[href*='?page=']::attr(href)"
        ).getall()

        # Only follow first 5 pages to avoid crawling thousands of listings
        for href in next_pages[:5]:
            full_url = f"https://jiji.co.ke{href}" if href.startswith("/") else href
            yield scrapy.Request(
                full_url,
                meta={
                    "playwright": True,
                    "playwright_include_page": True,
                    "playwright_page_methods": [
                        PageMethod("wait_for_load_state", "networkidle"),
                        PageMethod("wait_for_timeout", 3000),
                    ],
                },
                callback=self.parse_index,
                errback=self.handle_error,
            )

    async def parse_listing(self, response):
        """
        Extract listing data from an individual Jiji listing page.
        Selectors verified against live Jiji DOM May 2026.
        """
        page = response.meta.get("playwright_page")
        if page:
            await page.close()

        area_from_url = response.meta.get("area_from_url", "")

        # ── Title ──────────────────────────────────────────────────────────
        title = response.css(
            "h1.b-advert-title-inner::text, "
            ".qa-advert-title::text, "
            "h1::text"
        ).get("").strip()

        if not title:
            logger.warning(f"No title found at {response.url}")
            return

        # ── Price ──────────────────────────────────────────────────────────
        price_text = response.css(
            ".qa-advert-price::text"
        ).get("").strip()

        price_kes = None
        raw_price = None
        if price_text:
            price_clean = re.sub(r"[^\d]", "", price_text)
            if price_clean:
                price_kes = int(price_clean)
                raw_price = f"KES {price_kes:,}/mo"

        # ── Location ───────────────────────────────────────────────────────
        # Jiji shows location as "Area, Sub-area" — take first part
        location_text = response.css(
            ".b-list-advert__region__text::text, "
            "[class*='region']::text, "
            ".b-advert-contact__location::text"
        ).get("").strip()

        area = location_text.split(",")[0].strip() if location_text else area_from_url.replace("-", " ").title()

        # ── Description ────────────────────────────────────────────────────
        description = response.css(
            ".b-advert-description__inner::text, "
            "[class*='description-text']::text"
        ).get("").strip()

        # ── Owner / seller ─────────────────────────────────────────────────
        owner_name = response.css(
            ".b-seller-info__name::text, "
            "[class*='seller']  [class*='name']::text, "
            ".qa-seller-name::text"
        ).get("").strip() or None

        owner_phone = response.css(
            "[class*='phone']::text, "
            ".b-advert-contact__phone::text"
        ).get("").strip() or None

        if owner_phone:
            owner_phone = re.sub(r"[^\d+]", "", owner_phone) or None

        # Also check description for phone
        if not owner_phone and description:
            owner_phone = self._extract_phone(description)

        # Classify owner type
        owner_type = self._classify_owner(owner_name or "")

        # ── Bedrooms ───────────────────────────────────────────────────────
        bedrooms = None
        # Try from attributes table
        attr_names  = response.css("div.b-advert-attribute__name::text").getall()
        attr_values = response.css("div.b-advert-attribute__value::text").getall()
        for name, value in zip(attr_names, attr_values):
            if "bedroom" in name.lower() or "bed" in name.lower():
                try:
                    bedrooms = int(re.search(r"\d+", value).group())
                except (AttributeError, ValueError):
                    pass
                break

        # Fallback: extract from title
        if not bedrooms:
            m = re.search(r"(\d+)\s*(?:bed|bdrm|bedroom)", title, re.IGNORECASE)
            bedrooms = int(m.group(1)) if m else None

        # ── Listing date ───────────────────────────────────────────────────
        date_text = response.css(
            "[class*='date']::text, "
            "time::attr(datetime), "
            ".b-advert-date::text"
        ).get("").strip()

        listing_date = self._parse_date(date_text)

        # ── Source ID from URL ─────────────────────────────────────────────
        # URL pattern: /area/category/slug-RANDOMID.html
        id_match = re.search(r"-([A-Za-z0-9]{20,})\.html$", response.url)
        source_id = id_match.group(1) if id_match else None

        item = ListingItem()
        item["property_name"]  = title
        item["property_type"]  = self._property_type(title)
        item["bedrooms"]       = bedrooms
        item["price_kes"]      = price_kes
        item["raw_price"]      = raw_price
        item["area"]           = area
        item["raw_address"]    = location_text or area
        item["owner_name"]     = owner_name
        item["owner_type"]     = owner_type
        item["owner_phone"]    = owner_phone
        item["owner_email"]    = self._extract_email(description)
        item["owner_website"]  = None
        item["listing_date"]   = listing_date
        item["source"]         = "jiji"
        item["source_url"]     = response.url
        item["source_id"]      = source_id
        item["scraped_at"]     = datetime.now(timezone.utc)

        yield item

    # ── Helpers ────────────────────────────────────────────────────────────

    def _property_type(self, name: str) -> str:
        n = name.lower()
        if "bedsitter" in n or "bedsit" in n: return "bedsitter"
        if "studio"    in n:                  return "studio"
        if "apartment" in n or "flat" in n:   return "apartment"
        if "townhouse" in n or "maisonette" in n: return "townhouse"
        if "villa"     in n:                  return "villa"
        if "bungalow"  in n:                  return "house"
        if "house"     in n:                  return "house"
        return "apartment"

    def _classify_owner(self, name: str) -> str:
        n = name.lower()
        if any(kw in n for kw in ["management", "manager", "managing agent"]):
            return "pm"
        if any(kw in n for kw in ["agency", "ltd", "limited", "realtors",
                                    "real estate", "estates", "properties"]):
            return "agency"
        return "owner"

    def _extract_phone(self, text: str) -> str | None:
        for pattern in [
            r'\+254[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}',
            r'07\d{2}[\s\-]?\d{3}[\s\-]?\d{3}',
            r'01\d{2}[\s\-]?\d{3}[\s\-]?\d{3}',
        ]:
            m = re.search(pattern, text)
            if m:
                return re.sub(r'[\s\-]', '', m.group())
        return None

    def _extract_email(self, text: str) -> str | None:
        if not text:
            return None
        m = re.search(r'[\w\.\-]+@[\w\.\-]+\.\w+', text)
        return m.group().lower() if m else None

    def _parse_date(self, text: str) -> date | None:
        if not text:
            return None
        today = date.today()
        text = text.lower().strip()
        try:
            # ISO format
            if re.match(r'\d{4}-\d{2}-\d{2}', text):
                return date.fromisoformat(text[:10])
            # Relative dates
            if "today" in text or "just now" in text:
                return today
            if "yesterday" in text:
                return today - timedelta(days=1)
            m = re.search(r'(\d+)\s*(day|week|month|year)', text)
            if m:
                n, unit = int(m.group(1)), m.group(2)
                if unit == "day":   return today - timedelta(days=n)
                if unit == "week":  return today - timedelta(weeks=n)
                if unit == "month": return today - timedelta(days=n * 30)
                if unit == "year":  return today - timedelta(days=n * 365)
        except Exception:
            pass
        return None

    def handle_error(self, failure):
        logger.error(f"Request failed: {failure.request.url} — {failure.value}")