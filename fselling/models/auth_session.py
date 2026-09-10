"""Durable online authentication sessions for Plan 4."""

import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, text

from ..core.database import Base


AUTH_DEVICE_TYPES = frozenset({"DESKTOP", "TABLET", "MOBILE", "KDS", "UNKNOWN"})


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        Index(
            "ux_auth_sessions_active_device",
            "user_id",
            "device_id",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
        ),
        Index("ix_auth_sessions_user_state", "user_id", "revoked_at", "expires_at"),
    )

    session_id = Column(String(128), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    device_id = Column(String(128), nullable=False)
    device_name = Column(String(80), nullable=False)
    device_type = Column(String(16), nullable=False, default="UNKNOWN")
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    last_seen_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    expires_at = Column(DateTime, nullable=False)
    revoked_at = Column(DateTime, nullable=True)
    revoked_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    revoke_reason = Column(String(48), nullable=True)
