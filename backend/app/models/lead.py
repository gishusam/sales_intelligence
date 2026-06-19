from app.database import Base
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func


class Lead(Base):
    __tablename__ = "leads"

    id              = Column(Integer, primary_key=True)
    zone_id         = Column(Integer, ForeignKey("zones.id"), nullable=True)
    name            = Column(String, nullable=False)
    owner_name      = Column(String)
    owner_type      = Column(String)
    phone           = Column(String)
    email           = Column(String)
    website         = Column(String)
    area            = Column(String)
    lead_type       = Column(String)
    source          = Column(String)
    source_url      = Column(String)
    score           = Column(Float, default=0.0)
    status          = Column(String, default="new")
    notes           = Column(String)
    assigned_to     = Column(String)
    last_contacted  = Column(DateTime(timezone=True))
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), onupdate=func.now())
