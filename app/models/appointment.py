from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .base import Base


class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True)
    company_id = Column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    client_name = Column(String(150), nullable=False)
    client_phone = Column(String(32), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    title = Column(String(200), nullable=False)
    cal_booking_id = Column(String(64), nullable=False, unique=True)
    status = Column(String(32), nullable=False, default="pending")
    meeting_url = Column(String(512), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    confirmed_at = Column(DateTime(timezone=True), nullable=True)
    reminder_24h_sent = Column(DateTime(timezone=True), nullable=True)
    reminder_1h_sent = Column(DateTime(timezone=True), nullable=True)
    no_show_checked = Column(DateTime(timezone=True), nullable=True)

    company = relationship("Company", back_populates="appointments")

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "company_id": self.company_id,
            "client_name": self.client_name,
            "client_phone": self.client_phone,
            "start_time": self.start_time.isoformat() if self.start_time else "",
            "end_time": self.end_time.isoformat() if self.end_time else "",
            "title": self.title,
            "cal_booking_id": self.cal_booking_id,
            "status": self.status,
            "meeting_url": self.meeting_url or "",
            "created_at": self.created_at.isoformat() if self.created_at else "",
            "confirmed_at": self.confirmed_at.isoformat() if self.confirmed_at else "",
            "reminder_24h_sent": self.reminder_24h_sent.isoformat() if self.reminder_24h_sent else "",
            "reminder_1h_sent": self.reminder_1h_sent.isoformat() if self.reminder_1h_sent else "",
            "no_show_checked": self.no_show_checked.isoformat() if self.no_show_checked else "",
        }


__all__ = ["Appointment"]
