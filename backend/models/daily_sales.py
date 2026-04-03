from datetime import date
from sqlalchemy import Date, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base


class DailySales(Base):
    __tablename__ = "daily_sales"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    weekly_report_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_reports.id"), nullable=False
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    # 売上は千円単位で格納
    sales_amount: Mapped[float | None] = mapped_column(Float)
    customer_count: Mapped[int | None] = mapped_column(Integer)
    weather: Mapped[str | None] = mapped_column(String(50))

    weekly_report: Mapped["WeeklyReport"] = relationship(  # noqa: F821
        back_populates="daily_sales"
    )
