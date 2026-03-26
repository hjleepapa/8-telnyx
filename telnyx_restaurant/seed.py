"""Demo rows when the reservations table is empty."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from telnyx_restaurant.models import Reservation, ReservationStatus


def seed_demo_reservations(db: Session) -> int:
    existing = db.execute(select(Reservation.id).limit(1)).first()
    if existing:
        return 0

    now = datetime.now(UTC)
    rows = [
        Reservation(
            confirmation_code="HNK-7K2M",
            guest_name="Jordan Kim",
            guest_phone="+15550000001",
            party_size=4,
            starts_at=now + timedelta(days=1, hours=19),
            status=ReservationStatus.confirmed.value,
            special_requests="Window table if possible",
        ),
        Reservation(
            confirmation_code="HNK-9P1Q",
            guest_name="Alex Park",
            guest_phone="+15551234567",
            party_size=2,
            starts_at=now + timedelta(days=3, hours=18, minutes=30),
            status=ReservationStatus.pending.value,
            special_requests=None,
        ),
        Reservation(
            confirmation_code="HNK-3XZA",
            guest_name="Sam Lee",
            guest_phone="+15559876543",
            party_size=6,
            starts_at=now - timedelta(days=1, hours=19),
            status=ReservationStatus.completed.value,
            special_requests="Birthday — small cake",
        ),
    ]
    for r in rows:
        db.add(r)
    db.commit()
    return len(rows)
