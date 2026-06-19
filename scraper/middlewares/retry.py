import logging
from fake_useragent import UserAgent

logger = logging.getLogger(__name__)


class RotatingUserAgentMiddleware:
    """
    Replaces the default Scrapy user agent with a random real browser UA
    on every request. Sites that block bots check this header first.
    """

    def __init__(self):
        self.ua = UserAgent()

    def process_request(self, request, spider):
        agent = self.ua.random
        request.headers["User-Agent"] = agent
        logger.debug(f"User-Agent set: {agent[:60]}...")
