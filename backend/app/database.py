# backend/app/database.py
# Handles both local Docker (separate vars) and Railway (DATABASE_URL string)

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from app.config import settings


def get_database_url() -> str:
    # Railway provides a single DATABASE_URL — use it if present
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        # Railway uses postgres:// but SQLAlchemy needs postgresql://
        return database_url.replace("postgres://", "postgresql://", 1)
    # Local Docker — build from separate variables
    return settings.database_url


engine = create_engine(
    get_database_url(),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI dependency — yields a DB session, always closes it."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()