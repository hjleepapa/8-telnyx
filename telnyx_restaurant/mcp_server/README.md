# Hanok Table MCP server

This is a **real** [Model Context Protocol](https://modelcontextprotocol.io/) server that exposes the same behavior as the Hanok **REST API** via **tools** and a small **resource** / **prompt**. Telnyx Voice AI (or Claude Desktop, Cursor, etc.) can attach this process as an MCP server while your **FastAPI** app stays deployed on Render.

## Requirements

- Python **3.11+** (match the main app).

When MCP **streamable HTTP** is mounted **in the same uvicorn process** as the API, tools must use **non-blocking** HTTP (`httpx.AsyncClient` + `await`). Blocking `httpx` in **`asyncio.to_thread`** can **deadlock**: all default pool threads wait on HTTP responses from this same process while the event loop cannot dispatch those routes (~60s timeouts).
- Install deps from repo root: `pip install -r telnyx_restaurant/requirements.txt`
- The MCP process must reach your API over HTTPS or HTTP (`HANOK_MCP_API_BASE_URL` or `HANOK_PUBLIC_BASE_URL`).

## Run locally (stdio — default)

From the **repository root** (so `telnyx_restaurant` is importable):

```bash
export HANOK_MCP_API_BASE_URL=https://telnyx.convonetai.com   # or http://127.0.0.1:8000 if API runs locally
PYTHONPATH=. python -m telnyx_restaurant.mcp_server
```

Stdio is what many MCP **clients** expect (they spawn the process and talk over stdin/stdout).

### Optional: streamable HTTP (debug / remote clients)

```bash
export HANOK_MCP_TRANSPORT=streamable-http
PYTHONPATH=. python -m telnyx_restaurant.mcp_server
```

FastMCP defaults to `host=127.0.0.1`, `port=8000` unless you configure via env / SDK; check `mcp` logs for the exact path (often `/mcp`).

## Tools

| Tool | REST behavior |
|------|----------------|
| `list_menu_items` | `GET /api/reservations/menu/items` |
| `get_reservation` | `GET /api/reservations/lookup` (guest_name + guest_phone) |
| `get_reservation_by_code` | `GET /api/reservations/by-code/{code}` |
| `search_seating_availability` | `GET /api/reservations/seating/availability?date=` (if allocation enabled) |
| `create_reservation` | `POST /api/reservations` |
| `update_reservation_details` | `PATCH /api/reservations/{id}/amend` |
| `set_reservation_status` | `PATCH /api/reservations/{id}/status` |
| `cancel_reservation` | Same as status → `cancelled` |

Tool responses are JSON strings with `http_status` and `data` (or error hints) so the LLM can read errors.

## Resource & prompt

- `hanok://api-base` — shows the resolved API origin.
- Prompt `reservation_voice_flow` — short suggested flow for voice.

---

## Telnyx Mission Control — “Create MCP Server” (HTTP + required URL)

Some Telnyx UIs only offer **Type: HTTP**, **Name**, **URL** (required), and **API Key**. That screen is **not** where you set `HANOK_MCP_API_BASE_URL`: there are **no per-server Python env vars** there. It registers a **remote MCP endpoint** Telnyx’s cloud will call.

Do this instead:

1. On **Render** (same web service as `uvicorn`), set:
   - `HANOK_MCP_HTTP_MOUNT=1`
   - **`HANOK_PUBLIC_BASE_URL=https://your-host`** (required for MCP **Host** / **Origin** validation — e.g. `https://telnyx.convonetai.com`; see `hanok_mcp_streamable_transport_security()` in `config.py`). Optionally add **`HANOK_MCP_ALLOWED_HOSTS`** / **`HANOK_MCP_ALLOWED_ORIGINS`** if clients use another hostname, or **`HANOK_MCP_DISABLE_DNS_REBINDING=1`** only if you accept the risk.
   - `HANOK_MCP_API_BASE_URL` if the REST origin should differ from the public URL.
   - Optional: `HANOK_MCP_HTTP_MOUNT_PATH=/mcp` (default is `/mcp`)
2. Redeploy. FastAPI mounts the MCP **streamable HTTP** app at that path. (The app lifespan runs `session_manager.run()` so Telnyx’s POSTs do not hit “Task group is not initialized”.)
3. In Telnyx **Create MCP Server**:
   - **Name:** e.g. `hanok_table_mcp`
   - **Type:** HTTP
   - **URL:** `https://your-host/mcp` (use your real host; path must match `HANOK_MCP_HTTP_MOUNT_PATH`)
   - **API Key:** leave empty unless Telnyx docs or your own auth layer require it

`HANOK_MCP_API_BASE_URL` / `HANOK_PUBLIC_BASE_URL` are read by **your deployed Python process** on Render so MCP **tools** can HTTP-call the REST API (usually the same public origin).

---

## Manual steps you must do (checklist)

### 1. Environment

| Variable | Purpose |
|----------|---------|
| `HANOK_MCP_API_BASE_URL` | **Recommended:** full origin of the API (e.g. `https://telnyx.convonetai.com`). Use this when MCP runs on your laptop and the API is on Render. |
| `HANOK_PUBLIC_BASE_URL` | Used **if** `HANOK_MCP_API_BASE_URL` is unset — same origin as webhooks. |
| (neither set) | Falls back to `http://127.0.0.1:8000` — only for API + MCP on same machine. |
| `HANOK_MCP_TRANSPORT` | `stdio` (default), `sse`, or `streamable-http`. |

### 2. Telnyx Mission Control — connect MCP to the assistant

Exact UI labels change over time; conceptually:

1. Open your **AI Assistant**.
2. Find **MCP**, **Integrations**, or **Model Context Protocol** server configuration.
3. Add a server that runs your command, for example:
   - **Command:** `python3` or full path to your venv `python`
   - **Args:** `-m`, `telnyx_restaurant.mcp_server`
   - **Working directory:** root of this git repo
   - **Env:** `PYTHONPATH=.` and `HANOK_MCP_API_BASE_URL=https://<your-public-api-host>`
4. Save and **publish** the assistant.
5. **Call test:** dial your number and ask to look up a reservation; confirm tool calls hit your public API (watch Render logs or `HANOK_RESERVATION_VERBOSE_LOG`).

If Telnyx only supports **HTTP** MCP transports, set `HANOK_MCP_TRANSPORT=streamable-http`, expose the MCP port securely (VPN, second Render service, or localhost tunnel), and paste the **MCP URL** from Telnyx docs.

### 3. Coexist with HTTP tools

You may keep **existing HTTP Action** tools pointed at `…/api/reservations/…` **and** add MCP. If tools duplicate behavior, tighten the **system instruction** so the model prefers one path (e.g. MCP for structure, HTTP only as fallback).

### 4. Deploy on Render (optional second service)

- **Service A:** existing FastAPI web service (API + webhooks).
- **Service B:** worker or web process that runs `python -m telnyx_restaurant.mcp_server` with `HANOK_MCP_API_BASE_URL=https://service-a-host` and TCP healthchecks if you use streamable HTTP.

Telnyx may still require **stdio** from their runner; confirm in their docs.

### 5. Security

- Do **not** expose MCP over the public internet without authentication unless the product requires it — stdio via spawned process is safest.
- MCP tools forward reservation data; align with your data-retention / demo policy (synthetic data only for the challenge).

---

## Verify without Telnyx

Use any MCP inspector or the official SDK client against stdio, or call the REST API directly with `curl` against the same URLs the tools use.
