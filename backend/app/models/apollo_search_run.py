from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.database import Base

# Register referenced prospect table in SQLAlchemy metadata.
from app.models.apollo_prospect import (  # noqa: F401
    ApolloProspect,
    ApolloProspectContact,
)


class ApolloSearchRun(Base):
    __tablename__ = "apollo_search_runs"

    id = Column(Integer, primary_key=True)

    filters = Column(JSON)

    found_count = Column(Integer, default=0)
    qualified_count = Column(Integer, default=0)
    approved_count = Column(Integer, default=0)
    rejected_count = Column(Integer, default=0)
    imported_count = Column(Integer, default=0)

    credits_used = Column(Integer, default=0)
    status = Column(String, nullable=False, default="queued")
    processed_count = Column(Integer, default=0)
    no_contact_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    queued_count = Column(Integer, default=0)
    credit_status = Column(JSON)
    billing_cycle_reset_at = Column(String)
    assigned_to = Column(String)
    enrichment_started_at = Column(DateTime(timezone=True))
    enrichment_completed_at = Column(DateTime(timezone=True))

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class ApolloSearchRunProspect(Base):
    __tablename__ = "apollo_search_run_prospects"
    __table_args__ = (
        UniqueConstraint(
            "search_run_id",
            "prospect_id",
            name="uq_apollo_search_run_prospect",
        ),
    )

    id = Column(Integer, primary_key=True)

    search_run_id = Column(
        Integer,
        ForeignKey(
            "apollo_search_runs.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    prospect_id = Column(
        Integer,
        ForeignKey(
            "apollo_prospects.id",
            ondelete="CASCADE",
        ),
        nullable=False,
    )

    contact_id = Column(
        Integer,
        ForeignKey(
            "apollo_prospect_contacts.id",
            ondelete="SET NULL",
        ),
    )
    status = Column(String, nullable=False, default="queued")
    attempts = Column(Integer, nullable=False, default=0)
    processed_at = Column(DateTime(timezone=True))
    last_error = Column(String)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
