from sqlalchemy import Column, Integer, String, Boolean, DateTime, func
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id         = Column(Integer, primary_key=True)
    name       = Column(String, nullable=False)
    email      = Column(String, nullable=False, unique=True)
    password   = Column(String, nullable=False)   # bcrypt hash
    role       = Column(String, default="sales")  # sales / admin
    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())