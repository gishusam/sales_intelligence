# app/models/scraper_run.py

from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from app.database import Base


class ScraperRun(Base):
    __tablename__ = "scraper_runs"

    id = Column(Integer, primary_key=True, index=True)

    source = Column(String, nullable=False)       # apartments/agencies/developers
    area = Column(String, nullable=False)

    status = Column(String, default="running")

    records_found = Column(Integer, default=0)
    with_contacts = Column(Integer, default=0)

    imported = Column(Integer, default=0)
    updated = Column(Integer, default=0)
    duplicates = Column(Integer, default=0)
    rejected = Column(Integer, default=0)

    started_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    duration_seconds = Column(Integer, nullable=True)