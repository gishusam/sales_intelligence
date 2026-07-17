"""
developers.py — KPDA Developer Extraction + Google Maps Enrichment
Phase 1: Extract developer names + tiers from the KPDA member PDF
Phase 2: Enrich each developer with contact info via Google Maps

Usage:
  # Extract only (run once, or when KPDA updates their PDF)
  python scraper/spiders/developers.py --extract

  # Enrich only (run anytime to fill in missing contacts)
  python scraper/spiders/developers.py --enrich

  # Both
  python scraper/spiders/developers.py --extract --enrich

  # Enrich with visible browser, limit to 10
  python scraper/spiders/developers.py --enrich --limit 10 --visible
"""

import asyncio
import logging
import os
import re
import argparse
import unicodedata
from datetime import datetime, timezone

import psycopg2
from playwright.async_api import async_playwright
from spiders.google_consent import dismiss_google_consent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [developers] %(levelname)s: %(message)s"
)
logger = logging.getLogger(__name__)

DB_CONFIG = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    dbname=os.getenv("POSTGRES_DB", "nzetu_db"),
    user=os.getenv("POSTGRES_USER", "nzetu"),
    password=os.getenv("POSTGRES_PASSWORD", "changeme"),
)

KPDA_PDF_URL = "https://www.kpda.or.ke/documents/MEMBERS%20IN%20GOOD%20STANDING%202024.pdf"

TIER_SCORES = {
    "PLATINUM": 90, "GOLD": 75, "ASSOCIATE GOLD": 70,
    "CORPORATE": 60, "ASSOCIATE": 50,
    "ASSOCIATE SILVER": 45, "ASSOCIATE BRONZE": 35,
}

# Known section headers that end the developers list
STOP_MARKERS = {
    "REAL ESTATE AGENTS", "REAL ESTATE ADVISORY FIRM",
    "ARCHITECTURAL FIRMS", "LAW FIRMS"
}

TIER_PATTERN = re.compile(
    r'^(.*?)\s*(PLATINUM|ASSOCIATE GOLD|GOLD|ASSOCIATE SILVER|'
    r'ASSOCIATE BRONZE|ASSOCIATE|CORPORATE)\s*$'
)


# ════════════════════════════════════════════════════════════════
# PHASE 1 — EXTRACT from KPDA PDF
# ════════════════════════════════════════════════════════════════

def ensure_table(conn):
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS developer_staging (
                id              SERIAL PRIMARY KEY,
                developer_name  TEXT NOT NULL,
                membership_tier TEXT,
                tier_score      INTEGER,

                -- Enrichment
                contact_phone   TEXT,
                contact_email   TEXT,
                contact_website TEXT,
                address         TEXT,
                rating          FLOAT,
                review_count    INTEGER,
                maps_url        TEXT,
                enrichment_status TEXT DEFAULT 'pending',
                enriched_at     TIMESTAMPTZ,

                source          TEXT DEFAULT 'kpda_directory',
                scraped_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        # Add columns if table existed without them
        for col, typ in [
            ("contact_phone", "TEXT"), ("contact_email", "TEXT"),
            ("contact_website", "TEXT"), ("address", "TEXT"),
            ("rating", "FLOAT"), ("review_count", "INTEGER"),
            ("maps_url", "TEXT"),
            ("enrichment_status", "TEXT DEFAULT 'pending'"),
            ("enriched_at", "TIMESTAMPTZ"),
        ]:
            cur.execute(f"ALTER TABLE developer_staging ADD COLUMN IF NOT EXISTS {col} {typ}")

        # Ensure unique constraint exists
        cur.execute("""
            UPDATE developer_staging SET developer_name = 'unknown_' || id::text
              WHERE developer_name IS NULL
        """)
        cur.execute("ALTER TABLE developer_staging ALTER COLUMN developer_name SET NOT NULL")
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint WHERE conname = 'dev_staging_name_uniq'
                ) THEN
                    ALTER TABLE developer_staging
                        ADD CONSTRAINT dev_staging_name_uniq UNIQUE (developer_name);
                END IF;
            END $$;
        """)
        conn.commit()


def extract_from_pdf():
    """Download KPDA PDF and extract developer names + tiers."""
    import pdfplumber
    import requests

    logger.info("Downloading KPDA member directory...")
    resp = requests.get(KPDA_PDF_URL, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }, timeout=30)
    pdf_path = "/tmp/kpda_members.pdf"
    with open(pdf_path, "wb") as f:
        f.write(resp.content)

    companies = []
    in_developers_section = False

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if not text:
                continue
            for line in text.split("\n"):
                line = line.strip()
                if not line:
                    continue

                if "PROPERTY DEVELOPERS CATEGORY" in line:
                    in_developers_section = True
                    continue

                if any(m in line.upper() for m in STOP_MARKERS):
                    in_developers_section = False
                    continue

                if not in_developers_section:
                    continue

                m = TIER_PATTERN.match(line)
                if m:
                    name = m.group(1).strip().rstrip("/").strip()
                    tier = m.group(2).strip()
                else:
                    # Mangled PDF extraction — fallback
                    name = re.sub(r"(Limi.*|CORPORATE)$", "Limited", line).strip()
                    tier = "CORPORATE"

                name = re.sub(r"\s+", " ", name).strip()
                if name and len(name) > 2:
                    companies.append((name, tier))

    logger.info(f"Extracted {len(companies)} developers from PDF")

    conn = psycopg2.connect(**DB_CONFIG)
    ensure_table(conn)

    with conn.cursor() as cur:
        for name, tier in companies:
            cur.execute("""
                INSERT INTO developer_staging (developer_name, membership_tier, tier_score)
                VALUES (%s, %s, %s)
                ON CONFLICT (developer_name) DO UPDATE SET
                    membership_tier = EXCLUDED.membership_tier,
                    tier_score      = EXCLUDED.tier_score
            """, (name, tier, TIER_SCORES.get(tier, 50)))
        conn.commit()

        cur.execute("SELECT COUNT(*) FROM developer_staging")
        total = cur.fetchone()[0]

        cur.execute("""
            SELECT membership_tier, COUNT(*), MAX(tier_score)
            FROM developer_staging GROUP BY membership_tier
            ORDER BY MAX(tier_score) DESC
        """)
        breakdown = cur.fetchall()

    conn.close()

    print(f"\n{'='*50}")
    print(f"EXTRACTION COMPLETE — {total} developers in database")
    print(f"{'='*50}")
    for tier, count, score in breakdown:
        print(f"  {(tier or 'unknown'):<16} {count:>3}  (score {score})")
    print()


# ════════════════════════════════════════════════════════════════
# PHASE 2 — ENRICH via Google Maps
# ════════════════════════════════════════════════════════════════

def normalize(name: str) -> str:
    name = name.lower().strip()
    name = unicodedata.normalize("NFD", name)
    name = "".join(c for c in name if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", name)).strip()


def get_pending(conn, limit: int):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, developer_name, membership_tier, tier_score
            FROM developer_staging
            WHERE enrichment_status = 'pending'
            ORDER BY tier_score DESC
            LIMIT %s
        """, (limit,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def save_enrichment(conn, dev_id: int, data: dict):
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE developer_staging SET
                contact_phone     = %s,
                contact_email     = %s,
                contact_website   = %s,
                address           = %s,
                rating            = %s,
                review_count      = %s,
                maps_url          = %s,
                enrichment_status = %s,
                enriched_at       = %s
            WHERE id = %s
        """, (
            data.get("phone"), data.get("email"), data.get("website"),
            data.get("address"), data.get("rating"), data.get("review_count"),
            data.get("maps_url"), data.get("status", "done"),
            datetime.now(timezone.utc), dev_id
        ))
        conn.commit()


async def enrich_developer(page, name: str) -> dict:
    """Search Google Maps for a developer name and extract contact info."""
    query = f"{name} Nairobi Kenya"
    url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}?hl=en"
    result = {"status": "failed"}

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await dismiss_google_consent(page)
        await page.wait_for_timeout(2500)

        # Check if it went straight to a single place page (h1 visible)
        # or a results list (div.Nv2PK)
        card = await page.query_selector("div.Nv2PK")
        if card:
            # Click the first result to open its detail panel
            await card.click()
            await page.wait_for_timeout(2000)

        try:
            await page.wait_for_selector("h1", timeout=8000)
        except Exception:
            result["status"] = "no_results"
            return result

        full_text = await page.inner_text("body")
        lines = [l.strip() for l in full_text.splitlines() if l.strip()]

        # Phone
        phone = None
        btn = await page.query_selector(
            'button[data-tooltip*="phone"], button[aria-label*="phone"], a[href^="tel:"]'
        )
        if btn:
            phone = re.sub(r'[^\d+]', '', await btn.inner_text()) or None
        if not phone:
            pm = re.search(
                r'(\+?254[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}|'
                r'0[17]\d{2}[\s\-]?\d{3}[\s\-]?\d{3})',
                full_text
            )
            if pm:
                phone = re.sub(r'[\s\-]', '', pm.group())

        # Website
        website = None
        web_btn = await page.query_selector(
            'a[data-tooltip*="website"], a[aria-label*="website"]'
        )
        if web_btn:
            href = await web_btn.get_attribute("href")
            if href and "google.com" not in href and not href.startswith("/aclk"):
                website = href

        # Email
        email = None
        em = re.search(r'[\w\.\-]+@[\w\.\-]+\.[a-zA-Z]{2,}', full_text)
        if em:
            email = em.group().lower()

        # Rating + reviews
        rating, review_count = None, None
        for line in lines[:6]:
            m = re.match(r'^(\d\.\d)$', line)
            if m:
                rating = float(m.group(1))
            m2 = re.search(r'\((\d[\d,]*)\)', line)
            if m2:
                review_count = int(m2.group(1).replace(",", ""))

        # Address
        address = None
        for line in lines[1:8]:
            if any(kw in line.lower() for kw in
                   ["road", "street", "avenue", "nairobi", "kenya", "way", "drive"]):
                address = line.replace("·", "").strip()
                break

        maps_url = page.url

        result.update({
            "phone": phone, "email": email, "website": website,
            "address": address, "rating": rating,
            "review_count": review_count, "maps_url": maps_url,
            "status": "done",
        })

        logger.info(
            f"  ✓ {name[:40]:<40} phone:{phone or '—':<15} "
            f"web:{'yes' if website else '—':<4} rating:{rating or '—'}"
        )

    except Exception as e:
        result["status"] = "failed"
        logger.warning(f"  ✗ {name[:40]} | {e}")

    return result


async def run_enrichment(limit: int, headless: bool):
    conn = psycopg2.connect(**DB_CONFIG)
    ensure_table(conn)
    pending = get_pending(conn, limit)

    if not pending:
        logger.info("No pending developers to enrich")
        conn.close()
        return

    logger.info(f"Enriching {len(pending)} developers...")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        for i, dev in enumerate(pending, 1):
            logger.info(f"[{i}/{len(pending)}] [{dev['membership_tier']}] {dev['developer_name']}")
            result = await enrich_developer(page, dev["developer_name"])
            save_enrichment(conn, dev["id"], result)
            await asyncio.sleep(2)

        await browser.close()

    # Summary
    with conn.cursor() as cur:
        cur.execute("""
            SELECT enrichment_status, COUNT(*),
                   COUNT(contact_phone), COUNT(contact_website)
            FROM developer_staging GROUP BY enrichment_status
        """)
        stats = cur.fetchall()

        cur.execute("""
            SELECT developer_name, membership_tier, contact_phone,
                   contact_website, rating
            FROM developer_staging
            WHERE enrichment_status = 'done'
            ORDER BY tier_score DESC, rating DESC NULLS LAST
            LIMIT 15
        """)
        top = cur.fetchall()

    conn.close()

    print(f"\n{'='*65}")
    print(f"ENRICHMENT COMPLETE")
    print(f"{'='*65}")
    print(f"{'Status':<12} {'Total':>6} {'With Phone':>11} {'With Website':>13}")
    for status, total, phones, sites in stats:
        print(f"{status:<12} {total:>6} {phones:>11} {sites:>13}")

    if top:
        print(f"\nTop enriched developers:")
        print(f"{'Developer':<35} {'Tier':<10} {'Phone':<15} {'Website':<6} {'Rating'}")
        print(f"{'─'*75}")
        for name, tier, phone, web, rating in top:
            ns = (name[:32]+"..") if len(name) > 32 else name
            print(f"{ns:<35} {tier:<10} {(phone or '—'):<15} "
                  f"{'yes' if web else '—':<6} {rating or '—'}")
    print(f"\n{'='*65}\n")


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="KPDA developer extraction + enrichment")
    parser.add_argument("--extract", action="store_true", help="Extract from KPDA PDF")
    parser.add_argument("--enrich", action="store_true", help="Enrich via Google Maps")
    parser.add_argument("--limit", type=int, default=20, help="Max developers to enrich")
    parser.add_argument("--visible", action="store_true", help="Show browser")
    args = parser.parse_args()

    if not args.extract and not args.enrich:
        parser.print_help()
        return

    if args.extract:
        extract_from_pdf()

    if args.enrich:
        asyncio.run(run_enrichment(args.limit, headless=not args.visible))


if __name__ == "__main__":
    main()
