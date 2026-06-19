FROM python:3.11-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd -m appuser && \
    chown -R appuser:appuser /app
USER appuser
EXPOSE ${PORT:-8000}
CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 2
