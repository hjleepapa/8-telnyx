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
| **`HANOK_MCP_HTTP_TIMEOUT_SECONDS`** | Optional. Default **45** (seconds); capped in code. |
| **`DB_URI`** / **`DATABASE_URL`** | Postgres for reservation tools to persist data. |

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

---

## Tools

| Tool | REST |
|------|------|
| `list_menu_items` | `GET /api/reservations/menu/items` |
| `get_reservation` | `GET /api/reservations/lookup` (`guest_name`, `guest_phone`) |
| `get_reservation_by_code` | `GET /api/reservations/by-code/{code}` |
| `search_seating_availability` | `GET /api/reservations/seating/availability?date=` (needs **`HANOK_TABLE_ALLOCATION_ENABLED`** on the API) |
| `create_reservation` | `POST /api/reservations` |
| `update_reservation_details` | `PATCH /api/reservations/{id}/amend` |
| `set_reservation_status` | `PATCH /api/reservations/{id}/status` |
| `cancel_reservation` | `PATCH …/status` with `cancelled` |

Responses are JSON strings with `http_status` and `data` (or error fields) for the model.

Implementation: [`server.py`](server.py).

---

## Resource & prompt

- **Resource** `hanok://api-base` — resolved REST origin the server uses.
- **Prompt** `reservation_voice_flow` — short suggested steps for voice booking.

---

## Operations & troubleshooting

- **Render logs:** confirm `POST /mcp/` or tool traffic and **`GET/POST /api/reservations`** on the same service.
- **421 / DNS rebinding:** ensure **`HANOK_PUBLIC_BASE_URL`** matches the hostname Telnyx uses, or extend **`HANOK_MCP_ALLOWED_HOSTS`** / **`HANOK_MCP_ALLOWED_ORIGINS`**.
- **Timeouts:** increase **`HANOK_MCP_HTTP_TIMEOUT_SECONDS`** slightly if the DB is cold-starting.
- **Reservation debugging:** optional **`HANOK_RESERVATION_VERBOSE_LOG=1`** on the web service (see root README / `config.py`).

Full project overview and challenge checklist: **[`../../README.md`](../../README.md)** (repo root).
