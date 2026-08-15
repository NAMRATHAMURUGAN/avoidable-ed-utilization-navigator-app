"""Provider directory database models."""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base
from backend.models.model_run import JSONType


class Provider(Base):
    """Provider directory model distinguishing verified vs compatibility/demo records."""

    __tablename__ = "providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    address: Mapped[str] = mapped_column(String(255), nullable=False)
    city_state_zip: Mapped[str] = mapped_column(String(128), nullable=False)
    distance_miles: Mapped[float] = mapped_column(Float, nullable=False)
    is_open_247: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    operating_hours: Mapped[str] = mapped_column(String(255), nullable=False)
    current_wait_time_mins: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    copay_amount: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    average_total_cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    phone: Mapped[str] = mapped_column(String(64), nullable=False)
    telehealth_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    accepts_medicare: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    services: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    is_demo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
