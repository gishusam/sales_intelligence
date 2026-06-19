import os
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "../.env"))

BOT_NAME = "nzetu_scraper"
SPIDER_MODULES = ["spiders"]
NEWSPIDER_MODULE = "spiders"

# ── Request fingerprinting (suppress deprecation warning) ────────
REQUEST_FINGERPRINTER_IMPLEMENTATION = "2.7"

# ── Politeness ───────────────────────────────────────────────────
ROBOTSTXT_OBEY = True
DOWNLOAD_DELAY = 2
RANDOMIZE_DOWNLOAD_DELAY = True
CONCURRENT_REQUESTS = 4
CONCURRENT_REQUESTS_PER_DOMAIN = 2

# ── Retry ────────────────────────────────────────────────────────
RETRY_ENABLED = True
RETRY_TIMES = 3
RETRY_HTTP_CODES = [429, 500, 502, 503, 504]

# ── AutoThrottle ─────────────────────────────────────────────────
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 1
AUTOTHROTTLE_MAX_DELAY = 10
AUTOTHROTTLE_TARGET_CONCURRENCY = 2.0

# ── Playwright — opt-in per request, NOT global handler ──────────
# BuyRentKenya uses standard HTTP — no Playwright needed
# PigiaMe sets meta={"playwright": True} per request
DOWNLOAD_HANDLERS = {
    "http": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
    "https": "scrapy.core.downloader.handlers.http.HTTPDownloadHandler",
}

# Only load Playwright reactor when a spider actually needs it
TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"

# ── Middlewares ───────────────────────────────────────────────────
DOWNLOADER_MIDDLEWARES = {
    "scrapy.downloadermiddlewares.useragent.UserAgentMiddleware": None,
    "middlewares.retry.RotatingUserAgentMiddleware": 400,
}

# ── Pipelines ────────────────────────────────────────────────────
ITEM_PIPELINES = {
    "pipelines.dedup.RedisDedupPipeline": 100,
    "pipelines.db_pipeline.PostgresPipeline": 200,
}

# ── Database ──────────────────────────────────────────────────────
DATABASE_URL = (
    f"postgresql://{os.getenv('POSTGRES_USER')}:{os.getenv('POSTGRES_PASSWORD')}"
    f"@{os.getenv('POSTGRES_HOST', 'localhost')}:{os.getenv('POSTGRES_PORT', 5432)}"
    f"/{os.getenv('POSTGRES_DB')}"
)

# ── Redis ─────────────────────────────────────────────────────────
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# ── Logging ───────────────────────────────────────────────────────
LOG_LEVEL = "INFO"
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"
