#!/usr/bin/env python3
"""Create demo reservations for table-allocation / waitlist walkthroughs via POST /api/reservations.

Requires a running Hanok API (e.g. uvicorn). Table allocation must be on server-side:

  HANOK_TABLE_ALLOCATION_ENABLED=1

Scenario **vip-queue** expects exactly one allocatable 6-top per slot (matches project tests):

  HANOK_TABLE_INVENTORY_JSON='{"6":1}'
  HANOK_TABLE_SLOT_MINUTES=60
  HANOK_RESERVATION_DURATION_MINUTES=60

Scenario **party-skip** expects one 8-top and one 4-top per slot:

  HANOK_TABLE_INVENTORY_JSON='{"8":1,"4":1}'
  (same slot/duration settings as above work)

Examples::

  python scripts/seed_waitlist_demo.py --base-url http://127.0.0.1:8000 \\
    vip-queue --starts-at 2026-07-20T02:00:00Z

  python scripts/seed_waitlist_demo.py --base-url http://127.0.0.1:8000 \\
    cancel-code --code HNK-XXXX

  python scripts/seed_waitlist_demo.py party-skip --starts-at 2026-07-20T02:00:00Z
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx


def _default_starts_at_iso() -> str:
    t = datetime.now(UTC) + timedelta(days=1)
    t = t.replace(hour=18, minute=0, second=0, microsecond=0)
    return t.isoformat().replace("+00:00", "Z")


def _post_reservation(client: httpx.Client, base: str, payload: dict[str, Any]) -> dict[str, Any]:
    url = f"{base}/api/reservations"
    r = client.post(url, json=payload)
    if r.status_code >= 400:
        raise SystemExit(f"POST {url} -> {r.status_code}\n{r.text}")
    return r.json()


def _print_row(label: str, data: dict[str, Any]) -> None:
    tabs = data.get("tables_allocated")
    tabs_s = ",".join(str(x) for x in tabs) if tabs else "—"
    print(
        f"{label:16} id={data['id']} code={data['confirmation_code']} "
        f"party={data['party_size']} seating={data.get('seating_status')} "
        f"priority={data.get('guest_priority')} tables=[{tabs_s}]"
    )


def cmd_vip_queue(client: httpx.Client, base: str, starts_at: str, duration: int | None) -> int:
    common: dict[str, Any] = {
        "starts_at": starts_at,
        "source_channel": "api",
        "waitlist_if_full": True,
        "party_size": 6,
    }
    if duration is not None:
        common["duration_minutes"] = duration

    hold = _post_reservation(
        client,
        base,
        {
            **common,
            "guest_name": "Demo Hold Sixtop",
            "guest_phone": "+15550106001",
            "guest_priority": "normal",
        },
    )
    _print_row("hold", hold)

    norm = _post_reservation(
        client,
        base,
        {
            **common,
            "guest_name": "Demo Waitlist Norm",
            "guest_phone": "+15550106002",
            "guest_priority": "normal",
        },
    )
    _print_row("waitlist_norm", norm)

    vip = _post_reservation(
        client,
        base,
        {
            **common,
            "guest_name": "Demo Waitlist VIP",
            "guest_phone": "+15550106003",
            "guest_priority": "vip",
        },
    )
    _print_row("waitlist_vip", vip)

    print("\nNext: cancel the hold so VIP promotes before norm, e.g.:")
    print(
        f"  python scripts/seed_waitlist_demo.py --base-url {base} "
        f"cancel-code --code {hold['confirmation_code']}"
    )
    return 0


def cmd_party_skip(client: httpx.Client, base: str, starts_at: str, duration: int | None) -> int:
    common: dict[str, Any] = {
        "starts_at": starts_at,
        "source_channel": "api",
        "waitlist_if_full": True,
    }
    if duration is not None:
        common["duration_minutes"] = duration

    a8 = _post_reservation(
        client,
        base,
        {
            **common,
            "guest_name": "Demo Seated Eight",
            "guest_phone": "+15550108001",
            "party_size": 8,
            "guest_priority": "normal",
        },
    )
    _print_row("seated_8", a8)

    b4 = _post_reservation(
        client,
        base,
        {
            **common,
            "guest_name": "Demo Seated Four",
            "guest_phone": "+15550108002",
            "party_size": 4,
            "guest_priority": "normal",
        },
    )
    _print_row("seated_4", b4)

    w8 = _post_reservation(
        client,
        base,
        {
            **common,
            "guest_name": "Demo Wait Eight",
            "guest_phone": "+15550108003",
            "party_size": 8,
            "guest_priority": "normal",
        },
    )
    _print_row("wait_8", w8)

    w4 = _post_reservation(
        client,
        base,
        {
            **common,
            "guest_name": "Demo Wait Four",
            "guest_phone": "+15550108004",
            "party_size": 4,
            "guest_priority": "normal",
        },
    )
    _print_row("wait_4", w4)

    print("\nNext: cancel the party-of-4 seated booking; wait_4 should allocate before wait_8:")
    print(
        f"  python scripts/seed_waitlist_demo.py --base-url {base} "
        f"cancel-code --code {b4['confirmation_code']}"
    )
    return 0


def cmd_cancel_code(client: httpx.Client, base: str, code: str) -> int:
    url = f"{base}/api/reservations/by-code/{code}/status"
    r = client.patch(url, params={"cancel": "1"})
    if r.status_code >= 400:
        raise SystemExit(f"PATCH {url} -> {r.status_code}\n{r.text}")
    data = r.json()
    _print_row("cancelled", data)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="API origin (no trailing slash)",
    )
    p.add_argument(
        "--duration-minutes",
        type=int,
        default=None,
        help="Reservation length (omit to use server default, often 120)",
    )
    sub = p.add_subparsers(dest="command", required=True)

    p_vip = sub.add_parser("vip-queue", help="Hold + normal waitlist + VIP waitlist (needs one 6-top / slot)")
    p_vip.add_argument(
        "--starts-at",
        default=None,
        help=f"ISO start time (default: tomorrow 18:00 UTC as Z), e.g. 2026-07-20T02:00:00Z",
    )

    p_skip = sub.add_parser(
        "party-skip",
        help="Fill 8+4 then two waitlist rows; cancel the 4-top guest to promote smaller party first",
    )
    p_skip.add_argument("--starts-at", default=None, help="ISO start time (default: tomorrow 18:00 UTC)")

    p_can = sub.add_parser("cancel-code", help="PATCH reservation to cancelled by HNK-… code")
    p_can.add_argument("--code", required=True, help="confirmation_code e.g. HNK-ABC1")

    args = p.parse_args(argv)
    base = args.base_url.rstrip("/")
    starts = getattr(args, "starts_at", None) or _default_starts_at_iso()
    dur: int | None = args.duration_minutes

    with httpx.Client(timeout=30.0) as client:
        if args.command == "vip-queue":
            return cmd_vip_queue(client, base, starts, dur)
        if args.command == "party-skip":
            return cmd_party_skip(client, base, starts, dur)
        if args.command == "cancel-code":
            return cmd_cancel_code(client, base, args.code)
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        raise SystemExit(130) from None
