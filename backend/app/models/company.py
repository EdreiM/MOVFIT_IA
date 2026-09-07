import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), default="active")
    plan: Mapped[str] = mapped_column(String(50), default="starter")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    users = relationship("UserCompany", back_populates="company", cascade="all, delete-orphan")
    numbers = relationship("Number", back_populates="company", cascade="all, delete-orphan")
    conversations = relationship("Conversation", back_populates="company", cascade="all, delete-orphan")
    # Uma empresa pode ter várias linhas de AiConfig agora (padrão + uma por
    # integração personalizada) — lista, não mais um-pra-um.
    ai_configs = relationship("AiConfig", back_populates="company", cascade="all, delete-orphan")
    integrations = relationship("Integration", back_populates="company", cascade="all, delete-orphan")
