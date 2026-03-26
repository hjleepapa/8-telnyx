"""SQLAlchemy engine and session helpers."""

from __future__ import annotations

import logging
from collections.abc import Generator

from fastapi import HTTPException
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from telnyx_restaurant.config import database_url

logger = logging.getLogger(__name__)

Base = declarative_base()

_engine = None
SessionLocal: sessionmaker[Session] | None = None


def get_engine():
    global _engine, SessionLocal
    url = database_url()
    if not url:
        return None
    if _engine is None:
        _engine = create_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=5)
        SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=_engine)
        logger.info("Database engine configured")
    return _engine


def get_db() -> Generator[Session, None, None]:
    if not database_url():
        raise HTTPException(
            status_code=503,
            detail="Database not configured. Set DB_URI or DATABASE_URL.",
        )
    get_engine()
    if SessionLocal is None:
        raise HTTPException(status_code=503, detail="Database session unavailable.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _ensure_reservation_columns(engine) -> None:
    """Add columns when table already existed (e.g. Render) before new fields."""
    try:
        insp = inspect(engine)
        if not insp.has_table("reservations"):
            return
        cols = {c["name"] for c in insp.get_columns("reservations")}
    except Exception:
        logger.exception("Could not inspect reservations table")
        return

    statements: list[str] = []
    if "preorder_json" not in cols:
        statements.append("ALTER TABLE reservations ADD COLUMN preorder_json TEXT")
    if "food_subtotal_cents" not in cols:
        statements.append(
            "ALTER TABLE reservations ADD COLUMN food_subtotal_cents INTEGER NOT NULL DEFAULT 0"
        )
    if "preorder_discount_cents" not in cols:
        statements.append(
            "ALTER TABLE reservations ADD COLUMN preorder_discount_cents INTEGER NOT NULL DEFAULT 0"
        )
    if "food_total_cents" not in cols:
        statements.append(
            "ALTER TABLE reservations ADD COLUMN food_total_cents INTEGER NOT NULL DEFAULT 0"
        )
    if "source_channel" not in cols:
        statements.append(
            "ALTER TABLE reservations ADD COLUMN source_channel VARCHAR(32) NOT NULL DEFAULT 'online'"
        )
    if "reminder_call_status" not in cols:
        statements.append("ALTER TABLE reservations ADD COLUMN reminder_call_status VARCHAR(128)")

    if not statements:
        return
    try:
        with engine.begin() as conn:
            for stmt in statements:
                conn.execute(text(stmt))
        logger.info("Applied %s reservation column migration(s)", len(statements))
    except Exception:
        logger.exception("Reservation column migration failed — run manual ALTER if needed")


def init_db() -> bool:
    """Create tables if DB URL is set. Returns True if models were synced."""
    global _engine, SessionLocal
    from telnyx_restaurant import models  # noqa: F401 — register models

    engine = get_engine()
    if engine is None:
        logger.warning("No DB_URI/DATABASE_URL — skipping DB init")
        return False
    try:
        Base.metadata.create_all(bind=engine)
        _ensure_reservation_columns(engine)
    except Exception:
        logger.exception("Database create_all failed — check DB_URI / network / sslmode")
        _engine = None
        SessionLocal = None
        return False
    logger.info("Database tables ensured")
    return True
