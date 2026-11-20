"""Login audit metadata held separately from active browser sessions."""

from sqlalchemy import CheckConstraint, Index, String
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Row, key_string, table_args


class LoginEvent(Row, Base):
    __tablename__ = "ac_login_events"
    __table_args__ = table_args(
        CheckConstraint(
            "outcome IN ('success','failure','limited')", name="ck_login_event_outcome"
        ),
        Index("ix_login_event_created_at", "created_at"),
        Index("ix_login_event_account", "user_id", "id"),
    )

    user_id: Mapped[int | None] = mapped_column(BIGINT(unsigned=True))
    username: Mapped[str | None] = mapped_column(key_string())
    role: Mapped[str | None] = mapped_column(String(16))
    outcome: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32))
    ip_address: Mapped[str | None] = mapped_column(key_string(45))
    location: Mapped[str | None] = mapped_column(String(180))
    location_source: Mapped[str | None] = mapped_column(String(16))
    user_agent: Mapped[str | None] = mapped_column(String(384))
    request_id: Mapped[str | None] = mapped_column(key_string(36))
