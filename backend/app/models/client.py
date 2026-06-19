from sqlalchemy import Column, Integer, String, DateTime, func, ForeignKey
from geoalchemy2 import Geometry

from app.database import Base


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, index=True)
    zone_id = Column(Integer, ForeignKey("zones.id"), nullable=True, index=True)
    name = Column(String, nullable=False)
    phone = Column(String)
    unit_count = Column(Integer, default=0)
    location = Column(Geometry("POINT", srid=4326))
    onboarded_at = Column(DateTime(timezone=True), server_default=func.now())
