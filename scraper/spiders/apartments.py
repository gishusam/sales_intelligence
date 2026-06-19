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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [apartments] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

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


def score_building(name, category, review_count, rating, area) -> tuple:
    score = 0
    reasons = []
    nl = (name or "").lower()
    cl = (category or "").lower()
    al = (area or "").lower()

    matched = [kw for kw in BUILDING_KEYWORDS if kw in nl]
    if matched:
        pts = min(len(matched) * 8, 25)
        score += pts
        reasons.append(f"keywords: {', '.join(matched[:3])} (+{pts})")

    rc = review_count or 0
    if rc >= 200:   score += 25; reasons.append(f"reviews {rc} (+25)")
    elif rc >= 100: score += 20; reasons.append(f"reviews {rc} (+20)")
    elif rc >= 50:  score += 15; reasons.append(f"reviews {rc} (+15)")
    elif rc >= 20:  score += 8;  reasons.append(f"reviews {rc} (+8)")
    elif rc >= 5:   score += 3;  reasons.append(f"reviews {rc} (+3)")

    r = rating or 0
    if r >= 4.5:   score += 10; reasons.append(f"rating {r} (+10)")
    elif r >= 4.0: score += 7;  reasons.append(f"rating {r} (+7)")
    elif r >= 3.5: score += 4;  reasons.append(f"rating {r} (+4)")

    if any(kw in cl for kw in MANAGEMENT_CATEGORIES):
        score += 15; reasons.append(f"category: {category} (+15)")

    if al in PREMIUM_AREAS:
        score += 15; reasons.append(f"premium area (+15)")
    elif al in HIGH_DENSITY_AREAS:
        score += 10; reasons.append(f"high-density area (+10)")

    if any(s in nl for s in ["phase", "block", "tower", "wing", "ii", "iii"]):
        score += 10; reasons.append("multi-block indicator (+10)")

    return min(score, 100), " | ".join(reasons)


# ── Discovery ─────────────────────────────────────────────────────

async def discover_area(page, area: str) -> list[dict]:
    """Search Google Maps and extract all building cards for an area."""
    all_buildings = []
    seen = set()

    for keyword in KEYWORDS:
        query = f"{keyword} {area} Nairobi"
        url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}?hl=en"

        logger.info(f"  Searching: {query}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=45000)
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
        website = None
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
        email = None
        em = re.search(r'[\w\.\-]+@[\w\.\-]+\.[a-zA-Z]{2,}', full_text)
        if em:
            email = em.group().lower()

        # ── MANAGEMENT COMPANY ─────────────────────────────────────────────
        management_company = None
        mgmt_kws = ["managed by", "management", "caretaker",
                    "leasing office", "developed by", "marketed by"]
        for i, line in enumerate(lines):
            if any(kw in line.lower() for kw in mgmt_kws):
                for j in range(i + 1, min(i + 4, len(lines))):
                    c = lines[j]
                    if len(c) > 3 and c.lower() != line.lower():
                        management_company = c
                        break
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

        building.update({
            "contact_phone":      phone,
            "contact_email":      email,
            "contact_website":    website,
            "management_company": management_company,
            "social_media":       " | ".join(socials) if socials else None,
            "enrichment_status":  "done",
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

def save_buildings(conn, buildings: list[dict]) -> int:
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
    ) for b in buildings]

    with conn.cursor() as cur:
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
                enriched_at, scraped_at
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
                management_company = COALESCE(EXCLUDED.management_company,
                                              apartment_staging.management_company),
                enrichment_status  = EXCLUDED.enrichment_status,
                confidence         = EXCLUDED.confidence,
                enriched_at        = EXCLUDED.enriched_at
        """, rows)
        conn.commit()
    return len(rows)


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
    conn = psycopg2.connect(**DB_CONFIG)

    with conn.cursor() as cur:
        area_filter = "AND search_area = ANY(%s)" if areas != DEFAULT_AREAS else ""
        params = [limit]
        if areas != DEFAULT_AREAS:
            params = [areas, limit]

        cur.execute(f"""
            SELECT id, building_name, maps_url, search_area
            FROM apartment_staging
            WHERE contact_phone IS NULL
              AND maps_url IS NOT NULL
              AND enrichment_status != 'skipped'
              {area_filter}
            ORDER BY lead_score DESC
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

        for record_id, name, maps_url, area in records:
            logger.info(f"  [{record_id}] {name} ({area})")
            building = {"maps_url": maps_url, "contact_phone": None}
            await enrich_building(page, building)

            if building.get("contact_phone") or building.get("contact_website"):
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE apartment_staging SET
                            contact_phone      = COALESCE(%s, contact_phone),
                            contact_email      = COALESCE(%s, contact_email),
                            contact_website    = COALESCE(%s, contact_website),
                            management_company = COALESCE(%s, management_company),
                            enrichment_status  = 'done',
                            confidence         = %s,
                            enriched_at        = NOW()
                        WHERE id = %s
                    """, (
                        building.get("contact_phone"),
                        building.get("contact_email"),
                        building.get("contact_website"),
                        building.get("management_company"),
                        building.get("confidence", "low"),
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

async def run(areas: list[str], enrich_top: int, headless: bool):
    conn = psycopg2.connect(**DB_CONFIG)
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
            logger.info(f"\n── Area: {area} ──────────────────────────")

            buildings = await discover_area(page, area)
            logger.info(f"  Discovered {len(buildings)} unique buildings")

            if not buildings:
                continue

            buildings.sort(key=lambda b: b.get("lead_score", 0), reverse=True)
            to_enrich = [
                b for b in buildings[:enrich_top]
                if b.get("maps_url")
                and not b.get("contact_phone")
            ]

            logger.info(f"  Enriching top {len(to_enrich)} buildings...")
            for b in to_enrich:
                logger.info(
                    f"  [{b['lead_score']:>3}] {b['building_name'][:45]}"
                )
                await enrich_building(page, b)
                await asyncio.sleep(2)

            saved = save_buildings(conn, buildings)
            grand_total += saved
            logger.info(f"  Saved {saved} buildings for {area}")

        await browser.close()

    with psycopg2.connect(**DB_CONFIG) as c:
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

    conn.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--areas", type=str)
    parser.add_argument("--enrich-top", type=int, default=15)
    parser.add_argument("--visible", action="store_true")
    parser.add_argument("--reenrich-missing", action="store_true",
                        help="Re-enrich existing DB records missing phone/website")
    parser.add_argument("--limit", type=int, default=50,
                        help="Max records to re-enrich (default 50)")
    args = parser.parse_args()

    areas = (
        [a.strip() for a in args.areas.split(",")]
        if args.areas else DEFAULT_AREAS
    )

    if args.reenrich_missing:
        asyncio.run(reenrich_missing(areas, args.limit, not args.visible))
    else:
        asyncio.run(run(areas, args.enrich_top, not args.visible))


if __name__ == "__main__":
    main()