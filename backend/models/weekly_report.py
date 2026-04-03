from datetime import date
from sqlalchemy import Date, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base


class WeeklyReport(Base):
    __tablename__ = "weekly_reports"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.id"), nullable=False)
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    week_end: Mapped[date] = mapped_column(Date, nullable=False)
    source_filename: Mapped[str | None] = mapped_column(String(255))

    store: Mapped["Store"] = relationship(back_populates="weekly_reports")  # noqa: F821
    daily_sales: Mapped[list["DailySales"]] = relationship(  # noqa: F821
        back_populates="weekly_report", cascade="all, delete-orphan"
    )
    report_texts: Mapped[list["ReportText"]] = relationship(  # noqa: F821
        back_populates="weekly_report", cascade="all, delete-orphan"
    )
