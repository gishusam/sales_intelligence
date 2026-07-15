from app.database import Base
from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Date, Text
from sqlalchemy.sql import func


class Lead(Base):
    __tablename__ = "leads"

    id               = Column(Integer, primary_key=True)
    name             = Column(String, nullable=False)
    owner_name       = Column(String)
    owner_type       = Column(String)
    phone            = Column(String)
    email            = Column(String)
    website          = Column(String)
    area             = Column(String)
    lead_type        = Column(String)
    source           = Column(String)
    source_url       = Column(String)
    score            = Column(Float, default=0.0)
    status           = Column(String, default="new")
    assigned_to      = Column(String)
    last_contacted   = Column(DateTime(timezone=True))
    contact_attempts = Column(Integer, default=0)
    follow_up_date   = Column(Date)
    email_sent_at    = Column(DateTime(timezone=True))
    lead_quality     = Column(String)
    ai_score         = Column(String)
    ai_score_reason  = Column(Text)
    ai_scored_at     = Column(DateTime(timezone=True))
    contact_person      = Column(String)
    contact_person_role = Column(String)
    promoted_at      = Column(DateTime(timezone=True))
    created_at       = Column(DateTime(timezone=True), server_default=func.now())
    updated_at       = Column(DateTime(timezone=True), onupdate=func.now())
