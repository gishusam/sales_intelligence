# zones model — placeholder, not yet in use
# PostGIS geometry columns removed until Railway PostGIS is confirmed
from app.database import Base
from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func


class Zone(Base):
    __tablename__ = "zones"

    id         = Column(Integer, primary_key=True)
    name       = Column(String, nullable=False, unique=True)
    sub_label  = Column(String)
    tier       = Column(String)
    lead_count = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
