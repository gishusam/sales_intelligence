from sqlalchemy import Column, Integer, String, Float, DateTime, func, ForeignKey, Text 
from geoalchemy2 import Geometry


from app.database import Base


class Lead(Base):
    __tablename__ = "leads"

    id            = Column(Integer, primary_key=True)

    # Who they are
    name          = Column(String, nullable=False)
    owner_name    = Column(String)
    phone         = Column(String)
    email         = Column(String)
    website       = Column(String)

    # Where they are
    area          = Column(String)

    # What they are
    lead_type     = Column(String)   # apartment / agency / landlord
    source        = Column(String)   # google_maps / buyrentkenya / jiji

    # Sales workflow
    score         = Column(Float, default=0.0)
    status        = Column(String, default="new")
    notes         = Column(Text)
    assigned_to   = Column(String)
    last_contacted = Column(DateTime(timezone=True))

    # Dedup key
    name_normalized = Column(String, unique=True)

    # Timestamps
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())