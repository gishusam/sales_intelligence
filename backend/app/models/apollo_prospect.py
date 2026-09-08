from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.sql import func

from app.database import Base


class ApolloProspect(Base):
    __tablename__ = "apollo_prospects"

    id = Column(Integer, primary_key=True)

    apollo_organization_id = Column(String)
    name = Column(String, nullable=False)
    normalized_name = Column(String)
    domain = Column(String)
    website_url = Column(String)
    linkedin_url = Column(String)

    employee_count = Column(Integer)
    city = Column(String)
    country = Column(String)
    industry = Column(String)
    keywords = Column(JSON)

    quality_score = Column(Float, default=0)
    quality_band = Column(String)
    score_breakdown = Column(JSON)
    score_reasons = Column(JSON)

    review_status = Column(
        String,
        nullable=False,
        default="discovered",
    )

    imported_lead_id = Column(
        Integer,
        ForeignKey("leads.id"),
        nullable=True,
    )

    first_seen_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    last_seen_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ApolloProspectContact(Base):
    __tablename__ = "apollo_prospect_contacts"

    id = Column(Integer, primary_key=True)

    prospect_id = Column(
        Integer,
        ForeignKey(
            "apollo_prospects.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    apollo_person_id = Column(String)
    first_name = Column(String)
    last_name = Column(String)
    name = Column(String)
    title = Column(String)
    seniority = Column(String)
    linkedin_url = Column(String)

    email = Column(String)
    phone = Column(String)

    enrichment_status = Column(
        String,
        nullable=False,
        default="not_enriched",
    )

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
