from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship
from backend.database import Base


class Store(Base):
    __tablename__ = "stores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    manager_name: Mapped[str | None] = mapped_column(String(100))

    weekly_reports: Mapped[list["WeeklyReport"]] = relationship(  # noqa: F821
        back_populates="store", cascade="all, delete-orphan"
    )
