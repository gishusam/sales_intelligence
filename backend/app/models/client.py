from app.database import Base
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func


class Client(Base):
    __tablename__ = "clients"

    id          = Column(Integer, primary_key=True)
    zone_id     = Column(Integer, ForeignKey("zones.id"), nullable=True)
    name        = Column(String, nullable=False)
    phone       = Column(String)
    unit_count  = Column(Integer)
    onboarded_at = Column(DateTime(timezone=True), server_default=func.now())
