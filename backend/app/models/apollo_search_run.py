from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Integer,
)
from sqlalchemy.sql import func

from app.database import Base

# Register referenced prospect table in SQLAlchemy metadata.
from app.models.apollo_prospect import ApolloProspect  # noqa: F401


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

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


class ApolloSearchRunProspect(Base):
    __tablename__ = "apollo_search_run_prospects"

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

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
