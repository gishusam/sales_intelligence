import hashlib
import logging

import redis
from scrapy.exceptions import DropItem

logger = logging.getLogger(__name__)

DEDUP_KEY_PREFIX = "nzetu:seen:"
DEDUP_TTL_SECONDS = 60 * 60 * 24 * 30  # 30 days


class RedisDedupPipeline:
    """
    Drops any lead whose source_url we've already scraped.
    Uses Redis SET with a 30-day TTL.

    Why Redis and not just checking the DB?
    Redis is ~0.1ms per check vs ~5ms for a DB query.
    At scale (thousands of listings) this matters.
    """

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self.client = None

    @classmethod
    def from_crawler(cls, crawler):
        return cls(redis_url=crawler.settings.get("REDIS_URL"))

    def open_spider(self, spider):
        self.client = redis.from_url(self.redis_url, decode_responses=True)
        logger.info("Redis dedup pipeline connected")

    def close_spider(self, spider):
        if self.client:
            self.client.close()

    def process_item(self, item, spider):
        url = item.get("source_url", "")
        if not url:
            raise DropItem("Missing source_url — cannot dedup")

        # Hash the URL so Redis keys stay short and consistent
        fingerprint = hashlib.sha256(url.encode()).hexdigest()
        redis_key = f"{DEDUP_KEY_PREFIX}{fingerprint}"

        if self.client.exists(redis_key):
            raise DropItem(f"Duplicate listing dropped: {url}")

        # Mark as seen with 30-day expiry
        self.client.setex(redis_key, DEDUP_TTL_SECONDS, "1")
        return item
