FROM mcr.microsoft.com/playwright/python:v1.60.0-jammy

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app/scraper
ENV SCRAPER_LOCATIONS_PATH=/app/scraper_locations.json

WORKDIR /app

COPY scraper/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /tmp/requirements.txt

COPY scraper ./scraper
COPY backend/app/scraper_locations.json ./scraper_locations.json
COPY pipeline.py github_agent.py ./

RUN useradd --create-home worker && chown -R worker:worker /app
USER worker

ENTRYPOINT ["python", "github_agent.py"]
