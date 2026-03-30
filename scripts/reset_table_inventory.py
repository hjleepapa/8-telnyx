#!/usr/bin/env python3
"""Delete all table_slot_inventory rows (PostgreSQL / SQLite).

Run on Render or locally after changing HANOK_TABLE_INVENTORY_JSON, or if counts
look wrong (e.g. every new reservation waitlists after the first). Inventory
rows are recreated on the next booking per slot from the JSON template.

  export DB_URI=postgresql://...
  python scripts/reset_table_inventory.py
"""

from __future__ import annotations

from sqlalchemy import delete

from telnyx_restaurant.config import database_url
from telnyx_restaurant.db import get_engine
from telnyx_restaurant.models import TableSlotInventory


def main() -> None:
    if not database_url():
        raise SystemExit("DB_URI / DATABASE_URL is not set.")
    get_engine()
    from telnyx_restaurant.db import SessionLocal

    if SessionLocal is None:
        raise SystemExit("Database session factory unavailable.")
    db = SessionLocal()
    try:
        db.execute(delete(TableSlotInventory))
        db.commit()
        print("table_slot_inventory cleared.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
