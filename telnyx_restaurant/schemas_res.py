"""Pydantic schemas for reservation API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ReservationCreate(BaseModel):
    guest_name: str = Field(..., min_length=1, max_length=255)
    guest_phone: str = Field(..., min_length=3, max_length=64)
    party_size: int = Field(..., ge=1, le=20)
    starts_at: datetime
    special_requests: str | None = Field(None, max_length=2000)


class ReservationRead(BaseModel):
    id: int
    confirmation_code: str
    guest_name: str
    guest_phone: str
    party_size: int
    starts_at: datetime
    status: str
    special_requests: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReservationStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(pending|confirmed|seated|completed|cancelled)$")
