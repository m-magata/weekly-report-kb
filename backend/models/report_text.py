from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base


class ReportText(Base):
    __tablename__ = "report_texts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    weekly_report_id: Mapped[int] = mapped_column(
        ForeignKey("weekly_reports.id"), nullable=False
    )
    # 週報①〜④に対応するシートインデックス（1始まり）
    sheet_index: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_name: Mapped[str | None] = mapped_column()
    content: Mapped[str | None] = mapped_column(Text)

    weekly_report: Mapped["WeeklyReport"] = relationship(  # noqa: F821
        back_populates="report_texts"
    )
