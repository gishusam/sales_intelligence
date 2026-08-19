"""
apartments.py — Apartment Discovery + Enrichment in one pass
Searches Google Maps for residential buildings per area,
then immediately visits each result page to extract contact details.
Writes everything to apartment_staging — one table, one process.

Usage:
  python scraper/spiders/apartments.py
  python scraper/spiders/apartments.py --areas "Kilimani,Westlands"
  python scraper/spiders/apartments.py --visible
  python scraper/spiders/apartments.py --enrich-top 20
"""

import asyncio
import logging
import os
import re
import argparse
import unicodedata
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import execute_values
from playwright.async_api import async_playwright
from location_catalog import resolve_location, source_search_terms
from spiders.database import _connect, run_transaction
from spiders.google_consent import dismiss_google_consent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [apartments] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

ENRICHMENT_SCORE_THRESHOLD = 40
DEFAULT_AREAS = [
    "Kilimani", "Kileleshwa", "Westlands", "Lavington",
    "South B", "South C", "Parklands", "Muthaiga",
    "Karen", "Runda", "Spring Valley", "Ruaka",
    "Syokimau", "Kasarani", "Ngara", "Pangani",
    "Embakasi", "Langata", "Kahawa West", "Donholm",
]

KEYWORDS = [
    "apartments",
    "residential apartments",
    "flats for rent",
    "residences",
    "apartment complex",
    "gated community apartments",
    "towers apartments",
]

PREMIUM_AREAS = {
    "kilimani", "kileleshwa", "westlands", "lavington",
    "muthaiga", "karen", "runda", "spring valley", "parklands",
}

HIGH_DENSITY_AREAS = {
    "south b", "south c", "ruaka", "kasarani", "syokimau",
    "ngara", "pangani", "embakasi", "langata", "donholm",
}

BUILDING_KEYWORDS = [
    "apartments", "apartment", "flats", "flat",
    "residences", "residence", "towers", "tower",
    "court", "gardens", "estate", "villas", "villa",
    "suites", "heights", "place", "park", "close",
    "block", "complex", "homes",
]

MANAGEMENT_CATEGORIES = [
    "apartment building", "residential", "condominium",
    "housing", "property management", "gated community",
    "real estate", "lodging",
]

DB_CONFIG = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    dbname=os.getenv("POSTGRES_DB", "nzetu_db"),
    user=os.getenv("POSTGRES_USER", "nzetu"),
    password=os.getenv("POSTGRES_PASSWORD", "changeme"),
)



# ── Helpers ───────────────────────────────────────────────────────

def normalize(name: str) -> str:
    name = name.lower().strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    name = re.sub(r"[^\w\s]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def extract_coords(url: str) -> tuple:
    if not url:
        return None, None
    m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    m = re.search(r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)", url)
    if m:
        return float(m.group(1)), float(m.group(2))
    return None, None


def score_building(name, category, review_count, rating, area, management_company=None,) -> tuple:
    score = 0
    reasons = []

    name_lower = (name or "").lower()
    category_lower = (category or "").lower()
    area_lower = (area or "").lower()
    management_lower = (management_company or "").lower()

    large_property_terms = [
        "estate",
        "complex",
        "gated community",
    ]

    medium_property_terms = [
        "towers",
        "residences",
        "heights",
        "gardens",
        "villas",
    ]

    basic_property_terms = [
        "apartments",
        "apartment",
        "flats",
        "flat",
    ]

    if any(term in name_lower for term in large_property_terms):
        score += 30
        reasons.append("large property/estate signal (+30)")

    elif any(term in name_lower for term in medium_property_terms):
        score += 20
        reasons.append("medium/large residential property signal (+20)")

    elif any(term in name_lower for term in basic_property_terms):
        score += 10
        reasons.append("apartment property signal (+10)")

      # ---------------------------------------------------------
    # 2. PROFESSIONAL MANAGEMENT EVIDENCE — max 20
    # ---------------------------------------------------------

    if management_company:
        score += 20
        reasons.append(
            f"identified management company: {management_company} (+20)"
        )

    elif any(
        term in category_lower
        for term in [
            "property management",
            "real estate",
        ]
    ):
        score += 10
        reasons.append("professional management category signal (+10)")


    # ---------------------------------------------------------
    # 3. MULTI-BUILDING / COMPLEX INDICATOR — max 15
    # ---------------------------------------------------------

    multi_building_terms = [
        "phase",
        "block",
        "tower",
        "wing",
        "estate",
        "complex",
    ]

    if any(term in name_lower for term in multi_building_terms):
        score += 15
        reasons.append("multi-building/complex indicator (+15)")


    # ---------------------------------------------------------
    # 4. ACTIVITY / REVIEW VOLUME — max 15
    # ---------------------------------------------------------

    reviews = review_count or 0

    if reviews >= 200:
        score += 15
        reasons.append(f"{reviews} Google reviews (+15)")

    elif reviews >= 100:
        score += 12
        reasons.append(f"{reviews} Google reviews (+12)")

    elif reviews >= 50:
        score += 10
        reasons.append(f"{reviews} Google reviews (+10)")

    elif reviews >= 20:
        score += 7
        reasons.append(f"{reviews} Google reviews (+7)")

    elif reviews >= 5:
        score += 3
        reasons.append(f"{reviews} Google reviews (+3)")


    # ---------------------------------------------------------
    # 5. PROPERTY CATEGORY FIT — max 10
    # ---------------------------------------------------------

    property_categories = [
        "apartment building",
        "residential",
        "condominium",
        "housing",
        "gated community",
    ]

    if any(term in category_lower for term in property_categories):
        score += 10
        reasons.append(f"strong property category: {category} (+10)")


    # ---------------------------------------------------------
    # 6. AREA ATTRACTIVENESS — max 5
    # ---------------------------------------------------------

    if area_lower in PREMIUM_AREAS:
        score += 5
        reasons.append("premium market area (+5)")

    elif area_lower in HIGH_DENSITY_AREAS:
        score += 3
        reasons.append("high-density market area (+3)")


    # ---------------------------------------------------------
    # 7. GOOGLE RATING — max 5
    # ---------------------------------------------------------

    current_rating = rating or 0

    if current_rating >= 4.5:
        score += 5
        reasons.append(f"rating {current_rating} (+5)")

    elif current_rating >= 4.0:
        score += 4
        reasons.append(f"rating {current_rating} (+4)")

    elif current_rating >= 3.5:
        score += 2
        reasons.append(f"rating {current_rating} (+2)")


    # Never exceed 100.
    score = min(score, 100)

    return score, " | ".join(reasons)


# ── Discovery ─────────────────────────────────────────────────────

def build_queries(location_value: str) -> list[str]:
    location = resolve_location(location_value)
    return [
        f"{term} {location['qualified_term']}"
        for term in source_search_terms("apartments")
    ]


async def discover_area(page, location_value: str) -> list[dict]:
    """Search Google Maps and extract all building cards for an area."""
    location = resolve_location(location_value)
    area = location["name"]
    all_buildings = []
    seen = set()

    for query in build_queries(location_value):
        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}?hl=en"

        logger.info(f"  Searching: {query}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
            await dismiss_google_consent(page)
            await page.wait_for_timeout(2000)

            try:
                await page.wait_for_selector("div.Nv2PK", timeout=12000)
            except Exception:
                logger.warning(f"  No results for: {query}")
                continue

            feed = await page.query_selector('div[role="feed"]')
            for _ in range(4):
                if feed:
                    await feed.evaluate("el => el.scrollTop += 600")
                else:
                    await page.evaluate("window.scrollBy(0, 600)")
                await page.wait_for_timeout(1000)

            cards = await page.query_selector_all("div.Nv2PK")
            logger.info(f"  {len(cards)} cards found")

            for card in cards:
                try:
                    text = (await card.inner_text()).strip()
                    lines = [l.strip() for l in text.splitlines() if l.strip()]
                    if not lines or len(lines[0]) < 3:
                        continue

                    name = lines[0]
                    norm = normalize(name)
                    if norm in seen:
                        continue
                    seen.add(norm)

                    full = " ".join(lines)

                    rating = None
                    for line in lines[1:4]:
                        m = re.match(r'^(\d\.\d)$', line)
                        if m:
                            rating = float(m.group(1))
                            break

                    review_count = None
                    m = re.search(r'\((\d[\d,]*)\)', full)
                    if m:
                        review_count = int(m.group(1).replace(",", ""))

                    category = None
                    for line in lines[1:5]:
                        if "·" in line and not re.search(r'\d{3,}', line):
                            category = line.split("·")[0].strip()
                            break

                    phone = None
                    pm = re.search(
                        r'(\+?254[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}|'
                        r'0[17]\d{2}[\s\-]?\d{3}[\s\-]?\d{3})',
                        full
                    )
                    if pm:
                        phone = re.sub(r'[\s\-]', '', pm.group())

                    maps_url = None
                    link = await card.query_selector("a.hfpxzc")
                    if link:
                        maps_url = await link.get_attribute("href")

                    lat, lng = extract_coords(maps_url or "")
                    score, reasons = score_building(
                        name, category, review_count, rating, area
                    )

                    all_buildings.append({
                        "building_name":   name,
                        "normalized_name": norm,
                        "search_area":     area,
                        "search_query":    query,
                        "category":        category,
                        "rating":          rating,
                        "review_count":    review_count,
                        "maps_url":        maps_url,
                        "latitude":        lat,
                        "longitude":       lng,
                        "lead_score":      score,
                        "score_reasons":   reasons,
                        "contact_phone":   phone,
                    })

                except Exception as e:
                    logger.debug(f"Card error: {e}")

            await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"  Page error on '{query}': {e}")

    return all_buildings


# ── Enrichment ────────────────────────────────────────────────────

async def enrich_building(page, building: dict) -> dict:
    """
    Visit the Google Maps detail page and extract contact intelligence.

    KEY FIX: Google Maps embeds the phone number directly in a tel: link
    href attribute — e.g. href="tel:+254712345678". This is more reliable
    than trying to find and click a button, which changes with every
    Google Maps UI update.

    Also waits longer (4s) for the contact panel to fully render before
    trying to extract — the panel loads after the map tiles, not immediately.
    """
    maps_url = building.get("maps_url")
    if not maps_url:
        building["enrichment_status"] = "skipped"
        building["confidence"] = "low"
        return building

    try:
        await page.goto(maps_url, wait_until="domcontentloaded", timeout=30000)
        await dismiss_google_consent(page)

        # ── FIX: increased wait from 2500ms to 4000ms ──────────────────────
        # The contact panel (phone, website) loads after the map tiles.
        # 2.5 seconds was not enough — 4 seconds catches it reliably.
        await page.wait_for_timeout(4000)

        try:
            await page.wait_for_selector("h1", timeout=8000)
        except Exception:
            building["enrichment_status"] = "failed"
            return building

        full_text = await page.inner_text("body")
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        # ── PHONE: read from tel: href directly ────────────────────────────
        # Google Maps always puts the phone in href="tel:+254..."
        # Reading the href is far more reliable than button text or tooltips.
        phone = building.get("contact_phone")
        if not phone:
            try:
                await page.wait_for_selector('a[href^="tel:"]', timeout=5000)
                tel_link = await page.query_selector('a[href^="tel:"]')
                if tel_link:
                    href = await tel_link.get_attribute("href")
                    if href:
                        phone = re.sub(r'[^\d+]', '', href.replace("tel:", "")) or None
            except Exception:
                pass  # tel: link not present — building has no phone on Maps

        # Fallback: regex scan of full page text
        if not phone:
            pm = re.search(
                r'(\+?254[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}|'
                r'0[17]\d{2}[\s\-]?\d{3}[\s\-]?\d{3})',
                full_text
            )
            if pm:
                phone = re.sub(r'[\s\-]', '', pm.group())

        # ── WEBSITE ────────────────────────────────────────────────────────
        website = building.get("contact_website")
        web_btn = await page.query_selector(
            'a[data-tooltip*="website"], a[aria-label*="website"]'
        )
        if web_btn:
            href = await web_btn.get_attribute("href")
            if href and "google.com" not in href:
                website = href
        if not website:
            wm = re.search(
                r'https?://(?!maps\.google|goo\.gl|google\.com)'
                r'[a-zA-Z0-9\-\.]+\.[a-zA-Z]{2,}[^\s\)\"\']*',
                full_text
            )
            if wm:
                website = wm.group().rstrip(".,")

        # ── EMAIL ──────────────────────────────────────────────────────────
        email = building.get("contact_email")
        em = re.search(r'[\w\.\-]+@[\w\.\-]+\.[a-zA-Z]{2,}', full_text)
        if em:
            email = em.group().lower()

        # ── MANAGEMENT COMPANY ─────────────────────────────────────────────
        management_company = building.get("management_company")

        if not management_company:
            management_patterns = [
                r"managed by\s*[:\-]?\s*([^\n|•]{3,80})",
                r"developed by\s*[:\-]?\s*([^\n|•]{3,80})",
                r"marketed by\s*[:\-]?\s*([^\n|•]{3,80})",
                r"property management by\s*[:\-]?\s*([^\n|•]{3,80})",
            ]

            for pattern in management_patterns:
                match = re.search(
                    pattern,
                    full_text,
                    re.IGNORECASE,
                )

                if not match:
                    continue

                candidate = match.group(1).strip()

                bad_management_values = [
                    "rating:",
                    "reviews",
                    "stars",
                    "minutes",
                    "hours",
                ]

                looks_like_time = bool(
                    re.fullmatch(r"\d{1,2}:\d{2}", candidate)
                )

                if (
                    not looks_like_time
                    and not any(
                        bad in candidate.lower()
                        for bad in bad_management_values
                    )
                ):
                    management_company = candidate
                    break

        # ── SOCIAL MEDIA ───────────────────────────────────────────────────
        socials = []
        for platform, pat in [
            ("facebook",  r'facebook\.com/[\w\.\-/]+'),
            ("instagram", r'instagram\.com/[\w\.\-/]+'),
        ]:
            m = re.search(pat, full_text, re.IGNORECASE)
            if m:
                socials.append(f"{platform}: {m.group()}")

        # ── CONFIDENCE SCORE ───────────────────────────────────────────────
        found = sum(bool(x) for x in [phone, website, email, management_company])
        confidence = "high" if found >= 3 else "medium" if found >= 1 else "low"

        has_direct_contact = bool(phone or email)

        enrichment_status = (
            "done"
            if has_direct_contact
            else "no_contact"
        )

        building.update({
            "contact_phone":      phone,
            "contact_email":      email,
            "contact_website":    website,
            "management_company": management_company,
            "social_media":       " | ".join(socials) if socials else None,
            "enrichment_status":  enrichment_status,
            "confidence":         confidence,
            "enriched_at":        datetime.now(timezone.utc),
        })

        logger.info(
            f"    ✓ phone:{phone or '—'} web:{'yes' if website else '—'} "
            f"mgmt:{management_company or '—'} conf:{confidence}"
        )

    except Exception as e:
        building["enrichment_status"] = "failed"
        building["confidence"] = "low"
        logger.debug(f"    Enrich error: {e}")

    return building


# ── Database ──────────────────────────────────────────────────────

def save_buildings(buildings: list[dict], run_id: int | None = None) -> int:
    if not buildings:
        return 0

    rows = [(
        b.get("building_name"),
        b.get("normalized_name"),
        b.get("search_area"),
        b.get("search_query"),
        b.get("category"),
        b.get("rating"),
        b.get("review_count"),
        b.get("maps_url"),
        b.get("latitude"),
        b.get("longitude"),
        b.get("lead_score", 0),
        b.get("score_reasons"),
        b.get("contact_phone"),
        b.get("contact_email"),
        b.get("contact_website"),
        b.get("management_company"),
        b.get("social_media"),
        b.get("enrichment_status", "pending"),
        b.get("confidence", "low"),
        b.get("enriched_at"),
        datetime.now(timezone.utc),
        run_id,
    ) for b in buildings]

    def persist(connection):
        with connection.cursor() as cur:
            execute_values(cur, """
                INSERT INTO apartment_staging (
                    building_name, normalized_name,
                    search_area, search_query,
                    category, rating, review_count,
                    maps_url, latitude, longitude,
                    lead_score, score_reasons,
                    contact_phone, contact_email, contact_website,
                    management_company, social_media,
                    enrichment_status, confidence,
                    enriched_at, scraped_at, run_id
                ) VALUES %s
                ON CONFLICT (normalized_name, search_area) DO UPDATE SET
                    rating             = EXCLUDED.rating,
                    review_count       = EXCLUDED.review_count,
                    lead_score         = EXCLUDED.lead_score,
                    contact_phone      = COALESCE(EXCLUDED.contact_phone,
                                                  apartment_staging.contact_phone),
                    contact_email      = COALESCE(EXCLUDED.contact_email,
                                                  apartment_staging.contact_email),
                    contact_website    = COALESCE(EXCLUDED.contact_website,
                                                  apartment_staging.contact_website),
                    management_company = COALESCE(
                        EXCLUDED.management_company,
                        apartment_staging.management_company
                    ),
                    enrichment_status  = EXCLUDED.enrichment_status,
                    confidence         = EXCLUDED.confidence,
                    enriched_at        = EXCLUDED.enriched_at,
                    scraped_at         = EXCLUDED.scraped_at,
                    run_id             = EXCLUDED.run_id
            """, rows)
        return len(rows)

    return run_transaction(
        persist,
        fallback_config=DB_CONFIG,
        operation_name="Saving apartment staging rows",
    )

def rescore_existing():
    """
    Recalculate Sales Opportunity Scores for existing apartment_staging
    records using the current score_building() logic.
    """

    conn = _connect(DB_CONFIG)

    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                id,
                building_name,
                category,
                review_count,
                rating,
                search_area,
                management_company
            FROM apartment_staging
        """)

        records = cur.fetchall()

        logger.info(
            f"Re-scoring {len(records)} existing apartment records..."
        )

        updated = 0

        for (
            record_id,
            name,
            category,
            review_count,
            rating,
            area,
            management_company,
        ) in records:

            score, reasons = score_building(
                name,
                category,
                review_count,
                rating,
                area,
                management_company,
            )

            cur.execute("""
                UPDATE apartment_staging
                SET
                    lead_score = %s,
                    score_reasons = %s
                WHERE id = %s
            """, (
                score,
                reasons,
                record_id,
            ))

            updated += 1

        conn.commit()

    logger.info(
        f"Re-scored {updated} apartment staging records."
    )

    conn.close()

# ── Re-enrich existing records missing phone ──────────────────────

async def reenrich_missing(areas: list[str], limit: int, headless: bool):
    """
    Re-run enrichment only on records that already exist in the DB
    but are missing contact_phone. Useful after the tel: href fix
    without having to re-scrape everything from scratch.

    Usage:
        python scraper/spiders/apartments.py --reenrich-missing
        python scraper/spiders/apartments.py --reenrich-missing --areas "Kilimani"
    """
    conn = _connect(DB_CONFIG)


    with conn.cursor() as cur:
        area_filter = "AND search_area = ANY(%s)" if areas != DEFAULT_AREAS else ""
        params = [limit]
        if areas != DEFAULT_AREAS:
            params = [areas, limit]

        cur.execute(f"""
            SELECT DISTINCT ON (maps_url) id, building_name, maps_url, search_area,category,review_count,rating, management_company
            FROM apartment_staging
            WHERE lead_score >= 40
                AND maps_url IS NOT NULL
                AND enrichment_status != 'skipped'
                AND NOT (
                    enrichment_status = 'no_contact'
                    AND enriched_at >= NOW() - INTERVAL '30 days'
                )
                AND (
                    (
                       contact_phone is NULL
                       AND contact_email is NULL
                    )
                    OR (
                         lead_score < 50
                         AND management_company is NULL
                    )
                )
              {area_filter}
            ORDER BY maps_url, lead_score DESC
            LIMIT %s
        """, params)
        records = cur.fetchall()

    logger.info(f"Re-enriching {len(records)} records missing phone...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox",
                  "--disable-blink-features=AutomationControlled"]
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

        for (record_id, name, maps_url, area, category, review_count, rating, existing_management_company, )in records:
            logger.info(f"  [{record_id}] {name} ({area})")
            building = {
                "building_name": name,
                "maps_url": maps_url,
                "search_area": area,
                "category": category,
                "review_count": review_count,
                "rating": rating,
                "contact_phone": None,
                "contact_email": None,
            }
            await enrich_building(page, building)

            management_for_score = (
                building.get("management_company")
                or existing_management_company
            )

            final_score, final_reasons = score_building(
                name,
                category,
                review_count,
                rating,
                area,
                management_for_score,
            )

            building["lead_score"] = final_score
            building["score_reasons"] = final_reasons

            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE apartment_staging SET
                        contact_phone      = COALESCE(%s, contact_phone),
                        contact_email      = COALESCE(%s, contact_email),
                        contact_website    = COALESCE(%s, contact_website),
                        management_company = COALESCE(%s, management_company),

                        lead_score         = %s,
                        score_reasons      = %s,

                        enrichment_status  = %s,
                        confidence         = %s,
                        enriched_at        = NOW()

                    WHERE id = %s
                """, (
                    building.get("contact_phone"),
                    building.get("contact_email"),
                    building.get("contact_website"),
                    management_for_score,

                    building.get("lead_score"),
                    building.get("score_reasons"),

                    building.get("enrichment_status", "failed"),
                    building.get("confidence", "low"),
                    record_id,
                ))

                cur.execute("""
                    UPDATE apartment_staging SET
                        contact_phone      = COALESCE(%s, contact_phone),
                        contact_email      = COALESCE(%s, contact_email),
                        contact_website    = COALESCE(%s, contact_website),
                        management_company = COALESCE(%s, management_company),
                        enrichment_status  = %s,
                        confidence         = %s,
                        enriched_at        = NOW()

                    WHERE maps_url = %s
                    AND id <> %s
                """, (
                    building.get("contact_phone"),
                    building.get("contact_email"),
                    building.get("contact_website"),
                    management_for_score,
                    building.get("enrichment_status", "failed"),
                    building.get("confidence", "low"),
                    maps_url,
                    record_id,
                ))

                conn.commit()

            await asyncio.sleep(2)

        await browser.close()

    # Summary
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                COUNT(*) as total,
                COUNT(contact_phone) as with_phone,
                COUNT(contact_website) as with_website
            FROM apartment_staging
        """)
        total, with_phone, with_web = cur.fetchone()

    print(f"\nRe-enrich complete.")
    print(f"Total: {total} | With phone: {with_phone} | With website: {with_web}")
    conn.close()


# ── Main ──────────────────────────────────────────────────────────

async def run(areas: list[str], enrich_top: int, headless: bool, run_id: int | None = None):
    grand_total = 0

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox",
                  "--disable-blink-features=AutomationControlled"]
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

            buildings = await discover_area(page, area)
            logger.info(f"  Discovered {len(buildings)} unique buildings")

            if not buildings:
                continue

            buildings.sort(key=lambda b: b.get("lead_score", 0), reverse=True)
            to_enrich = [
                b
                for b in buildings
                if b.get("lead_score", 0) >= ENRICHMENT_SCORE_THRESHOLD
                and b.get("maps_url")
            ]

            logger.info(f"  Enriching {len(to_enrich)} sales-worthy buildings"
                        f"(score >={ENRICHMENT_SCORE_THRESHOLD} )..."
                        )
            for b in to_enrich:
                logger.info(
                    f"  [{b['lead_score']:>3}] {b['building_name'][:45]}"
                )
                await enrich_building(page, b)
                final_score, final_reasons = score_building(
                    b.get("building_name"),
                    b.get("category"),
                    b.get("review_count"),
                    b.get("rating"),
                    b.get("search_area"),
                    b.get("management_company"),
                )

                b["lead_score"] = final_score
                b["score_reasons"] = final_reasons

                logger.info(
                    f"    Final sales score: {final_score}"
                )

                await asyncio.sleep(2)

            saved = save_buildings(buildings, run_id=run_id)
            grand_total += saved
            logger.info(
                "  Saved %s buildings for %s",
                saved,
                location["name"],
            )

        await browser.close()

    with _connect(DB_CONFIG) as c:
        with c.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM apartment_staging")
            total = cur.fetchone()[0]

            cur.execute("""
                SELECT search_area, COUNT(*),
                       SUM(CASE WHEN contact_phone IS NOT NULL THEN 1 ELSE 0 END),
                       MAX(lead_score)
                FROM apartment_staging
                GROUP BY search_area ORDER BY COUNT(*) DESC LIMIT 12
            """)
            by_area = cur.fetchall()

            cur.execute("""
                SELECT building_name, search_area, lead_score,
                       contact_phone, contact_website,
                       management_company, confidence
                FROM apartment_staging
                WHERE lead_score >= 50
                ORDER BY lead_score DESC LIMIT 15
            """)
            top = cur.fetchall()

    print(f"\n{'='*65}")
    print(f"APARTMENT SCRAPE + ENRICH COMPLETE")
    print(f"Total in apartment_staging: {total}")
    print(f"{'='*65}")
    print(f"\n{'Area':<20} {'Buildings':>10} {'With Phone':>11} {'Max Score':>10}")
    print(f"{'─'*55}")
    for area, cnt, phones, mx in by_area:
        print(f"{area:<20} {cnt:>10} {phones:>11} {mx:>10}")

    if top:
        print(f"\nTop leads (score ≥ 50):")
        print(f"{'Building':<32} {'Area':<14} {'Score':>5} {'Phone':<15} {'Conf'}")
        print(f"{'─'*75}")
        for name, area, score, phone, web, mgmt, conf in top:
            ns = (name[:29]+"..") if name and len(name)>29 else (name or "—")
            print(f"{ns:<32} {(area or '—'):<14} {score:>5} "
                  f"{(phone or '—'):<15} {conf or '—'}")
    print(f"\n{'='*65}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--area-id", type=str)
    parser.add_argument("--areas", type=str)
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--enrich-top", type=int, default=15)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--reenrich-missing", action="store_true",
                        help="Re-enrich existing DB records missing phone/website")
    parser.add_argument(
    "--rescore-existing",
    action="store_true",
    help="Recalculate sales opportunity scores for existing apartment staging records",
    )
    parser.add_argument("--limit", type=int, default=50,
                        help="Max records to re-enrich (default 50)")
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

    if args.rescore_existing:
        rescore_existing()

    elif args.reenrich_missing:
        asyncio.run(
            reenrich_missing(
                areas,
                args.limit,
                not args.visible,
            )
        )

    else:
        asyncio.run(
            run(
                areas,
                args.enrich_top,
                not args.visible,
                args.run_id,
            )
        )


if __name__ == "__main__":
    main()
