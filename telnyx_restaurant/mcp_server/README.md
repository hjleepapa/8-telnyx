# Hanok Table MCP server (Render + Telnyx)

[Model Context Protocol](https://modelcontextprotocol.io/) tools for **Hanok Table** reservations. In production everything runs on **one Render.com web service**: **FastAPI** serves the REST API, webhooks, and static site, and **optionally mounts** this MCP app on the **same uvicorn process** so Telnyx can use **HTTP MCP** against your public URL.

## Architecture (current)

| Piece | Behavior |
|-------|-----------|
| **Process** | Single **`uvicorn telnyx_restaurant.app:app`** on Render (see root **`Procfile`**). |
| **REST + MCP** | When **`HANOK_MCP_HTTP_MOUNT=1`**, `app.py` mounts FastMCP’s **streamable HTTP** app at **`HANOK_MCP_HTTP_MOUNT_PATH`** (default **`/mcp`**). |
| **Lifespan** | FastAPI lifespan runs **`mcp.session_manager.run()`** while MCP HTTP is enabled — required so Telnyx’s requests do not hit “task group is not initialized”. |
| **Tools → API** | Tools call the reservation API with **`httpx.AsyncClient`** against **`hanok_mcp_api_base_url()`** (see `config.py`). On Render this is normally the **same public origin** as your site (set **`HANOK_PUBLIC_BASE_URL`** / optional **`HANOK_MCP_API_BASE_URL`**). |
| **Why async HTTP** | Blocking `httpx` from inside the same process would **deadlock** the worker (threads wait on responses the event loop never finishes). All tool handlers use **`await`** + **`AsyncClient`**. |

```text
Telnyx Cloud  ──HTTPS──►  Render Web Service (one dyno)
                              │
                              ├─ FastAPI  /api/reservations/…
                              ├─ FastAPI  /webhooks/telnyx/…
                              └─ Mounted MCP  /mcp/  →  same tools call same origin /api/…
```

---

## Render: environment variables

Set these on the **same** web service that runs the API:

| Variable | Purpose |
|----------|---------|
| **`HANOK_MCP_HTTP_MOUNT`** | `1`, `true`, or `yes` — enable MCP streamable HTTP mount. |
| **`HANOK_PUBLIC_BASE_URL`** | Public origin **without** trailing slash, e.g. `https://your-service.onrender.com`. Used for MCP **Host / Origin** validation (`hanok_mcp_streamable_transport_security`) and as the default REST base for tools when **`HANOK_MCP_API_BASE_URL`** is unset. |
| **`HANOK_MCP_API_BASE_URL`** | Optional. Set only if tool HTTP calls must target a **different** origin than `HANOK_PUBLIC_BASE_URL` (unusual on Render). |
| **`HANOK_MCP_HTTP_MOUNT_PATH`** | Optional. Default **`/mcp`**. Mount URL must match what you paste into Telnyx. |
| **`HANOK_MCP_ALLOWED_HOSTS`** / **`HANOK_MCP_ALLOWED_ORIGINS`** | Optional comma-separated extra hosts/origins if Telnyx or proxies use a hostname that does not match `HANOK_PUBLIC_BASE_URL`. |
| **`HANOK_MCP_DISABLE_DNS_REBINDING`** | `1` only if you accept weaker transport checks (not recommended on untrusted networks). |
| **`HANOK_MCP_HTTP_TIMEOUT_SECONDS`** | Optional. Default **45** (seconds); capped in code (5–120). |
| **`HANOK_MCP_TRANSPORT`** | For **local** `python -m telnyx_restaurant.mcp_server.server` only: `stdio` (default), `sse`, or `streamable-http`. Render mounted MCP ignores this; the FastAPI app chooses streamable HTTP. |
| **`DB_URI`** / **`DATABASE_URL`** | Postgres so the **API** persists reservations (tools hit HTTP; the API needs the DB). |
| **`HANOK_TABLE_ALLOCATION_ENABLED`** | API feature flag. When off, **`search_seating_availability`** returns **404** with an explanation; create/update responses may still include **`seating_status`** depending on product mode. |

Redeploy after changing env vars.

---

## Telnyx Mission Control — HTTP MCP server

Telnyx registers a **remote MCP endpoint** (Type **HTTP**) — it does **not** run Python on your laptop.

1. Complete Render env + deploy (above).
2. In **Create MCP Server** (or equivalent):
   - **Name:** e.g. `hanok_table_mcp`
   - **Type:** **HTTP**
   - **URL:** `https://<your-render-host>/mcp/` — use your real host; **trailing slash** avoids some **307** redirect quirks with HTTP clients.
   - **API Key:** only if your Telnyx project or an extra auth layer requires it (usually empty for this app).
3. Attach the MCP server to your **AI Assistant** and publish.

`HANOK_MCP_API_BASE_URL` and `HANOK_PUBLIC_BASE_URL` are read by your **Render** Python process so each tool request resolves to your live **`/api/reservations/...`** routes.

**Assistant instructions:** Do not duplicate these REST operations with separate **HTTP Action** tools (same paths as MCP) — that risks double bookings and conflicting updates. Use **one** canonical instruction block and **MCP-only** reservation tools; see root **[`README.md`](../../README.md)** → *Canonical Telnyx Assistant instructions*.

---

## Telnyx dynamic variables (for the model + MCP)

When **`POST /webhooks/telnyx/variables`** is wired, map JSON fields to instruction placeholders. The MCP server’s built-in **`instructions`** and **`reservation_voice_flow`** prompt expect you to use them consistently:

| Variable (example) | Purpose with MCP |
|---------------------|-------------------|
| **`{{locale_hint}}`** / preferred locale fields | Spoken language (**en-US** vs **ko-KR**); aligns with server guidance and optional **`preferred_locale`** on create/update. |
| **`{{caller_phone_normalized}}`** | **E.164** line id — pass as **`guest_phone`** on **`get_reservation`**, **`create_reservation`**, etc., when appropriate. |
| **`{{caller_line_single_booking}}`** | If **`yes`**, call **`get_reservation`** with **only** `guest_phone` (omit `guest_name`) for phone-only lookup. Greet with **`{{guest_personalized_greeting_suggestion}}`** first; do not demand name before lookup. |
| **`{{caller_line_has_multiple_bookings}}`** | If **`yes`**, ask which name, then **`get_reservation(guest_phone, guest_name=…)`**. |
| **`{{guest_lookup_identification_hint}}`** | Narrative hint when disambiguation is needed. |
| **`{{guest_lookup_name_for_tools}}`** | On-file **`guest_name`** for tools when you already know it (optional). |
| Seating / waitlist (`{{reservation_seating_status}}`, **`{{guest_waitlist_*}}`**, etc.) | Drive spoken truth: **waitlist** ≠ table confirmed; use hints for EWT, caps, VIP fairness. Full list: root README *Useful keys*. |

Always read **`reservation_id`** and **`confirmation_code`** from the JSON **`data`** object after **`get_reservation`** or **`create_reservation`**; never pass template placeholders as IDs.

---

## Tools (summary → REST)

Every tool returns a **JSON string** for the model, usually shaped as **`{ "http_status": <int>, "data": <object> }`**, or **`error`** / **`detail`** on client failures. Parse **`data`** for ids, codes, **`seating_status`**, preorder lines, etc.

| Tool | Maps to | When to use |
|------|---------|-------------|
| **`list_menu_items`** | `GET /api/reservations/menu/items` | Before building or changing a pre-order (valid **`menu_item_id`** / aliases). |
| **`get_reservation`** | `GET /lookup-by-phone` *or* `GET /lookup` | **Required** `guest_phone`. **Optional** `guest_name`: omitted → phone-only lookup; set → name+phone lookup after disambiguation. **Before** amend, status change, or cancel when you need numeric **`id`**. |
| **`get_reservation_by_code`** | `GET /api/reservations/by-code/{code}` | When the caller gives **HNK-…**; normalizes `HNK-` prefix. |
| **`search_seating_availability`** | `GET /api/reservations/seating/availability?date=` | **`date`**: **`YYYY-MM-DD`** (UTC calendar day). Needs **`HANOK_TABLE_ALLOCATION_ENABLED`**. If unavailable (**404**), skip or proceed without promising allocation. |
| **`create_reservation`** | `POST /api/reservations` | New booking: **`guest_name`**, **`guest_phone`**, **`party_size`**, **`starts_at`** (ISO-8601; if no offset, API uses restaurant local TZ, default **America/Los_Angeles**). Optional **`preorder_lines_json`** or voice-friendly **`preorder_items`** (e.g. `bulgogi:2,kimchi_jjigae:1` or `2x bulgogi` — JSON wins if both set). **`special_requests`**, **`preferred_locale`** (`en`/`ko`), **`source_channel`** (default **`voice`**). |
| **`update_reservation_details`** | `PATCH /api/reservations/{id}/amend` | Patch only fields you change: **`party_size`**, **`starts_at`**, preorder (**`preorder_lines_json`** / **`preorder_items`**), **`special_requests`**, **`guest_name`**, **`guest_phone`**, **`preferred_locale`**, **`guest_priority`** (`normal` / `vip` for waitlist ordering when allocation is on). **`reservation_id`** must be the real integer from lookup. |
| **`set_reservation_status`** | `PATCH /api/reservations/{id}/status` | Lifecycle only: **`pending`**, **`confirmed`**, **`seated`**, **`completed`**, **`cancelled`** (also accepts *cancel* / *canceled* → **cancelled**). |
| **`cancel_reservation`** | Same status route with **`cancelled`** | Prefer when intent is **cancel only**. |

**Pre-order:** Use **`list_menu_items`** first. On create/update, either structured JSON array or **`preorder_items`** shorthand; invalid JSON returns a tool-level error string without calling the API.

**IDs:** **`reservation_id`** arguments are **integers** from the latest successful **`get_reservation`** / **`create_reservation`** / **`get_reservation_by_code`** response — never a literal `{{reservation_id}}` template.

Implementation: [`server.py`](server.py).

---

## Resource & prompt

| Name | Purpose |
|------|---------|
| **Resource** `hanok://api-base` | Returns JSON with the configured REST origin the MCP process uses (`hanok_mcp_api_base_url`). |
| **Prompt** `reservation_voice_flow` | Short turn order: greet → phone-only vs multi-booking lookup → **`list_menu_items`** → create/update/cancel → confirm code and time aloud. |

The FastMCP **`instructions`** string (in code) mirrors webhook variables, lookup rules, PATCH vs status routes, and spoken confirmation after cancel.

---

## Operations & troubleshooting

- **Render logs:** confirm `POST /mcp/` or tool traffic and **`GET/POST /api/reservations`** on the same service.
- **421 / DNS rebinding:** ensure **`HANOK_PUBLIC_BASE_URL`** matches the hostname Telnyx uses, or extend **`HANOK_MCP_ALLOWED_HOSTS`** / **`HANOK_MCP_ALLOWED_ORIGINS`**.
- **Timeouts:** increase **`HANOK_MCP_HTTP_TIMEOUT_SECONDS`** slightly if the DB is cold-starting.
- **Reservation / seating debugging:** optional **`HANOK_RESERVATION_VERBOSE_LOG=1`** on the web service (see root README / `config.py`).
- **Same-process deadlock:** tools must stay **async** to the API; do not swap in blocking HTTP from MCP handlers.

Full project overview, webhook schema, waitlist wording, and **canonical Telnyx assistant paste**: **[`../../README.md`](../../README.md)** (repo root).
