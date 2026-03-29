"""REST API for reservations (MCP / voice tools will call this)."""

from __future__ import annotations

import json
import logging
import re
import secrets
import string
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect, Request

from telnyx_restaurant.config import (
    hanok_default_reservation_duration_minutes,
    hanok_reservation_verbose_logging,
    hanok_slot_step_minutes,
    hanok_table_allocation_enabled,
    hanok_voice_create_dedup_seconds,
)
from telnyx_restaurant.db import get_db
from telnyx_restaurant.menu_catalog import MENU_ITEMS
from telnyx_restaurant.phone_normalize import phone_lookup_variants, to_e164_us
from telnyx_restaurant.models import Reservation, ReservationStatus
from telnyx_restaurant.preorder_calc import serialize_preorder
from telnyx_restaurant.reminders import schedule_demo_reminder_call
from telnyx_restaurant.seating_service import (
    SeatingUnavailableError,
    book_on_create,
    effective_priority_for_row,
    iter_day_slot_starts,
    snapshot_effective_availability,
)
from telnyx_restaurant.table_allocation import floor_slot_start
from telnyx_restaurant.schemas_res import (
    ReservationCreate,
    ReservationRead,
    ReservationStatusUpdate,
    ReservationUpdate,
    _unwrap_nested_reservation_payload,
)

router = APIRouter(prefix="/api/reservations", tags=["reservations"])
logger = logging.getLogger(__name__)


def _shallow_body_summary(raw: dict[str, Any], *, max_keys: int = 30) -> dict[str, Any]:
    """Truncate values for logs (no full preorder blobs)."""
    out: dict[str, Any] = {}
    for k in sorted(raw.keys())[:max_keys]:
        v = raw[k]
        if isinstance(v, dict):
            out[k] = f"<dict {len(v)} keys>"
        elif isinstance(v, list):
            out[k] = f"<list len={len(v)}>"
        elif isinstance(v, str) and len(v) > 100:
            out[k] = f"{v[:100]}…"
        else:
            out[k] = v
    return out

CHANGED_HDR = "X-Hanok-Reservation-Changed"


def _normalize_starts_at_cmp(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _booking_mutable_snapshot(row: Reservation) -> tuple:
    """Comparable snapshot for detecting real PATCH mutations (avoids pointless commits / LLM confusion)."""
    return (
        row.guest_name,
        row.guest_phone,
        row.party_size,
        _normalize_starts_at_cmp(row.starts_at),
        row.status,
        row.preorder_json,
        row.special_requests,
        row.food_subtotal_cents,
        row.preorder_discount_cents,
        row.food_total_cents,
        row.preferred_locale,
    )


def _set_changed_header(response: Response | None, changed: bool) -> None:
    if response is not None:
        response.headers[CHANGED_HDR] = "1" if changed else "0"

_PATCH_NO_FIELDS_DETAIL = (
    "No recognized fields to apply after removing confirmation_code. The server did not update the row. "
    "Include at least one of: preorder lines, party_size, starts_at, status, guest_name, "
    "guest_phone, special_requests, preferred_locale. Examples: "
    '{"confirmation_code":"HNK-ABCD","party_size":4,"starts_at":"2026-03-28T19:00:00+00:00"} '
    'or {"confirmation_code":"HNK-ABCD","preorder":[{"menu_item_id":"bulgogi","quantity":1}]}. '
    "Empty array preorder/items [] does not change food. "
    "JSON null on preorder clears the cart when it is the real intent: not a Telnyx multi-key "
    "template where party_size, starts_at, guest fields, and preorder are all JSON null placeholders."
)


def _has_truthy_non_preorder_patch(body: ReservationUpdate) -> bool:
    """True if the patch includes any non-null value outside preorder (Telnyx multi-field templates)."""
    fs = body.model_fields_set
    if "party_size" in fs and body.party_size is not None:
        return True
    if "starts_at" in fs and body.starts_at is not None:
        return True
    if "guest_name" in fs and body.guest_name is not None:
        return True
    if "guest_phone" in fs and body.guest_phone is not None:
        return True
    if "status" in fs and body.status is not None:
        return True
    if "special_requests" in fs and body.special_requests is not None:
        return True
    if "preferred_locale" in fs and body.preferred_locale is not None:
        return True
    return False


def _telnyx_null_placeholder_bundle(body: ReservationUpdate) -> bool:
    """True when the body looks like a Telnyx amend template: several booking keys, all JSON null.

    Those clients send confirmation_code, guest_name, party_size, preorder, etc. on every call.
    preorder: null in that shape means 'unchanged', not 'clear cart'.
    """
    fs = body.model_fields_set
    bundle = frozenset(
        (
            "party_size",
            "starts_at",
            "guest_name",
            "guest_phone",
            "special_requests",
            "preorder",
            "preferred_locale",
        )
    )
    present = bundle & fs
    if len(present) < 2:
        return False
    for key in present:
        if getattr(body, key) is not None:
            return False
    return True


def _preorder_null_clears_cart(body: ReservationUpdate) -> bool:
    """Whether JSON preorder: null should clear stored cart (vs Telnyx null alongside party/time)."""
    if "preorder" not in body.model_fields_set or body.preorder is not None:
        return False
    if _telnyx_null_placeholder_bundle(body):
        return False
    return not _has_truthy_non_preorder_patch(body)


def _truthy_non_status_reservation_fields(body: ReservationUpdate) -> bool:
    """True if the patch would apply any field other than status with a non-None value.

    Telnyx templates include explicit JSON nulls for party_size, starts_at, etc.; those keys must
    not trip _reject_modifying_cancelled when the guest is only cancelling.
    """
    eff = _effective_reservation_patch_fields(body)
    eff.discard("status")
    for k in eff:
        if getattr(body, k) is not None:
            return True
    return False


def _effective_reservation_patch_fields(body: ReservationUpdate) -> set[str]:
    """Telnyx sends preorder: [] alongside other fields; [] means omit, not clear (see _apply_reservation_update)."""
    fs = set(body.model_fields_set)
    if "preorder" in fs and body.preorder is not None and len(body.preorder) == 0:
        fs.discard("preorder")
    if "preorder" in fs and body.preorder is None and not _preorder_null_clears_cart(body):
        fs.discard("preorder")
    return fs


def _require_reservation_update_fields(body: ReservationUpdate) -> None:
    """PATCH used to return 200 with an unchanged row when the tool only sent code — LLMs then claimed success."""
    if not _effective_reservation_patch_fields(body):
        raise HTTPException(status_code=422, detail=_PATCH_NO_FIELDS_DETAIL)


def _reject_modifying_cancelled(row: Reservation) -> None:
    if row.status == ReservationStatus.cancelled.value:
        raise HTTPException(
            status_code=409,
            detail="Reservation is cancelled; create a new booking or change status first.",
        )


def _apply_reservation_update(db: Session, row: Reservation, body: ReservationUpdate) -> bool:
    # Caller must use _require_reservation_update_fields before this when a PATCH should do work.
    # Telnyx/tools often include JSON nulls for untouched fields; never write NULL into NOT NULL columns.
    before_status = row.status
    before = _booking_mutable_snapshot(row)
    if "guest_name" in body.model_fields_set and body.guest_name is not None:
        row.guest_name = body.guest_name  # type: ignore[assignment]
    if "guest_phone" in body.model_fields_set and body.guest_phone is not None:
        gp = body.guest_phone.strip()  # type: ignore[union-attr]
        row.guest_phone = to_e164_us(gp) if gp else row.guest_phone
    if "party_size" in body.model_fields_set and body.party_size is not None:
        row.party_size = body.party_size  # type: ignore[assignment]
    if "starts_at" in body.model_fields_set:
        st = body.starts_at
        if st is not None:
            row.starts_at = st if st.tzinfo else st.replace(tzinfo=UTC)
    if "special_requests" in body.model_fields_set:
        row.special_requests = body.special_requests
    if "preferred_locale" in body.model_fields_set and body.preferred_locale is not None:
        row.preferred_locale = body.preferred_locale  # type: ignore[assignment]
    if "status" in body.model_fields_set and body.status is not None:
        row.status = body.status
    if "preorder" in body.model_fields_set:
        if body.preorder is None:
            if _preorder_null_clears_cart(body):
                row.preorder_json = None
                row.food_subtotal_cents = 0
                row.preorder_discount_cents = 0
                row.food_total_cents = 0
            # else: Telnyx sent preorder: null next to real party_size/starts_at — keep existing cart
        elif not body.preorder:
            # Telnyx tools often send preorder/items: [] in the same template as party_size/time.
            # Empty list means "do not change food", not "clear cart" (use preorder: null to clear).
            pass
        else:
            try:
                preorder_json, subtotal, discount, total = serialize_preorder(body.preorder)
            except ValueError as e:
                raise HTTPException(status_code=400, detail=str(e)) from e
            row.preorder_json = preorder_json
            row.food_subtotal_cents = subtotal
            row.preorder_discount_cents = discount
            row.food_total_cents = total
    after = _booking_mutable_snapshot(row)
    if after == before:
        return False
    row.updated_at = datetime.now(UTC)
    if (
        hanok_table_allocation_enabled()
        and row.status == ReservationStatus.cancelled.value
        and before_status != ReservationStatus.cancelled.value
    ):
        from telnyx_restaurant.seating_service import release_and_promote_after_cancel

        release_and_promote_after_cancel(db, row)
    return True


def _merge_status_from_cancel_query(
    status: str | None,
    cancel: str | None,
) -> str | None:
    """Telnyx tools often send ?cancel=1 or ?cancel=true instead of JSON."""
    if (status or "").strip():
        return status.strip()
    cf = (cancel or "").strip().lower()
    if cf in ("1", "true", "yes", "y", "cancel"):
        return "cancelled"
    return None


def _flat_status_is_null_or_blank(flat: dict[str, Any]) -> bool:
    cur = flat.get("status")
    return cur is None or (isinstance(cur, str) and not cur.strip())


def _flat_apply_cancel_and_query_status(
    flat: dict[str, Any],
    *,
    query_status: str | None,
    cancel: str | None,
) -> None:
    merged_q = _merge_status_from_cancel_query(query_status, cancel)
    if merged_q and _flat_status_is_null_or_blank(flat):
        flat["status"] = merged_q
    for flag in ("cancel", "Cancel", "cancel_reservation", "cancellation_requested"):
        v = flat.get(flag)
        if v is True:
            flat["status"] = "cancelled"
        elif isinstance(v, str) and v.strip().casefold() in ("true", "1", "yes", "y"):
            flat["status"] = "cancelled"


def _flat_strong_status_token(flat: dict[str, Any]) -> str | None:
    """Non-empty status string from common keys, lowercased (not JSON null)."""
    for key in (
        "status",
        "Status",
        "reservation_status",
        "reservationStatus",
        "booking_status",
    ):
        v = flat.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip().casefold()
    return None


def _flat_has_cancel_status_value(flat: dict[str, Any]) -> bool:
    st = _flat_strong_status_token(flat)
    if not st:
        return False
    s = st.replace(" ", "_").replace("-", "_")
    return s in {
        "cancel",
        "cancelled",
        "canceled",
        "cancellation",
        "void",
        "voided",
        "delete",
        "deleted",
    }


def _flat_infer_cancel_from_voice_aliases(flat: dict[str, Any]) -> None:
    """Telnyx tools often send status=null with operation/action=cancel — set status for PATCH."""
    strong = _flat_strong_status_token(flat)
    if strong in ("confirmed", "pending", "seated", "completed"):
        return
    if _flat_has_cancel_status_value(flat):
        return
    cancelish_keys = (
        "operation",
        "Operation",
        "action",
        "Action",
        "intent",
        "Intent",
        "command",
        "Command",
        "event",
        "Event",
        "type",
        "Type",
        "reservation_action",
        "booking_action",
        "change_type",
    )
    cancel_tokens = frozenset(
        {
            "cancel",
            "cancelled",
            "canceled",
            "cancellation",
            "cancel_reservation",
            "delete",
            "deleted",
            "void",
            "voided",
        }
    )
    for key in cancelish_keys:
        v = flat.get(key)
        if isinstance(v, str):
            t = v.strip().casefold().replace(" ", "_").replace("-", "_")
            if t in cancel_tokens or t.endswith("_cancel") or t.startswith("cancel_"):
                if _flat_status_is_null_or_blank(flat):
                    flat["status"] = "cancelled"
                return
        elif v is True and key and "cancel" in key.lower():
            if _flat_status_is_null_or_blank(flat):
                flat["status"] = "cancelled"
            return


def _patch_at_status_core(db: Session, row: Reservation, flat: dict[str, Any]) -> tuple[Reservation, bool]:
    """Sync DB work for PATCH …/status (run in threadpool — do not call from async without offload)."""
    patch: ReservationUpdate | None = None
    patch_exc: ValidationError | None = None
    try:
        patch = ReservationUpdate.model_validate(flat)
    except ValidationError as e:
        patch = None
        patch_exc = e

    eff = _effective_reservation_patch_fields(patch) if patch is not None else set()
    if patch is not None and eff:
        if _truthy_non_status_reservation_fields(patch):
            _reject_modifying_cancelled(row)
        changed = _apply_reservation_update(db, row, patch)
        if changed:
            db.commit()
        else:
            db.rollback()
        db.refresh(row)
        logger.info(
            "PATCH …/status row_id=%s path=reservation_update eff=%s changed=%s",
            row.id,
            sorted(eff),
            changed,
        )
        return row, changed

    try:
        parsed = ReservationStatusUpdate.model_validate(flat)
    except ValidationError as exc:
        if patch_exc is not None and flat:
            logger.warning(
                "PATCH …/status 422: ReservationUpdate failed keys=%s errors=%s",
                sorted(flat.keys())[:40],
                patch_exc.errors()[:8],
            )
            raise HTTPException(
                status_code=422,
                detail=patch_exc.errors(),
            ) from patch_exc
        if patch is not None and not eff:
            logger.warning(
                "PATCH …/status 422: body produced no settable fields (all unknown/null?). "
                "flat_keys=%s",
                sorted(flat.keys())[:40],
            )
        else:
            logger.warning(
                "PATCH …/status 422: flat_keys=%s pydantic_tail=%s",
                sorted(flat.keys())[:40],
                exc.errors()[:4],
            )
        raise HTTPException(
            status_code=422,
            detail=(
                "No valid fields to update. Send JSON with party_size (or partySize), starts_at (or startsAt), "
                "preorder, guest_name, status; or ?cancel=1. Empty PATCH body always 422. "
                f"Pydantic: {exc.errors()}"
            ),
        ) from exc

    if row.status == parsed.status:
        db.rollback()
        db.refresh(row)
        logger.info(
            "PATCH …/status row_id=%s path=status_only noop (already %s) flat_keys=%s",
            row.id,
            parsed.status,
            sorted(flat.keys())[:30],
        )
        return row, False
    prev_status = row.status
    row.status = parsed.status
    row.updated_at = datetime.now(UTC)
    if (
        hanok_table_allocation_enabled()
        and row.status == ReservationStatus.cancelled.value
        and prev_status != ReservationStatus.cancelled.value
    ):
        from telnyx_restaurant.seating_service import release_and_promote_after_cancel

        release_and_promote_after_cancel(db, row)
    db.commit()
    db.refresh(row)
    logger.info(
        "PATCH …/status row_id=%s path=status_only status->%s",
        row.id,
        parsed.status,
    )
    return row, True


def _patch_at_status_in_thread(row_id: int, flat: dict[str, Any]) -> tuple[ReservationRead, bool]:
    """Open a short-lived session in a worker thread so sync SQLAlchemy cannot block the event loop."""
    from telnyx_restaurant import db as db_mod

    db_mod.get_engine()
    SL = db_mod.SessionLocal
    if SL is None:
        raise HTTPException(status_code=503, detail="Database session unavailable.")
    db = SL()
    try:
        row = db.get(Reservation, row_id)
        if not row:
            raise HTTPException(status_code=404, detail="Reservation not found")
        row, changed = _patch_at_status_core(db, row, flat)
        return ReservationRead.model_validate(row), changed
    finally:
        db.close()


async def _patch_at_status_url(
    request: Request,
    *,
    row_id: int,
    query_status: str | None,
    cancel: str | None,
) -> tuple[ReservationRead, bool]:
    """Telnyx often binds `…/status` for all updates — accept full ReservationUpdate here too.

    Body is read asynchronously; DB work runs in a threadpool so MCP/voice clients are not stalled.
    """
    body = await read_json_or_form_body(request)
    flat = _unwrap_nested_reservation_payload(dict(body))
    for k in (
        "confirmation_code",
        "code",
        "confirmationCode",
        "hnk_code",
        "reservation_code",
        "next_reservation_code",
    ):
        flat.pop(k, None)

    _flat_apply_cancel_and_query_status(flat, query_status=query_status, cancel=cancel)
    _flat_infer_cancel_from_voice_aliases(flat)

    if hanok_reservation_verbose_logging():
        logger.info(
            "PATCH …/status row_id=%s query_status=%r cancel=%r flat_keys=%s body=%s",
            row_id,
            query_status,
            cancel,
            sorted(flat.keys())[:40],
            _shallow_body_summary(flat, max_keys=24),
        )

    return await run_in_threadpool(_patch_at_status_in_thread, row_id, flat)


async def read_status_request_payload(request: Request) -> dict[str, Any]:
    """Telnyx HTTP tools often use form encoding or plain text, not JSON — avoid FastAPI Body 422."""
    hdr = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" in hdr or "multipart/form-data" in hdr:
        try:
            form = await request.form()
        except ClientDisconnect:
            return {}
        except Exception:
            return {}
        out: dict[str, Any] = {}
        for key, val in form.multi_items():
            if hasattr(val, "read"):
                continue
            out[str(key)] = str(val)
        return out

    try:
        raw = await request.body()
    except ClientDisconnect:
        return {}
    if not raw or not raw.strip():
        return {}

    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if "json" in ct or ct in ("", "text/plain", "application/json", "text/json", "application/problem+json"):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            text = raw.decode("utf-8", errors="replace").strip()
            return {"status": text} if text else {}
        if isinstance(data, dict):
            return data
        coerced = _coerce_json_root_to_dict(data)
        if coerced:
            return coerced
        if isinstance(data, str):
            return {"status": data}
        return {}

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        text = raw.decode("utf-8", errors="replace").strip()
        return {"status": text} if text else {}
    if isinstance(data, dict):
        return data
    coerced = _coerce_json_root_to_dict(data)
    if coerced:
        return coerced
    return data if isinstance(data, dict) else {"status": str(data)}


_JSON_ARRAY_SINGLE_OBJECT_KEYS = frozenset(
    {
        "guest_name",
        "guest_phone",
        "party_size",
        "partySize",
        "party",
        "starts_at",
        "startsAt",
        "start_time",
        "name",
        "phone",
        "status",
        "id",
        "reservation_id",
        "reservationId",
        "booking_id",
        "confirmation_code",
        "code",
        "confirmationCode",
        "hnk_code",
        "reservation_code",
        "next_reservation_code",
        "preorder",
        "pre_order",
        "items",
        "lines",
        "menu",
        "cart",
        "dishes",
        "special_requests",
    }
)


def _coerce_json_root_to_dict(data: Any) -> dict[str, Any]:
    """Telnyx sometimes POSTs a JSON array of one reservation / amend object."""
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        z = data[0]
        if _JSON_ARRAY_SINGLE_OBJECT_KEYS.intersection(z):
            return z
    return {}


async def read_json_or_form_body(request: Request) -> dict[str, Any]:
    """POST body for JSON-like APIs (create). Accepts JSON or form; no fake `status` field from plain text."""
    hdr = (request.headers.get("content-type") or "").lower()
    if "application/x-www-form-urlencoded" in hdr or "multipart/form-data" in hdr:
        try:
            form = await request.form()
        except ClientDisconnect:
            logger.warning("read_json_or_form_body: client disconnected during form()")
            return {}
        except Exception:
            return {}
        out: dict[str, Any] = {}
        jsonish_keys = (
            "preorder",
            "items",
            "lines",
            "pre_order",
            "menu_order",
            "menu",
            "cart",
            "dishes",
            "data",
            "body",
            "payload",
            "food",
            "selected_dishes",
            "order_items",
            "food_items",
            "basket",
            "meal_selection",
            "variables",
        )
        for key, val in form.multi_items():
            if hasattr(val, "read"):
                continue
            k = str(key)
            v = "" if val is None else str(val)
            if k in jsonish_keys and v.strip().startswith(("{", "[")):
                try:
                    out[k] = json.loads(v)
                except json.JSONDecodeError:
                    out[k] = v
            else:
                out[k] = v
        return out

    try:
        raw = await request.body()
    except ClientDisconnect:
        logger.warning("read_json_or_form_body: client disconnected during body()")
        return {}

    if not raw or not raw.strip():
        return {}

    ct = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if "json" in ct or ct in ("", "application/json", "text/json", "application/problem+json"):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        d = _coerce_json_root_to_dict(data)
        return d

    try:
        data = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return _coerce_json_root_to_dict(data)


def _reject_unsubstituted_path_value(value: str, *, field: str = "code") -> str:
    """Telnyx/webhook misconfig often leaves {{code}} in the path; fail loudly."""
    v = (value or "").strip()
    if not v:
        raise HTTPException(status_code=400, detail=f"Missing {field}.")
    if "{{" in v or "}}" in v:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsubstituted URL template in {field}: {v!r}. "
                "In Telnyx, define a real Path/query parameter (e.g. confirmation_code) on the tool—"
                "do not type {{code}} in the URL. To find a booking without the code, use "
                "GET /api/reservations/lookup?phone={{caller_number}}&guest_name=… "
                "(name + phone is the primary lookup; map phone to telnyx_end_user_target from dynamic variables)."
            ),
        )
    return v


def _parse_reservation_id_path(raw: str) -> int:
    """Parse `/{id}` path segment; avoid FastAPI int 422 when Telnyx sends literal `{{reservation_id}}`."""
    v = (raw or "").strip()
    if not v:
        raise HTTPException(status_code=400, detail="Missing reservation id.")
    if "{{" in v or "}}" in v or "%7b%7b" in v.lower() or "%7d%7d" in v.lower():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsubstituted or invalid reservation id in path: {v!r}. "
                "Bind the tool path parameter to the numeric `id` from the create response JSON, "
                "or use PATCH /api/reservations/by-code/{{confirmation_code}} with the HNK-… code."
            ),
        )
    try:
        rid = int(v, 10)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="reservation_id must be a positive integer.",
        ) from None
    if rid < 1:
        raise HTTPException(status_code=400, detail="reservation_id must be a positive integer.")
    return rid


def _gen_confirmation_code() -> str:
    part = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(4))
    return f"HNK-{part}"


def _normalize_confirmation_code(code: str) -> str:
    """Fix ASR/LLM dropping the hyphen or appending junk: HNKWGJF → HNK-WGJF; HNK-WJGK-F → HNK-WJGK."""
    c = (code or "").strip().upper().replace(" ", "")
    if re.fullmatch(r"HNK-[A-Z0-9]{4}", c):
        return c
    m = re.fullmatch(r"HNK([A-Z0-9]{4})", c)
    if m:
        return f"HNK-{m.group(1)}"
    m2 = re.match(r"^(HNK-[A-Z0-9]{4})", c)
    if m2:
        return m2.group(1)
    return c


def _guest_name_matches(stored_full: str, hint: str) -> bool:
    """Case-insensitive match: full substring, first-name, or shared tokens."""
    def _norm_name_chunk(x: str) -> str:
        return (x or "").strip().casefold().rstrip(".")

    s = _norm_name_chunk(stored_full)
    h = _norm_name_chunk(hint)
    if not h:
        return False
    if not s:
        return False
    if h == s or h in s or s in h:
        return True
    s_parts = s.split()
    h_parts = h.split()
    if h_parts and s_parts and h_parts[0] == s_parts[0]:
        return True
    if set(h_parts) & set(s_parts):
        return True
    # e.g. guest_name on file "HJ" vs lookup "H James" / "H. James"
    alpha_words = re.findall(r"[A-Za-z0-9]+", h)
    if len(alpha_words) >= 2 and len(s) <= 8 and " " not in s:
        initials = "".join(w[0] for w in alpha_words if w).casefold()
        if len(initials) >= 2 and initials == s:
            return True
    return False


def _candidate_pool_for_phone(
    db: Session, variants: list[str], now: datetime
) -> list[Reservation]:
    rows = list(
        db.execute(
            select(Reservation)
            .where(
                Reservation.guest_phone.in_(variants),
                Reservation.status != ReservationStatus.cancelled.value,
            )
            .order_by(Reservation.starts_at.desc())
        )
        .scalars()
        .all()
    )
    upcoming = [r for r in rows if r.starts_at >= now]
    if upcoming:
        return sorted(upcoming, key=lambda r: r.starts_at)
    return sorted(rows, key=lambda r: r.starts_at, reverse=True)


def _is_menu_order_line_dict(d: dict[str, Any]) -> bool:
    """Skip nested preorder lines when scavenging reservation id / code from tool JSON."""
    return bool(d.get("menu_item_id") or d.get("menuItemId"))


def _truthy_identity_token(v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, bool):
        return False
    s = str(v).strip()
    if not s or s.lower() in ("null", "none", "undefined"):
        return False
    return True


def _flat_guest_identity_for_amend(flat: dict[str, Any]) -> tuple[str, str] | None:
    """Telnyx often sends null confirmation_code but still includes guest_name / guest_phone."""
    gn = flat.get("guest_name")
    if gn is None:
        gn = flat.get("name") or flat.get("customer_name") or flat.get("guestName")
    gp = flat.get("guest_phone")
    if gp is None:
        gp = (
            flat.get("phone")
            or flat.get("guestPhone")
            or flat.get("telnyx_end_user_target")
            or flat.get("caller_number")
        )
    if not _truthy_identity_token(gn) or not _truthy_identity_token(gp):
        return None
    gn = str(gn).strip()
    gp = str(gp).strip()
    if len(gp) < 3:
        return None
    return gn, gp


def _amend_resolve_row_via_guest_lookup(db: Session, flat: dict[str, Any]) -> Reservation | None:
    ident = _flat_guest_identity_for_amend(flat)
    if not ident:
        return None
    guest_name_hint, raw_phone = ident
    variants = phone_lookup_variants(raw_phone)
    if not variants:
        return None
    now = datetime.now(UTC)
    pool = _candidate_pool_for_phone(db, variants, now)
    if not pool:
        return None
    matched = [r for r in pool if _guest_name_matches(r.guest_name, guest_name_hint)]
    if len(matched) != 1:
        return None
    return matched[0]


def _scavenge_reservation_id_int(obj: Any, depth: int = 0) -> int | None:
    """Find reservation row id in nested objects (e.g. tool context), avoiding preorder lines."""
    id_keys = ("reservation_id", "reservationId", "booking_id")
    if depth > 14:
        return None
    if isinstance(obj, dict):
        if _is_menu_order_line_dict(obj):
            return None
        for k in id_keys:
            v = obj.get(k)
            if isinstance(v, int) and v >= 1:
                return v
            if isinstance(v, str) and v.strip().isdigit() and int(v.strip(), 10) >= 1:
                return int(v.strip(), 10)
        v = obj.get("id")
        if isinstance(v, int) and v >= 1:
            return v
        if isinstance(v, str) and v.strip().isdigit() and int(v.strip(), 10) >= 1:
            return int(v.strip(), 10)
        for v in obj.values():
            got = _scavenge_reservation_id_int(v, depth + 1)
            if got is not None:
                return got
    elif isinstance(obj, list):
        for it in obj:
            got = _scavenge_reservation_id_int(it, depth + 1)
            if got is not None:
                return got
    return None


def _scavenge_confirmation_code_str(obj: Any, depth: int = 0) -> str | None:
    """Find HNK-… or similar in nested payload when root confirmation_code is null."""
    code_keys = (
        "confirmation_code",
        "code",
        "confirmationCode",
        "hnk_code",
        "reservation_code",
        "next_reservation_code",
    )
    if depth > 14:
        return None
    if isinstance(obj, dict):
        if _is_menu_order_line_dict(obj):
            return None
        for k in code_keys:
            v = obj.get(k)
            if _truthy_identity_token(v):
                return str(v).strip()
        for v in obj.values():
            got = _scavenge_confirmation_code_str(v, depth + 1)
            if got:
                return got
    elif isinstance(obj, list):
        for it in obj:
            got = _scavenge_confirmation_code_str(it, depth + 1)
            if got:
                return got
    return None


@router.get("/menu/items")
def list_menu_items():
    """Public menu with prices for the online reservation pre-order step."""
    return [m.as_public() for m in MENU_ITEMS]


@router.get("/seating/availability")
def get_seating_availability(
    date_str: str = Query(
        ...,
        alias="date",
        description="UTC calendar day as YYYY-MM-DD.",
    ),
    db: Session = Depends(get_db),
):
    """Per–time-bucket effective table counts (min across sizes); requires HANOK_TABLE_ALLOCATION_ENABLED=1."""
    if not hanok_table_allocation_enabled():
        raise HTTPException(
            status_code=404,
            detail="Table allocation is disabled. Set HANOK_TABLE_ALLOCATION_ENABLED=1.",
        )
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid date: use YYYY-MM-DD.",
        ) from None
    step = hanok_slot_step_minutes()
    day_anchor = datetime(d.year, d.month, d.day, tzinfo=UTC)
    slots = iter_day_slot_starts(day_anchor, step)
    return {
        "date": date_str,
        "slot_minutes": step,
        "slots": snapshot_effective_availability(db, slots),
    }


@router.get("", response_model=list[ReservationRead])
def list_reservations(
    status: str | None = None,
    db: Session = Depends(get_db),
):
    q = select(Reservation).order_by(Reservation.starts_at.desc())
    if status:
        q = q.where(Reservation.status == status)
    return list(db.execute(q).scalars().all())


def _voice_create_recent_duplicate(
    db: Session,
    *,
    body: ReservationCreate,
    starts_at: datetime,
    guest_phone_e164: str,
    window_seconds: int,
) -> Reservation | None:
    """If a voice reservation was just created for the same phone, slot, and party, return it (Telnyx duplicate tools)."""
    if window_seconds <= 0 or body.source_channel != "voice":
        return None
    cutoff = datetime.now(UTC) - timedelta(seconds=window_seconds)
    phones = phone_lookup_variants(guest_phone_e164)
    if not phones:
        return None
    stmt = (
        select(Reservation)
        .where(
            Reservation.guest_phone.in_(phones),
            Reservation.party_size == body.party_size,
            Reservation.starts_at == starts_at,
            Reservation.status != ReservationStatus.cancelled.value,
            Reservation.source_channel == "voice",
            Reservation.created_at >= cutoff,
        )
        .order_by(Reservation.created_at.asc())
        .limit(1)
    )
    return db.execute(stmt).scalar_one_or_none()


@router.post("", response_model=ReservationRead)
async def create_reservation(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    raw = await read_json_or_form_body(request)
    try:
        body = ReservationCreate.model_validate(raw)
    except ValidationError as exc:
        if isinstance(raw, dict):
            logger.warning(
                "Reservation create 422: top_keys=%s pydantic_errors=%s",
                list(raw.keys())[:40],
                exc.errors()[:8],
            )
        else:
            logger.warning(
                "Reservation create 422: body_type=%s pydantic_errors=%s",
                type(raw).__name__,
                exc.errors()[:8],
            )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    gp_strip = body.guest_phone.strip()
    guest_phone_e164 = to_e164_us(gp_strip) if gp_strip else gp_strip
    starts_at_norm = (
        body.starts_at if body.starts_at.tzinfo else body.starts_at.replace(tzinfo=UTC)
    )
    if hanok_table_allocation_enabled():
        starts_at_norm = floor_slot_start(starts_at_norm, hanok_slot_step_minutes())

    window = hanok_voice_create_dedup_seconds()
    if window > 0 and body.source_channel == "voice":
        dup = _voice_create_recent_duplicate(
            db,
            body=body,
            starts_at=starts_at_norm,
            guest_phone_e164=guest_phone_e164,
            window_seconds=window,
        )
        if dup is not None:
            response.headers["X-Hanok-Deduplicated"] = "1"
            if body.preorder and not dup.preorder_items:
                patch = ReservationUpdate(preorder=body.preorder)
                changed = _apply_reservation_update(db, dup, patch)
                if changed:
                    db.commit()
                else:
                    db.rollback()
                db.refresh(dup)
                logger.info(
                    "POST /api/reservations voice dedup merge preorder reservation_id=%s",
                    dup.id,
                )
            else:
                logger.info(
                    "POST /api/reservations voice dedup return existing reservation_id=%s",
                    dup.id,
                )
            return dup

    code = _gen_confirmation_code()
    for _ in range(10):
        if not db.execute(
            select(Reservation.id).where(Reservation.confirmation_code == code)
        ).first():
            break
        code = _gen_confirmation_code()

    try:
        preorder_json, subtotal, discount, total = serialize_preorder(body.preorder)
    except ValueError as e:
        logger.warning(
            "Reservation create 400 (pre-order): guest=%s keys_in_raw=%s error=%s",
            (body.guest_name or "")[:80],
            list(raw.keys())[:30] if isinstance(raw, dict) else type(raw).__name__,
            str(e),
        )
        raise HTTPException(status_code=400, detail=str(e)) from e

    starts_at = starts_at_norm
    duration_minutes = (
        body.duration_minutes
        if body.duration_minutes is not None
        else hanok_default_reservation_duration_minutes()
    )
    guest_priority = effective_priority_for_row(body.guest_priority, total)
    row = Reservation(
        confirmation_code=code,
        guest_name=body.guest_name,
        guest_phone=to_e164_us(gp_strip) if gp_strip else gp_strip,
        party_size=body.party_size,
        starts_at=starts_at,
        duration_minutes=duration_minutes,
        guest_priority=guest_priority,
        status=ReservationStatus.confirmed.value,
        special_requests=body.special_requests,
        preorder_json=preorder_json,
        food_subtotal_cents=subtotal,
        preorder_discount_cents=discount,
        food_total_cents=total,
        source_channel=body.source_channel,
        preferred_locale=body.preferred_locale,
        reminder_call_status=(
            "no_outbound_reminder_source_api"
            if body.source_channel == "api"
            else "reminder_queued"
        ),
    )
    db.add(row)
    try:
        db.flush()
        if hanok_table_allocation_enabled():
            book_on_create(db, row, waitlist_ok=body.waitlist_if_full)
        db.commit()
    except SeatingUnavailableError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    db.refresh(row)

    if body.source_channel != "api":
        schedule_demo_reminder_call(
            reservation_id=row.id,
            guest_phone=row.guest_phone,
            guest_name=row.guest_name,
            confirmation_code=row.confirmation_code,
        )
    return row


@router.get("/lookup", response_model=ReservationRead)
def lookup_reservation_by_phone_and_name(
    guest_name: str = Query(
        ...,
        min_length=1,
        max_length=255,
        description="First or full name as on the reservation (required).",
    ),
    phone: str | None = Query(
        None,
        description="Guest phone — Telnyx tools often use `guest_phone` instead; either is accepted (empty `phone=` is ignored).",
    ),
    guest_phone: str | None = Query(
        None,
        description="Alias for `phone` (same value from dynamic variables / caller id).",
    ),
    db: Session = Depends(get_db),
):
    """Primary lookup: **phone + guest name**. Use this instead of confirmation codes when ASR mis-hears HNK-…"""
    raw_phone = ((phone or guest_phone) or "").strip()
    if len(raw_phone) < 3:
        raise HTTPException(
            status_code=422,
            detail="Send query parameter `phone` or `guest_phone` (guest line number, min 3 characters).",
        )
    phone = raw_phone
    if "{{" in phone or "}}" in phone:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsubstituted template in phone query. Bind `phone` to the caller "
                "(e.g. telnyx_end_user_target), not literal {{…}} in the URL."
            ),
        )
    if guest_name and ("{{" in guest_name or "}}" in guest_name):
        raise HTTPException(
            status_code=400,
            detail="Unsubstituted template in guest_name query.",
        )
    variants = phone_lookup_variants(phone.strip())
    if not variants:
        raise HTTPException(status_code=400, detail="Invalid or empty phone value.")

    now = datetime.now(UTC)
    pool = _candidate_pool_for_phone(db, variants, now)
    if not pool:
        raise HTTPException(
            status_code=404,
            detail="No reservation found for this phone number.",
        )

    hint = guest_name.strip()
    matched = [r for r in pool if _guest_name_matches(r.guest_name, hint)]
    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise HTTPException(
            status_code=404,
            detail="No reservation found for this phone number and name. Check spelling or try the name on the booking.",
        )
    raise HTTPException(
        status_code=409,
        detail="Multiple reservations still match this phone and name; ask for the confirmation code (HNK-…).",
    )


@router.get("/lookup-by-phone", response_model=ReservationRead)
def lookup_reservation_by_guest_phone(
    phone: str | None = Query(
        None,
        description="Guest phone from dynamic variables (any common US format). Empty `phone=` is ignored.",
    ),
    guest_phone: str | None = Query(
        None,
        description="Alias for `phone` (many Telnyx tools use this name).",
    ),
    guest_name: str | None = Query(
        None,
        min_length=1,
        max_length=255,
        description="Required when more than one active reservation shares this phone (disambiguate).",
    ),
    db: Session = Depends(get_db),
):
    """Legacy: phone-only works if there is exactly one candidate row. Prefer **`GET /api/reservations/lookup`** (phone + **required** guest_name) for voice.

    Prefers upcoming non-cancelled rows (nearest first), else most recent. If several rows share the
    phone, pass `guest_name` (spoken or on file) so we can pick the right one.
    Map Telnyx `telnyx_end_user_target` into query param `phone` or `guest_phone` from the tool.
    """
    raw_phone = ((phone or guest_phone) or "").strip()
    if len(raw_phone) < 3:
        raise HTTPException(
            status_code=422,
            detail="Send query parameter `phone` or `guest_phone` (min 3 characters).",
        )
    phone = raw_phone
    if "{{" in phone or "}}" in phone:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsubstituted template in phone query. Use a tool query/path parameter bound to the "
                "caller number (e.g. telnyx_end_user_target), not literal {{…}} in the URL."
            ),
        )
    if guest_name and ("{{" in guest_name or "}}" in guest_name):
        raise HTTPException(
            status_code=400,
            detail="Unsubstituted template in guest_name query.",
        )
    variants = phone_lookup_variants(phone.strip())
    if not variants:
        raise HTTPException(status_code=400, detail="Invalid or empty phone value.")

    now = datetime.now(UTC)
    pool = _candidate_pool_for_phone(db, variants, now)
    if not pool:
        raise HTTPException(
            status_code=404,
            detail="No reservation found for this phone number.",
        )

    if len(pool) == 1:
        return pool[0]

    # Multiple rows on this line: need a name match unless caller already unique by schedule
    hint = (guest_name or "").strip()
    if not hint:
        raise HTTPException(
            status_code=400,
            detail=(
                "Multiple reservations share this phone number. Ask which name the booking is under, "
                "then call again with query parameter guest_name (e.g. first name or full name as on the reservation)."
            ),
        )

    matched = [r for r in pool if _guest_name_matches(r.guest_name, hint)]
    if len(matched) == 1:
        return matched[0]
    if not matched:
        raise HTTPException(
            status_code=404,
            detail="No reservation on this phone matches that guest name.",
        )
    raise HTTPException(
        status_code=409,
        detail="Multiple reservations still match this phone and name; ask for the confirmation code (HNK-…).",
    )


@router.get("/by-code/{code}", response_model=ReservationRead)
def get_reservation_by_code(code: str, db: Session = Depends(get_db)):
    code = _normalize_confirmation_code(_reject_unsubstituted_path_value(code))
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


@router.patch("/by-code/{code}/status", response_model=ReservationRead)
async def update_status_by_code(
    code: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    status: str | None = Query(
        None,
        description="Optional when JSON body is empty or omits status (Telnyx query-only tools).",
    ),
    cancel: str | None = Query(
        None,
        description="If 1/true/yes/cancel, sets status to cancelled (no JSON body needed).",
    ),
):
    """Status and/or party time / pre-order / guest fields (miswired Telnyx tools often hit …/status)."""
    code = _normalize_confirmation_code(_reject_unsubstituted_path_value(code))
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    out, changed = await _patch_at_status_url(
        request, row_id=row.id, query_status=status, cancel=cancel
    )
    _set_changed_header(response, changed)
    return out


@router.patch("/by-code/{code}", response_model=ReservationRead)
def patch_reservation_by_code(
    code: str,
    body: ReservationUpdate,
    response: Response,
    db: Session = Depends(get_db),
):
    """Update party size, time, pre-order, or guest fields using confirmation code (voice-friendly)."""
    code = _normalize_confirmation_code(_reject_unsubstituted_path_value(code))
    row = db.execute(
        select(Reservation).where(Reservation.confirmation_code == code)
    ).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    _require_reservation_update_fields(body)
    if _truthy_non_status_reservation_fields(body):
        _reject_modifying_cancelled(row)
    changed = _apply_reservation_update(db, row, body)
    if changed:
        db.commit()
    else:
        db.rollback()
    db.refresh(row)
    _set_changed_header(response, changed)
    return row


async def _patch_amend_from_request(
    request: Request,
    response: Response,
    db: Session,
    *,
    path_row_id: int | None,
    query_reservation_id: int | None,
    query_amend_row_id: int | None,
) -> Reservation:
    """Shared PATCH /amend implementation; identity from path, query, body, guest lookup, or scavenging."""
    raw = await read_json_or_form_body(request)
    if not isinstance(raw, dict):
        logger.warning("PATCH /amend 422: body is not a JSON object (type=%s)", type(raw).__name__)
        raise HTTPException(
            status_code=422,
            detail="Send a JSON object or form fields with confirmation_code and fields to update.",
        )
    _code_like = frozenset(
        (
            "confirmation_code",
            "code",
            "confirmationCode",
            "hnk_code",
            "reservation_code",
            "next_reservation_code",
        )
    )
    _id_like = frozenset(("id", "reservation_id", "reservationId", "booking_id"))
    logger.info(
        "PATCH /amend: top_keys=%s has_code_key=%s has_id_key=%s",
        sorted(raw.keys())[:45],
        bool(_code_like.intersection(raw.keys())),
        bool(_id_like.intersection(raw.keys())),
    )
    if hanok_reservation_verbose_logging():
        logger.info("PATCH /amend raw summary: %s", _shallow_body_summary(raw))

    flat = _unwrap_nested_reservation_payload(dict(raw))
    qp = request.query_params
    _flat_apply_cancel_and_query_status(
        flat,
        query_status=qp.get("status"),
        cancel=qp.get("cancel"),
    )
    _flat_infer_cancel_from_voice_aliases(flat)

    code_raw = (
        flat.pop("confirmation_code", None)
        or flat.pop("code", None)
        or flat.pop("confirmationCode", None)
        or flat.pop("hnk_code", None)
        or flat.pop("reservation_code", None)
        or flat.pop("next_reservation_code", None)
    )
    rid_raw = flat.pop("reservation_id", None)
    if rid_raw is None:
        rid_raw = flat.pop("id", None)
    if rid_raw is None:
        rid_raw = flat.pop("reservationId", None)
    if rid_raw is None:
        rid_raw = flat.pop("booking_id", None)

    code_ok = code_raw is not None and str(code_raw).strip()
    rid_ok = rid_raw is not None and str(rid_raw).strip()
    if path_row_id is not None:
        rid_from_query: int | None = path_row_id
    elif query_reservation_id is not None:
        rid_from_query = query_reservation_id
    elif query_amend_row_id is not None:
        rid_from_query = query_amend_row_id
    else:
        rid_from_query = None
    amend_identity_fail_hint: str | None = None
    if not code_ok and not rid_ok and rid_from_query is not None:
        rid_raw = rid_from_query
        rid_ok = True
        if path_row_id is not None:
            logger.info("PATCH /amend: identity from path /amend/%s", path_row_id)
        else:
            logger.info("PATCH /amend: identity from query ?reservation_id/?id=%s", rid_from_query)
    elif not code_ok and not rid_ok:
        # Telnyx templates often send confirmation_code / id as JSON null at the root even
        # when guest fields or nested context still identify the row (same as prior GET /lookup).
        row_guess = _amend_resolve_row_via_guest_lookup(db, flat)
        if row_guess is not None:
            rid_raw = row_guess.id
            rid_ok = True
            logger.info("PATCH /amend: identity from guest_name+phone lookup id=%s", row_guess.id)
        else:
            ident = _flat_guest_identity_for_amend(flat)
            if ident is None:
                amend_identity_fail_hint = (
                    "Guest fallback skipped: no truthy guest_name+guest_phone in the body after unwrap "
                    "(Telnyx often sends JSON null for every scalar; only `preorder` is real). "
                    f"flat guest_name={flat.get('guest_name')!r} guest_phone={flat.get('guest_phone')!r}."
                )
            else:
                gn, gp = ident
                variants = phone_lookup_variants(gp)
                pool = (
                    _candidate_pool_for_phone(db, variants, datetime.now(UTC)) if variants else []
                )
                matched = [r for r in pool if _guest_name_matches(r.guest_name, gn)]
                amend_identity_fail_hint = (
                    f"Guest fallback: {len(pool)} active row(s) for this phone, "
                    f"{len(matched)} name-match for hint {gn!r} (need exactly one). "
                    "GET /lookup may still work if query name differs from amend body name."
                )
            sid = _scavenge_reservation_id_int(raw)
            if sid is not None:
                rid_raw = sid
                rid_ok = True
                amend_identity_fail_hint = None
                logger.info("PATCH /amend: identity from nested id scavenging id=%s", sid)
            else:
                sc = _scavenge_confirmation_code_str(raw)
                if sc:
                    code_raw = sc
                    code_ok = True
                    amend_identity_fail_hint = None
                    logger.info("PATCH /amend: identity from nested confirmation_code scavenging")
    if not code_ok and not rid_ok:
        logger.warning(
            "PATCH /amend 422: missing reservation identity (preorder not applied). "
            "query_id=%s top_keys=%s flat_keys=%s summary=%s hint=%s",
            rid_from_query,
            sorted(raw.keys()),
            sorted(flat.keys()),
            _shallow_body_summary(raw),
            amend_identity_fail_hint,
        )
        hints: list[str | dict[str, str]] = [
            {
                "fix": "Put the row id before /amend (same pattern as …/11/status)",
                "example": "PATCH /api/reservations/11/amend — Telnyx: …/{{reservation_id}}/amend (duplicate the status URL and replace `status` with `amend`).",
            },
            {
                "fix": "Or id after /amend segment",
                "example": "PATCH /api/reservations/amend/11 — Telnyx: …/amend/{{reservation_id}}.",
            },
            {
                "fix": "Or add the id as a query string on /amend",
                "example": "PATCH /api/reservations/amend?id=11 (same id as GET /lookup).",
            },
            {
                "fix": "Or patch the reservation directly (body-only preorder)",
                "example": "PATCH /api/reservations/11 with JSON {\"preorder\":[{\"menu_item_id\":\"bulgogi\",\"quantity\":1}]}",
            },
        ]
        if amend_identity_fail_hint:
            hints.append(amend_identity_fail_hint)
        raise HTTPException(
            status_code=422,
            detail={
                "error": "amend_missing_reservation_identity",
                "explanation": (
                    "Preorder and other fields are not saved because the server could not determine "
                    "which reservation row to update. The tool sent keys at the top level but values "
                    "are often JSON null; GET /lookup used different query params than what appears in this body."
                ),
                "hints": hints,
            },
        )
    logger.info(
        "PATCH /amend: resolved id_hint=%r code_hint=%s flat_keys_after_id_pop=%s",
        (str(rid_raw)[:24] + "…") if rid_raw is not None and len(str(rid_raw)) > 24 else rid_raw,
        bool(code_ok),
        sorted(flat.keys())[:35],
    )
    try:
        body = ReservationUpdate.model_validate(flat)
    except ValidationError as exc:
        logger.warning(
            "PATCH /amend 422 ReservationUpdate: flat_keys=%s errors=%s summary=%s",
            sorted(flat.keys())[:40],
            exc.errors()[:10],
            _shallow_body_summary(flat, max_keys=20),
        )
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    row: Reservation | None = None
    if code_ok:
        code = _normalize_confirmation_code(_reject_unsubstituted_path_value(str(code_raw).strip()))
        row = db.execute(select(Reservation).where(Reservation.confirmation_code == code)).scalar_one_or_none()
    else:
        try:
            rid = int(str(rid_raw).strip(), 10)
        except ValueError:
            logger.warning(
                "PATCH /amend 422: id not an integer rid_raw=%r (unsubstituted {{id}} in Telnyx?)",
                rid_raw,
            )
            raise HTTPException(
                status_code=422,
                detail="reservation_id / id must be a positive integer from the lookup response.",
            ) from None
        if rid < 1:
            logger.warning("PATCH /amend 422: id out of range rid_raw=%r", rid_raw)
            raise HTTPException(status_code=422, detail="reservation_id must be a positive integer.")
        row = db.get(Reservation, rid)
    if not row:
        logger.warning("PATCH /amend 404: no row for code_ok=%s rid=%s", code_ok, rid_raw if not code_ok else None)
        raise HTTPException(status_code=404, detail="Reservation not found")
    try:
        _require_reservation_update_fields(body)
    except HTTPException:
        logger.warning(
            "PATCH /amend 422: no patchable fields row_id=%s model_fields_set=%s effective=%s "
            "(empty preorder [] does not count; lone JSON null on preorder clears cart, not all-null Telnyx templates).",
            row.id,
            body.model_fields_set,
            _effective_reservation_patch_fields(body),
        )
        raise
    if _truthy_non_status_reservation_fields(body):
        _reject_modifying_cancelled(row)
    changed = _apply_reservation_update(db, row, body)
    if changed:
        db.commit()
    else:
        db.rollback()
    db.refresh(row)
    _set_changed_header(response, changed)
    logger.info(
        "PATCH /amend ok row_id=%s changed=%s fields=%s",
        row.id,
        changed,
        sorted(body.model_fields_set),
    )
    return row


@router.patch("/amend", response_model=ReservationRead)
async def amend_reservation_by_body_code(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    reservation_id: int | None = Query(
        None,
        ge=1,
        description="Numeric row id from GET /lookup when the JSON body only has null placeholders (Telnyx).",
    ),
    amend_row_id: int | None = Query(
        None,
        ge=1,
        alias="id",
        description="Alias for reservation_id in the query string (?id=11).",
    ),
):
    """Update a booking when tools post JSON instead of PATCH /{id}.

    Identify the row with **one of**: confirmation_code in the body, **reservation_id** / **id** in the body,
    **?reservation_id=** or **?id=** on the URL, **PATCH /{id}/amend** (same path shape as **/{id}/status**),
    **PATCH /amend/{id}**, guest_name+phone (unique match), or nested scavenging.
    """
    return await _patch_amend_from_request(
        request,
        response,
        db,
        path_row_id=None,
        query_reservation_id=reservation_id,
        query_amend_row_id=amend_row_id,
    )


@router.patch("/amend/{row_id}", response_model=ReservationRead)
async def amend_reservation_by_path_id(
    request: Request,
    response: Response,
    row_id: int = Path(
        ge=1,
        description="Same numeric id as GET /lookup and PATCH /{id}/status (use /amend/{{id}} in Telnyx).",
    ),
    db: Session = Depends(get_db),
):
    """PATCH /amend with id in the path — mirrors …/{id}/status for HTTP tools that substitute path only."""
    return await _patch_amend_from_request(
        request,
        response,
        db,
        path_row_id=row_id,
        query_reservation_id=None,
        query_amend_row_id=None,
    )


@router.patch("/{reservation_id}/status", response_model=ReservationRead)
async def update_status(
    reservation_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    status: str | None = Query(
        None,
        description="Optional when JSON body is empty or omits status (Telnyx query-only tools).",
    ),
    cancel: str | None = Query(
        None,
        description="If 1/true/yes/cancel, sets status to cancelled (no JSON body needed).",
    ),
):
    """Register before /{reservation_id}. Same as PATCH /{id} when body includes party/time/pre-order."""
    reservation_id_int = _parse_reservation_id_path(reservation_id)
    row = db.get(Reservation, reservation_id_int)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    out, changed = await _patch_at_status_url(
        request, row_id=row.id, query_status=status, cancel=cancel
    )
    _set_changed_header(response, changed)
    return out


@router.patch("/{reservation_id}/amend", response_model=ReservationRead)
async def amend_reservation_id_amend(
    reservation_id: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Same body unwrapping as PATCH /amend; id in path mirrors …/{id}/status (typical Telnyx template)."""
    reservation_id_int = _parse_reservation_id_path(reservation_id)
    return await _patch_amend_from_request(
        request,
        response,
        db,
        path_row_id=reservation_id_int,
        query_reservation_id=None,
        query_amend_row_id=None,
    )


@router.get("/{reservation_id}", response_model=ReservationRead)
def get_reservation(reservation_id: str, db: Session = Depends(get_db)):
    reservation_id_int = _parse_reservation_id_path(reservation_id)
    row = db.get(Reservation, reservation_id_int)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    return row


@router.patch("/{reservation_id}", response_model=ReservationRead)
def patch_reservation(
    reservation_id: str,
    body: ReservationUpdate,
    response: Response,
    db: Session = Depends(get_db),
):
    """Update party size, time, pre-order, or guest details (partial PATCH)."""
    reservation_id_int = _parse_reservation_id_path(reservation_id)
    row = db.get(Reservation, reservation_id_int)
    if not row:
        raise HTTPException(status_code=404, detail="Reservation not found")
    _require_reservation_update_fields(body)
    if _truthy_non_status_reservation_fields(body):
        _reject_modifying_cancelled(row)
    changed = _apply_reservation_update(db, row, body)
    if changed:
        db.commit()
    else:
        db.rollback()
    db.refresh(row)
    _set_changed_header(response, changed)
    return row
