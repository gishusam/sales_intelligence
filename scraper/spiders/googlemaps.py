"""
Google Maps property manager scraper using Playwright.
Selector-verified against live Google Maps output May 2026.

Usage:
  python scraper/spiders/googlemaps.py --areas "Kilimani,Westlands,Muthaiga"
  python scraper/spiders/googlemaps.py  # runs all default areas
  python scraper/spiders/googlemaps.py --visible  # show browser
"""

import asyncio
import logging
import os
import re
import argparse
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
from playwright.async_api import async_playwright
from location_catalog import resolve_location, source_search_terms
from spiders.database import run_transaction
from spiders.google_consent import dismiss_google_consent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [googlemaps] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

# ── Target areas ─────────────────────────────────────────────────
DEFAULT_AREAS = [
    "Kilimani", "Muthaiga", "Westlands", "Lavington",
    "Parklands", "Lenana Road", "Kindaruma Road",
    "Mugunga Road", "South C", "South B", "Kileleshwa",
    "Karen", "Runda", "Spring Valley", "Ruaka",
]

DB_CONFIG = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     os.getenv("POSTGRES_PORT", "5432"),
    "dbname":   os.getenv("POSTGRES_DB",   "nzetu_db"),
    "user":     os.getenv("POSTGRES_USER",  "nzetu"),
    "password": os.getenv("POSTGRES_PASSWORD", "changeme"),
}


def get_db():
    return psycopg2.connect(**DB_CONFIG)


def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS google_places_leads (
                id            SERIAL PRIMARY KEY,
                business_name TEXT NOT NULL,
                area          TEXT,
                address       TEXT,
                phone         TEXT,
                website       TEXT,
                rating        FLOAT,
                review_count  INTEGER,
                category      TEXT,
                search_query  TEXT,
                maps_url      TEXT,
                scraped_at    TIMESTAMPTZ DEFAULT NOW(),
                UNIQUE(business_name, area)
            )
        """)
        conn.commit()
    logger.info("google_places_leads table ready")


def save_results(results):
    if not results:
        return 0
    rows = [(
        r["business_name"], r["area"], r["address"],
        r["phone"], r["website"], r["rating"],
        r["review_count"], r["category"],
        r["search_query"], r["maps_url"],
        datetime.now(timezone.utc),
    ) for r in results]

    def persist(connection):
        with connection.cursor() as cur:
            execute_values(cur, """
                INSERT INTO google_places_leads
                  (business_name, area, address, phone, website,
                   rating, review_count, category, search_query,
                   maps_url, scraped_at)
                VALUES %s
                ON CONFLICT (business_name, area) DO UPDATE SET
                  phone        = EXCLUDED.phone,
                  website      = EXCLUDED.website,
                  rating       = EXCLUDED.rating,
                  review_count = EXCLUDED.review_count,
                  scraped_at   = EXCLUDED.scraped_at
            """, rows)
        return len(rows)

    return run_transaction(
        persist,
        fallback_config=DB_CONFIG,
        operation_name="Saving agency staging rows",
    )


async def get_page_diagnostic(page):
    """Small, non-sensitive snapshot for diagnosing blocked/changed Maps pages."""
    body = await page.locator("body").inner_text()
    return {
        "url": page.url,
        "title": await page.title(),
        "body": body[:1200],
    }


async def extract_cards(page, area, query):
    """
    Extract all business cards from the results panel.
    Confirmed selector: div.Nv2PK (9 cards on test run)
    """
    results = []

    cards = await page.query_selector_all("div.Nv2PK")
    logger.info(f"  {len(cards)} cards found")

    for card in cards:
        try:
            # ── Full text of card for parsing ─────────────────────
            text = (await card.inner_text()).strip()
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if not lines:
                continue

            # Line 0 is always the business name
            name = lines[0]
            if len(name) < 3:
                continue

            # ── Rating — first float in card text ─────────────────
            rating = None
            for line in lines[1:4]:
                m = re.match(r'^(\d\.\d)$', line.strip())
                if m:
                    rating = float(m.group(1))
                    break

            # ── Review count — number in parentheses ──────────────
            review_count = None
            full_text = " ".join(lines)
            m = re.search(r'\((\d[\d,]*)\)', full_text)
            if m:
                review_count = int(m.group(1).replace(",", ""))

            # ── Category — line after rating ──────────────────────
            category = None
            for line in lines[1:5]:
                if "·" in line and not re.search(r'\d{3,}', line):
                    category = line.split("·")[0].strip()
                    break

            # ── Phone — Kenyan number pattern ─────────────────────
            phone = None
            phone_match = re.search(
                r'(\+?254[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}|'
                r'0[17]\d{2}[\s\-]?\d{3}[\s\-]?\d{3})',
                full_text
            )
            if phone_match:
                phone = re.sub(r'[\s\-]', '', phone_match.group())

            # ── Address — line containing area keywords ────────────
            address = None
            addr_keywords = [
                "road", "rd", "avenue", "ave", "street", "st",
                "lane", "close", "way", "drive", "nairobi",
                area.lower().split()[0]
            ]
            for line in lines[2:]:
                if any(kw in line.lower() for kw in addr_keywords):
                    address = line.replace("·", "").strip()
                    break
            # Fallback: use the line that looks most like an address
            if not address:
                for line in lines[2:6]:
                    if len(line) > 10 and not re.match(r'^[\d.]+$', line):
                        address = line.replace("·", "").strip()
                        break

            # ── Maps URL from anchor ───────────────────────────────
            maps_url = None
            link = await card.query_selector("a.hfpxzc")
            if link:
                maps_url = await link.get_attribute("href")

            # ── Website from card ──────────────────────────────────
            website = None
            web_match = re.search(
                r'https?://(?!maps\.google|goo\.gl)[^\s\)]+',
                full_text
            )
            if web_match:
                website = web_match.group().rstrip(".,")

            result = {
                "business_name": name,
                "area":          area,
                "address":       address,
                "phone":         phone,
                "website":       website,
                "rating":        rating,
                "review_count":  review_count,
                "category":      category,
                "search_query":  query,
                "maps_url":      maps_url,
            }
            results.append(result)
            logger.info(
                f"  ✓ {name} | {category or '—'} | "
                f"Rating: {rating or '—'} | Phone: {phone or '—'}"
            )

        except Exception as e:
            logger.debug(f"Card error: {e}")
            continue

    return results


def build_queries(location_value: str) -> list[str]:
    location = resolve_location(location_value)
    return [
        f"{term} {location['qualified_term']}"
        for term in source_search_terms("agencies")
    ]


async def scrape_area(page, location_value):
    """Run two search queries for one area and return combined results."""
    location = resolve_location(location_value)
    area = location["name"]
    queries = build_queries(location_value)

    area_results = []
    seen = set()

    for query in queries:
        url = (
            f"https://www.google.com/maps/search/"
            f"{query.replace(' ', '+')}?hl=en"
        )
        logger.info(f"Searching: {query}")

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await dismiss_google_consent(page)
            # Wait for the results feed to appear
            try:
                await page.wait_for_selector("div.Nv2PK", timeout=15000)
            except Exception:
                logger.warning(
                    "  No results loaded for %s; page=%s",
                    query,
                    await get_page_diagnostic(page),
                )
                continue

            await page.wait_for_timeout(2000)

            cards = await extract_cards(page, area, query)

            for r in cards:
                key = r["business_name"].lower().strip()
                if key not in seen:
                    seen.add(key)
                    area_results.append(r)

            await asyncio.sleep(3)

        except Exception as e:
            logger.error(f"Error scraping '{query}': {e}")
            continue

    return area_results


async def run(areas, headless=True):
    run_transaction(
        ensure_table,
        fallback_config=DB_CONFIG,
        operation_name="Preparing agency staging table",
    )
    total = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        for area in areas:
            location = resolve_location(area)
            logger.info(
                "\n── Area: %s ──────────────────────────",
                location["name"],
            )
            results = await scrape_area(page, area)
            saved = save_results(results)
            total += saved
            logger.info(
                "Area '%s': %s found → %s saved",
                location["name"],
                len(results),
                saved,
            )
            await asyncio.sleep(2)

        await browser.close()

    logger.info(f"\nDone — {total} total records saved to google_places_leads")
    return total


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--area-id", type=str)
    parser.add_argument("--areas", type=str,
                        help="Comma-separated areas e.g. 'Kilimani,Westlands'")
    parser.add_argument("--visible", action="store_true",
                        help="Show browser window")
    args = parser.parse_args()

    areas = (
        [args.area_id]
        if args.area_id
        else (
            [a.strip() for a in args.areas.split(",")]
            if args.areas
            else DEFAULT_AREAS
        )
    )

    asyncio.run(run(areas, headless=not args.visible))


if __name__ == "__main__":
    main()
