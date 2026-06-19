import logging
from datetime import datetime, timezone, date

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from scrapy.exceptions import DropItem

logger = logging.getLogger(__name__)


class PostgresPipeline:
    def __init__(self, database_url):
        self.database_url = database_url
        self.engine = None
        self.Session = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(database_url=crawler.settings.get("DATABASE_URL"))

    def open_spider(self, spider):
        self.engine = create_engine(self.database_url, pool_pre_ping=True)
        self.Session = sessionmaker(bind=self.engine)
        logger.info("PostgreSQL pipeline connected — writing to listing_staging")

    def close_spider(self, spider):
        if self.engine:
            self.engine.dispose()

    def process_item(self, item, spider):
        if not item.get("source_url"):
            raise DropItem("Missing source_url")

        # Calculate listing age in days
        listing_date = item.get("listing_date")
        listing_age_days = None
        if listing_date:
            if isinstance(listing_date, str):
                try:
                    listing_date = date.fromisoformat(listing_date)
                except ValueError:
                    listing_date = None
            if listing_date:
                listing_age_days = (date.today() - listing_date).days

        session = self.Session()
        try:
            session.execute(text("""
                INSERT INTO listing_staging (
                    property_name, property_type, bedrooms,
                    price_kes, raw_price,
                    area, raw_address,
                    owner_name, owner_type, owner_phone,
                    owner_email, owner_website,
                    listing_date, listing_age_days,
                    source, source_url, source_id,
                    scraped_at
                ) VALUES (
                    :property_name, :property_type, :bedrooms,
                    :price_kes, :raw_price,
                    :area, :raw_address,
                    :owner_name, :owner_type, :owner_phone,
                    :owner_email, :owner_website,
                    :listing_date, :listing_age_days,
                    :source, :source_url, :source_id,
                    :scraped_at
                )
                ON CONFLICT (source_url) DO UPDATE SET
                    price_kes       = EXCLUDED.price_kes,
                    raw_price       = EXCLUDED.raw_price,
                    owner_phone     = COALESCE(EXCLUDED.owner_phone, listing_staging.owner_phone),
                    listing_age_days= EXCLUDED.listing_age_days,
                    scraped_at      = EXCLUDED.scraped_at
            """), {
                "property_name":  item.get("property_name"),
                "property_type":  item.get("property_type"),
                "bedrooms":       item.get("bedrooms"),
                "price_kes":      item.get("price_kes"),
                "raw_price":      item.get("raw_price"),
                "area":           item.get("area"),
                "raw_address":    item.get("raw_address"),
                "owner_name":     item.get("owner_name"),
                "owner_type":     item.get("owner_type"),
                "owner_phone":    item.get("owner_phone"),
                "owner_email":    item.get("owner_email"),
                "owner_website":  item.get("owner_website"),
                "listing_date":   listing_date,
                "listing_age_days": listing_age_days,
                "source":         item.get("source"),
                "source_url":     item.get("source_url"),
                "source_id":      item.get("source_id"),
                "scraped_at":     item.get("scraped_at",
                                           datetime.now(timezone.utc)),
            })
            session.commit()
            logger.info(
                f"✓ {item.get('source')} | "
                f"{item.get('owner_name','Unknown')} | "
                f"{item.get('area','')} | "
                f"{item.get('raw_price','')}"
            )
        except Exception as e:
            session.rollback()
            logger.error(f"DB write failed: {e}")
        finally:
            session.close()
        return item
