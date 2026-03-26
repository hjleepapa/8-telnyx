"""Demo rows when the reservations table is empty."""

from __future__ import annotations

import json
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
            preorder_json=json.dumps(
                [
                    {
                        "menu_item_id": "dolsot_bibimbap",
                        "name_en": "Dolsot bibimbap",
                        "quantity": 2,
                        "unit_price_cents": 2200,
                        "line_total_cents": 4400,
                    },
                    {
                        "menu_item_id": "bulgogi",
                        "name_en": "Soy-marinated bulgogi",
                        "quantity": 1,
                        "unit_price_cents": 2400,
                        "line_total_cents": 2400,
                    },
                ]
            ),
            food_subtotal_cents=6800,
            preorder_discount_cents=476,
            food_total_cents=6324,
            source_channel="online",
            reminder_call_status="demo_seed",
        ),
        Reservation(
            confirmation_code="HNK-9P1Q",
            guest_name="Alex Park",
            guest_phone="+15551234567",
            party_size=2,
            starts_at=now + timedelta(days=3, hours=18, minutes=30),
            status=ReservationStatus.pending.value,
            special_requests=None,
            source_channel="voice",
        ),
        Reservation(
            confirmation_code="HNK-3XZA",
            guest_name="Sam Lee",
            guest_phone="+15559876543",
            party_size=6,
            starts_at=now - timedelta(days=1, hours=19),
            status=ReservationStatus.completed.value,
            special_requests="Birthday — small cake",
            source_channel="online",
        ),
    ]
    for r in rows:
        db.add(r)
    db.commit()
    return len(rows)
