from sqlalchemy import Column, Integer, String, Float, DateTime, func
from geoalchemy2 import Geometry

from app.database import Base


class Zone(Base):
    __tablename__ = "zones"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True)
    sub_label = Column(String)
    boundary = Column(Geometry("POLYGON", srid=4326))  # PostGIS polygon
    density_score = Column(Float, default=0.0)         # units per km²
    opportunity_score = Column(Float, default=0.0)     # 0–100 composite
    tier = Column(String, default="cool")              # hot / warm / cool
    lead_count = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
