from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import DateTime, ForeignKey, JSON, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from switchsign.config import settings


class ContractStatus(str, Enum):
    pending = "pending"
    signed = "signed"
    expired = "expired"
    cancelled = "cancelled"


class Base(DeclarativeBase):
    pass


class Contract(Base):
    __tablename__ = "contracts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_name: Mapped[str] = mapped_column(String(255), nullable=False)
    client_email: Mapped[str] = mapped_column(String(320), nullable=False)
    contract_title: Mapped[str] = mapped_column(String(255), nullable=False)
    markdown_body: Mapped[str] = mapped_column(Text, nullable=False)
    signing_token: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    status: Mapped[ContractStatus] = mapped_column(String(20), default=ContractStatus.pending.value, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    signed_pdf_drive_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    signed_pdf_local_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_signature_png_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    client_signed_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    client_phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    client_signed_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    client_ip: Mapped[str | None] = mapped_column(String(100), nullable=True)
    client_user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)

    audit_entries: Mapped[list["AuditLog"]] = relationship(back_populates="contract", cascade="all, delete-orphan")


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    contract_id: Mapped[str] = mapped_column(String(36), ForeignKey("contracts.id"), nullable=False, index=True)
    event: Mapped[str] = mapped_column(String(100), nullable=False)
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSON, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    contract: Mapped[Contract] = relationship(back_populates="audit_entries")


def _sqlite_url(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


settings.switchsign_db_path.parent.mkdir(parents=True, exist_ok=True)
engine = create_engine(_sqlite_url(settings.switchsign_db_path), connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db() -> None:
    settings.switchsign_db_path.parent.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)


def get_session() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
