import re
import json
import logging
from datetime import datetime, timezone, date

import scrapy
from items import ListingItem

logger = logging.getLogger(__name__)


class BuyRentKenyaSpider(scrapy.Spider):
    name = "buyrentkenya"
    allowed_domains = ["buyrentkenya.com"]

    start_urls = [
        "https://www.buyrentkenya.com/property-for-rent",
        "https://www.buyrentkenya.com/houses-for-rent",
        "https://www.buyrentkenya.com/townhouses-for-rent",
        "https://www.buyrentkenya.com/villas-for-rent",
    ]

    custom_settings = {
        "DOWNLOAD_DELAY": 2,
        "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
        "ROBOTSTXT_OBEY": True,
    }

    def parse(self, response):
        listing_links = response.css("a[href*='/listings/']::attr(href)").getall()
        seen = set()
        for href in listing_links:
            if href not in seen:
                seen.add(href)
                yield response.follow(href, callback=self.parse_listing)
        logger.info(f"Found {len(seen)} listing links on {response.url}")

    def parse_listing(self, response):
        raw_blocks = response.css(
            "script[type='application/ld+json']::text"
        ).getall()

        for block in raw_blocks:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue

            graph = data.get("@graph", [])
            if not graph:
                continue

            node_map = {
                node["@id"]: node
                for node in graph
                if isinstance(node, dict) and "@id" in node
            }

            product = next(
                (n for n in graph if n.get("@type") == "Product"), None
            )
            if not product:
                continue

            offer_ref  = product.get("offers", {})
            offer_id   = offer_ref.get("@id") if isinstance(offer_ref, dict) else None
            offer      = node_map.get(offer_id, {})

            agent_ref  = offer.get("offeredBy", {})
            agent_id   = agent_ref.get("@id") if isinstance(agent_ref, dict) else None
            agent      = node_map.get(agent_id, {})

            agent_addr_ref = agent.get("address", {})
            agent_addr_id  = agent_addr_ref.get("@id") if isinstance(agent_addr_ref, dict) else None
            agent_addr     = node_map.get(agent_addr_id, {})

            accommodation = next(
                (n for n in graph if n.get("@type") == "Accommodation"), None
            ) or {}

            listing_addr = next(
                (n for n in graph
                 if n.get("@type") == "PostalAddress"
                 and "listing-" in n.get("@id", "")),
                {}
            )

            # Price
            price_spec = offer.get("priceSpecification", {})
            price_val  = price_spec.get("price")
            currency   = price_spec.get("priceCurrency", "KES")
            raw_price  = f"{currency} {price_val:,.0f}/mo" if price_val else None
            price_kes  = int(price_val) if price_val else None

            # Listing date — from datePosted or datePublished in JSON-LD
            listing_date = None
            listing_node = next(
                (n for n in graph if n.get("@type") == "RealEstateListing"), None
            ) or {}
            for date_field in ["datePosted", "datePublished", "dateModified"]:
                date_str = listing_node.get(date_field)
                if date_str and isinstance(date_str, str):
                    try:
                        listing_date = date.fromisoformat(date_str[:10])
                        break
                    except ValueError:
                        continue

            # Source ID
            id_match  = re.search(r"-(\d+)$", response.url)
            source_id = id_match.group(1) if id_match else None

            # Owner type from @type field
            agent_type = agent.get("@type", "")
            if agent_type == "RealEstateAgent":
                owner_type = "agency"
            elif agent_type == "Person":
                owner_type = "owner"
            else:
                owner_type = self._classify_owner(
                    agent.get("name", ""),
                    agent.get("description", "")
                )

            # Location
            locality = listing_addr.get("addressLocality", "")
            region   = listing_addr.get("addressRegion", "")
            area     = locality or region or None

            description = product.get("description", "")

            item = ListingItem()
            item["property_name"]  = product.get("name", "").split("|")[0].strip()
            item["property_type"]  = self._property_type(product.get("name", ""))
            item["bedrooms"]       = self._safe_int(accommodation.get("numberOfBedrooms"))
            item["price_kes"]      = price_kes
            item["raw_price"]      = raw_price
            item["area"]           = area
            item["raw_address"]    = (
                f"{agent_addr.get('streetAddress','').strip()}, "
                f"{agent_addr.get('addressLocality','')}".strip(", ") or None
            )
            item["owner_name"]     = agent.get("name") or None
            item["owner_type"]     = owner_type
            item["owner_phone"]    = self._phone(description)
            item["owner_email"]    = self._email(description)
            item["owner_website"]  = self._website(description)
            item["listing_date"]   = listing_date
            item["source"]         = "buyrentkenya"
            item["source_url"]     = response.url
            item["source_id"]      = source_id
            item["scraped_at"]     = datetime.now(timezone.utc)

            if item["property_name"] or item["area"]:
                yield item

    def _safe_int(self, v):
        try:
            return int(v) if v is not None else None
        except (ValueError, TypeError):
            return None

    def _phone(self, text):
        for p in [r'\+254[\s\-]?\d{3}[\s\-]?\d{3}[\s\-]?\d{3}',
                  r'07\d{2}[\s\-]?\d{3}[\s\-]?\d{3}',
                  r'01\d{2}[\s\-]?\d{3}[\s\-]?\d{3}']:
            m = re.search(p, text)
            if m:
                return re.sub(r'[\s\-]', '', m.group())
        return None

    def _email(self, text):
        m = re.search(r'[\w\.\-]+@[\w\.\-]+\.\w+', text)
        return m.group().lower() if m else None

    def _website(self, text):
        m = re.search(
            r'https?://(?!i\.roamcdn|assets\.buyrent|www\.buyrent)[^\s\)]+',
            text
        )
        return m.group().rstrip('.,') if m else None

    def _property_type(self, name):
        n = name.lower()
        if "townhouse"  in n: return "townhouse"
        if "apartment"  in n: return "apartment"
        if "house"      in n: return "house"
        if "villa"      in n: return "villa"
        if "bedsitter"  in n: return "bedsitter"
        if "studio"     in n: return "studio"
        if "commercial" in n: return "commercial"
        return "apartment"

    def _classify_owner(self, name, desc):
        combined = (name + " " + desc).lower()
        for kw in ["property management", "property manager", "managing agent"]:
            if kw in combined:
                return "pm"
        for kw in ["ltd", "limited", "agency", "realtors", "real estate", "estates"]:
            if re.search(rf'\b{re.escape(kw)}\b', combined):
                return "agency"
        return "owner"
