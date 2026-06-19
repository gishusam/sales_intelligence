# Official Playwright image — browsers + all system deps included
FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

# Python settings
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first — better layer caching
# If requirements.txt doesn't change, Docker skips this layer
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY . .

# Non-root user — never run scrapers as root
RUN useradd -m scraper && \
    chown -R scraper:scraper /app
USER scraper

# Default: keep container alive for docker exec / Airflow to call spiders
# Airflow will override this with: scrapy crawl buyrentkenya
CMD ["tail", "-f", "/dev/null"]
