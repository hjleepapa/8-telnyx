# Telnyx Voice AI — Hanok Table (Restaurant Reservations)

> **Hanok Table** is a **Korean-inspired demo restaurant** you can book by **phone** through a **Telnyx AI Assistant**. This repo is one deployable backend: **FastAPI**, **PostgreSQL**, **custom MCP tools**, **dynamic webhook variables**, and **Call Control** outbound reminders—with optional **table allocation and waitlist** logic.

[![Telnyx](https://img.shields.io/badge/Telnyx-Voice%20AI-00D4AA.svg)](https://developers.telnyx.com/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-000000.svg)](https://modelcontextprotocol.io/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Render](https://img.shields.io/badge/Deploy-Render-46E3B7.svg)](https://render.com/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

**Repository:** [github.com/hjleepapa/8-telnyx](https://github.com/hjleepapa/8-telnyx)

---

## Telnyx challenge — core requirements

This project is structured around the four required pillars below. Each maps directly to what you configure in **Telnyx Mission Control** and what runs on **Render**.

### 1. AI assistant (required)

| Requirement | How this project satisfies it |
|-------------|-------------------------------|
| Build an assistant in the **Telnyx Portal** (Assistant Builder) | Configure your assistant against **Hanok Table**: book by phone, look up reservations, change party size or time, pre-order menu items, cancel, and (when enabled) understand **waitlisted vs table-assigned** bookings. |
| **Compelling use case** | Full **voice reservation flow** with **menu-backed pre-orders**, optional **table inventory / waitlist**, **VIP waitlist priority** by explicit flag or large pre-order total, and **outbound reminder calls** when a table is confirmed or when a waitlisted guest is promoted. |
| **Callable via phone number** | Point a **Telnyx number** at your assistant; the assistant uses MCP (and/or HTTP tools) against your deployed API. |
| **Real conversational interactions** | Tools support natural aliases (phones, names, confirmation codes, `HNK-…` codes); voice **dedup** reduces double-booking from repeated tool calls. |

**What you configure in Telnyx:** assistant instructions should tell the model to read **tool JSON** after creates (especially `seating_status`: **allocated** vs **waitlist**) and to use **dynamic variables** (below) when the portal merges them into prompts.

---

### 2. MCP server integration (required)

| Requirement | How this project satisfies it |
|-------------|-------------------------------|
| **Custom MCP server** the assistant can use | [`telnyx_restaurant/mcp_server/server.py`](telnyx_restaurant/mcp_server/server.py) implements a **FastMCP** server with tools backed by your **same** reservation REST API. |
| **Meaningful tools / resources** | **Menu** lookup, **create** reservation (with `preorder_items` / lines), **lookup** by name+phone, **fetch by code**, **amend** (time, party, pre-order, notes, contact), **status / cancel**, optional **seating availability** by date. |
| **Enhances the assistant** | The assistant does not need hard-coded menu prices or ad-hoc HTTP shaping for every tool—MCP exposes structured operations over **httpx** to `POST /api/reservations`, `GET /lookup`, `PATCH …/amend`, etc. |

**Deploy URL (HTTP transport):** on **Render**, set **`HANOK_MCP_HTTP_MOUNT=1`** and point Telnyx at **`https://<your-render-host>/mcp/`** (trailing slash recommended). Steps: [`telnyx_restaurant/mcp_server/README.md`](telnyx_restaurant/mcp_server/README.md).

---

### 3. Dynamic webhook variables (required)

| Requirement | How this project satisfies it |
|-------------|-------------------------------|
| **Implement dynamic webhook variables** | **`POST /webhooks/telnyx/variables`** returns JSON keyed for instruction templates (map keys in Telnyx to these fields). |
| **Personalize / fetch context** | Caller ANI is matched to **`guest_phone`** (normalized variants). Response includes guest name, **upcoming reservation** metadata, pre-order summary and **food totals**, **seating / waitlist** fields when table allocation is on, and **concierge** hints for high-value pre-orders. |
| **Show how data improves the flow** | Example: if `{{guest_is_high_value_preorder}}` is **yes**, instructions can use `{{concierge_service_hint}}` and `{{cancel_retention_offer}}` on cancel intent; if `{{reservation_seating_status}}` is **waitlist**, the assistant should **not** say a table is confirmed until **allocated**. |
| **Deployed API** | Variables resolve against **PostgreSQL**; set **`DB_URI`** on Render. Without a DB, behavior is limited (demo ANI suffixes still return synthetic profiles in code). |

**Webhook URL:** `POST https://<your-render-host>/webhooks/telnyx/variables`

**Useful keys (non-exhaustive):** map JSON fields to Telnyx instruction variables and reference them as `{{guest_display_name}}`, `{{next_reservation_code}}`, `{{next_reservation_at}}`, `{{reservation_preorder_summary}}`, `{{reservation_food_total_cents}}`, `{{guest_is_high_value_preorder}}`, `{{concierge_service_hint}}`, `{{cancel_retention_offer}}`, `{{reservation_seating_status}}`, `{{guest_waitlist_priority}}`, `{{waitlist_fairness_hint}}`, `{{guest_waitlist_position}}`, `{{guest_waitlist_queue_size}}`, `{{guest_waitlist_estimated_wait_minutes}}`, `{{guest_waitlist_position_ordinal_en}}`, `{{guest_waitlist_wait_time_hint}}`, `{{guest_waitlist_tables_required}}`, `{{guest_waitlist_can_seat_after_ahead}}` (`yes` / `no`), `{{guest_waitlist_ahead_queue_feasible}}` (`yes` / `no`), `{{guest_waitlist_seating_capacity_hint}}`, `{{guest_waitlist_max_parties_per_slot}}`, `{{guest_waitlist_alternate_time_hint}}` (plus `{{preferred_locale}}` / `{{locale_hint}}` — see **Future improvements** below). Estimated wait is **position × `HANOK_WAITLIST_MINUTES_PER_POSITION`** (default **15** minutes). **`guest_waitlist_can_seat_after_ahead`** is **`no`** when, after simulating parties ahead in queue order, **current table counts** may not fit this party (e.g. **large party needing two or more tables** while only one compatible table may remain). **`guest_waitlist_tables_required`** is how many tables the plan uses for this party at full template capacity (speech only). At most **`HANOK_WAITLIST_MAX_PER_SLOT`** parties (default **5**) can join the waitlist for the same slot; further bookings return **409**.

**Telnyx assistant — waitlist (paste into instructions; variables use `{{Name}}` to match your dynamic variable mapping):**

```text
Waitlist position and wait time (from dynamic variables)
- After the webhook runs, use the waitlist fields only when they make sense:
  - If {{reservation_seating_status}} is exactly "waitlist", and {{guest_waitlist_position}} is a positive integer string (not "0", not "n/a"):
    • Tell the caller they are {{guest_waitlist_position_ordinal_en}} in line when that ordinal is non-empty; otherwise say they are "number" {{guest_waitlist_position}} in line.
    • Mention that {{guest_waitlist_queue_size}} parties are on the waitlist for that seating window.
    • Say the estimated wait is about {{guest_waitlist_estimated_wait_minutes}} minutes (not exact—tables depend on other guests finishing). It should rise by about 15 minutes per position (first ≈ 15, second ≈ 30, …) when the default is in effect.
    • You may use {{guest_waitlist_wait_time_hint}} verbatim or paraphrase it naturally.
    • {{guest_waitlist_max_parties_per_slot}} is the cap per seating window; if create fails with a full-waitlist message, use {{guest_waitlist_alternate_time_hint}} and offer a slot about two hours before or after.
    • If {{guest_waitlist_can_seat_after_ahead}} is "no": do **not** promise a table at this time. Explain tactfully that **enough tables may not be available together** once earlier waitlist parties are seated (large groups often need **more than one** table — see {{guest_waitlist_tables_required}}). Follow {{guest_waitlist_seating_capacity_hint}}; suggest **about two hours earlier or later** (or use search_seating_availability / alternate times if your tools expose them). If {{guest_waitlist_ahead_queue_feasible}} is "no", the wait is especially uncertain because the queue ahead is tight against **current** inventory.
  - If {{reservation_seating_status}} is "allocated", say they have a table assigned; do not give a waitlist position or waitlist ETA from these fields.
  - If {{guest_waitlist_position}} is "0" or {{reservation_seating_status}} is "not_applicable", do not describe a waitlist position or wait time from these variables.
  - If {{guest_waitlist_position}} is "n/a", table waitlists are not in use on this deployment; do not invent queue numbers or ETAs.
- If {{guest_waitlist_priority}} is "vip" or {{waitlist_fairness_hint}} explains VIP / large pre-order priority, use that when they ask why someone might be ahead in line.

General: never contradict {{reservation_seating_status}}. If they are waitlisted, do not say a table is confirmed until status becomes allocated.
```

**Related:** **`POST /webhooks/telnyx/call-control`** handles **Call Control** for **outbound reminder** TTS (`client_state` + optional DB fallback).

**Troubleshooting table allocation:** Capacity is stored per `(slot_start, table_size)` in the `table_slot_inventory` table. If you change **`HANOK_TABLE_INVENTORY_JSON`** or see **only the first reservation get a table** while the rest waitlist, older rows may still hold **`available_count = 0`** for one of the time buckets touched by your stay length (the allocator uses the **minimum** count across all buckets). Clear inventory and let the app recreate rows on the next bookings:

- **Render shell / Postgres:** `DELETE FROM table_slot_inventory;` or run **`python scripts/reset_table_inventory.py`** (requires **`DB_URI`** / **`DATABASE_URL`**).
- Turn on **`HANOK_RESERVATION_VERBOSE_LOG=1`** temporarily; failed allocations log **`eff`** / **`maps`** from `try_allocate_and_consume`.

---

### 4. Public deployment (required)

| Requirement | How this project satisfies it |
|-------------|-------------------------------|
| **Deploy publicly** | **Render.com** web service + **PostgreSQL** (see checklist below). The app entrypoint is **`uvicorn telnyx_restaurant.app:app`** (root **`Procfile`**). |
| **Working URLs / numbers for reviewers** | **You** publish your live **`https://…`** origin and the **Telnyx phone number** attached to the assistant. This README documents paths; it does not hard-code a challenge-specific number. |
| **Clear documentation** | **This file** + [`telnyx_restaurant/mcp_server/README.md`](telnyx_restaurant/mcp_server/README.md) + [`telnyx_restaurant/.env.example`](telnyx_restaurant/.env.example). |

**Minimum public checklist**

1. **Web:** `https://<host>/health` returns **200**.
2. **DB:** `DB_URI` / `DATABASE_URL` set; migrations applied via app startup / `db.py` guards.
3. **Optional MCP:** `HANOK_MCP_HTTP_MOUNT=1`, **`HANOK_PUBLIC_BASE_URL=https://<host>`** (helps Telnyx HTTP client and reminder `webhook_url` override).
4. **Telnyx:** Assistant **MCP** → `https://<host>/mcp/`; **Dynamic variables** + **Call Control** → `…/variables` and `…/call-control`.
5. **Outbound reminders (demo):** `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID`, `TELNYX_FROM_NUMBER`.

**Render (example):**

- **Start:** `uvicorn telnyx_restaurant.app:app --host 0.0.0.0 --port $PORT` (or use root **`Procfile`**).
- If `/` 404s but `/health` works, confirm **`static/index.html`** ships and **Root Directory** is repo root.

---

## Architecture (high level)

```mermaid
flowchart TB
  subgraph telnyx [Telnyx Mission Control]
    Assistant[AI Assistant]
    CC[Call Control App]
    Assistant --> CC
  end

  subgraph deploy [Single FastAPI service]
    App[app.py + routers]
    DB[(PostgreSQL)]
    MCPApp[Streamable MCP /mcp]
    App --> DB
    App --> MCPApp
  end

  Assistant -->|Dynamic variables JSON| Vars[POST /webhooks/telnyx/variables]
  Vars --> App
  CC -->|call.answered etc.| App
  Assistant -->|MCP tools| MCPApp
  MCPApp -->|httpx same origin| App
```

| Layer | Role |
|-------|------|
| **`routers/reservations.py`** | `/api/reservations` — create, list, lookup, PATCH **amend**, status/cancel, voice create dedup, **naive `starts_at` → wall-clock TZ → UTC**, **re-seat on amend** when table allocation is enabled. |
| **`routers/webhook.py`** | Dynamic **variables** + **call-control** for reminders. |
| **`routers/admin.py`** | **`GET /admin/reservations`** — calendar (day / week / month; view mode persisted in **localStorage**). |
| **`seating_service.py`** | Optional inventory, **waitlist promotion** (VIP by flag or pre-order total), **reminder** when a waitlisted guest gets a table. |
| **`mcp_server/server.py`** | MCP tools → same REST API. |

**Time semantics:** `starts_at` is stored in **UTC**. Values **with** an explicit offset keep their meaning. **Naive** ISO strings are interpreted as **restaurant wall time** (`HANOK_RESERVATION_WALL_TIMEZONE`, default aligned with `HANOK_ADMIN_DISPLAY_TIMEZONE`, typically `America/Los_Angeles`).

**Data:** Synthetic demo / reviewer use — not production PII.

---

## REST API (prefix `/api/reservations`)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/menu/items` | Menu for tools and web. |
| GET | `/seating/availability` | If allocation enabled: `?date=YYYY-MM-DD`. |
| GET | `` | List reservations (JSON). |
| POST | `` | Create (`waitlist_if_full`, `guest_priority`, preorder, `source_channel`, etc.). |
| GET | `/lookup` | **`guest_name` + phone** (primary). |
| GET | `/by-code/{code}` | By **HNK-…** |
| PATCH | `/amend`, `/{id}/amend`, `/by-code/...` | Partial updates; **`X-Hanok-Reservation-Changed`** header reflects real DB writes. |

Full route table and PATCH semantics are in code comments and earlier sections; see also **OpenAPI** at **`/docs`** on your Render URL.

---

## Static site & admin

| Route | Description |
|-------|-------------|
| `/` | Landing + widget hookup points. |
| `/reserve-online.html` | Web booking + pre-order. |
| `/reservation/status` | Guest lookup by confirmation code. |
| `/admin/reservations` | Staff calendar (**`?token=`** if `ADMIN_DASHBOARD_TOKEN`). |
| `/reservation-lab` | Optional API lab (`HANOK_RESERVATION_LAB=1`). |
| `/health` | Liveness. |

---

## Environment variables

See **[`telnyx_restaurant/.env.example`](telnyx_restaurant/.env.example)** for the full list. Highlights:

| Variable | Role |
|----------|------|
| **`DB_URI`** / **`DATABASE_URL`** | Postgres (Render-friendly SSL hinting in code). |
| **`HANOK_PUBLIC_BASE_URL`** | Public origin (reminder `webhook_url`, MCP). |
| **`HANOK_MCP_HTTP_MOUNT`**, path / DNS rebinding | MCP on same process. |
| **`TELNYX_*`** | Outbound reminders + Call Control. |
| **`HANOK_TABLE_ALLOCATION_ENABLED`**, **`HANOK_TABLE_INVENTORY_JSON`**, **`HANOK_VIP_PREORDER_CENTS`**, **`HANOK_PREMIUM_PREORDER_CENTS`** | Seating + waitlist + premium / VIP tiers. |

---

## Repository structure

```
8.telnyx/
├── README.md
├── Procfile
├── requirements.txt
└── telnyx_restaurant/
    ├── app.py
    ├── routers/{admin,reservations,webhook}.py
    ├── mcp_server/{server.py,README.md}
    ├── static/, templates/
    └── tests/
```

**API reference on Render:** `https://<your-render-host>/docs` (OpenAPI).

---

## Security & license

- Demo data only; do not commit real **`.env`** secrets.
- **MIT** — [LICENSE](LICENSE).

---

## Future improvements: locale & Korean (`locale_hint`)

The API and web UI already support **`preferred_locale`** (`en` / `ko`) on reservations. Dynamic variables can expose **`locale_hint`** (`en-US` / `ko-KR`) so instructions can say “speak Korean when `locale_hint` is ko-KR.”

**Current limitation:** In testing, **Korean speech-to-text (STT) quality in Telnyx** has been **unreliable** compared to English. Until STT for Korean improves, this README treats **Korean voice** as a **future integration path** rather than the primary demo story. The **English** assistant + **same backend** (MCP, variables, waitlist, reminders) remains the center of the challenge deliverable. You can still collect `preferred_locale=ko` from **web** bookings and use it for **dynamic variables** or post-call flows.

---

**Deploy details & MCP copy-paste:** [`telnyx_restaurant/mcp_server/README.md`](telnyx_restaurant/mcp_server/README.md)
