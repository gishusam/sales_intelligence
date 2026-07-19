# Nyumba Zetu Sales Intelligence API

A FastAPI backend for a real-estate sales intelligence platform targeting the Kenyan market. It manages leads, notes, scraper runs, and reporting.

## Stack

- **Backend**: FastAPI + SQLAlchemy (Python)
- **Database**: PostgreSQL (Replit built-in)
- **Auth**: JWT (python-jose + passlib)

## Running the app

The workflow `Start application` runs the server:

```
cd backend && uvicorn app.main:app --host 0.0.0.0 --port 5000 --reload
```

API docs are available at `/docs` (Swagger UI).

## Project structure

```
backend/
  app/
    main.py         # FastAPI app, router registration, CORS
    config.py       # Pydantic settings (reads env vars)
    database.py     # SQLAlchemy engine — uses DATABASE_URL when present
    auth.py         # JWT auth helpers
    models/         # SQLAlchemy ORM models
    routers/        # Route handlers (leads, notes, auth, scraper, reports)
    services/       # Business logic
migrations/
  init.sql          # Full schema — run once on a new database
scraper/            # Scrapy-based lead scrapers (run separately)
```

## Environment variables / secrets

| Key | Notes |
|-----|-------|
| `DATABASE_URL` | Auto-provided by Replit |
| `SECRET_KEY` | Replit Secret — used for sessions |
| `JWT_SECRET_KEY` | Replit Secret — used for JWT signing |
| `DEBUG` | Set to `true` in development |
| `ALLOWED_ORIGINS` | Comma-separated CORS origins |
| `GOOGLE_MAPS_API_KEY` | Optional — needed for Google Maps scraper |

## Notes

- The scraper component (`scraper/`) is Docker-based in the original project. On Replit it would need to be run as a standalone Python process or triggered via the `/api/scraper/run` endpoint pointing to a Cloud Run job.
- The `init.sql` schema was run against Replit's PostgreSQL on setup (Supabase-specific RLS/role commands were omitted as they don't apply here).

## User preferences

_None recorded yet._
