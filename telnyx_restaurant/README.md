# Telnyx restaurant service

FastAPI app deployed to **Render** (or run locally) for:

- **Dynamic webhook variables** — `POST /webhooks/telnyx/variables`
- **Health** — `GET /health`
- **Reservations API** — `POST /api/reservations` (optional `preorder`, `source_channel`), `GET /api/reservations/menu/items`, **`GET /api/reservations/lookup?phone=…&guest_name=…`** (primary guest lookup for voice/UI), legacy `GET /api/reservations/lookup-by-phone`, `GET /api/reservations/by-code/{code}`, `PATCH /api/reservations/{id}` and `PATCH /api/reservations/by-code/{code}` (party, time, pre-order, guest fields) plus `PATCH …/status` for status-only
- **Guests** — `/reserve-online.html`, `/reservation/status?code=HNK-…`
- **Admin** — `/admin/reservations`

## Run (from repository root)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn telnyx_restaurant.app:app --host 127.0.0.1 --port 8080
```

Open **http://127.0.0.1:8080/** in the browser (do not use `http://0.0.0.0:8080/` — that URL often hangs).

(`requirements.txt` at repo root includes `telnyx_restaurant/requirements.txt`.)

### Browser tab “keeps spinning”

1. Confirm the server logged `Uvicorn running on http://127.0.0.1:8080`.
2. In another terminal: `curl -sS -m 5 http://127.0.0.1:8080/ -o /dev/null -w '%{http_code}\n'` — expect `200`. If this works but the browser spins, open **Developer Tools → Network**, reload, and see which request stays **Pending** (often a blocked CDN: fonts, `unpkg`, or an extension).
3. Try a **private/incognito** window with extensions disabled.

## Render

- **Start command:** use the repo root `Procfile` or:  
  `uvicorn telnyx_restaurant.app:app --host 0.0.0.0 --port $PORT`
- **Root directory:** repository root (so `telnyx_restaurant` is importable).

## Webhook test

```bash
curl -s -X POST http://localhost:8080/webhooks/telnyx/variables \
  -H "Content-Type: application/json" \
  -d '{"caller_number": "+15550000001"}' | jq
```

The same `guest_phone` may be stored as `+1925…` or `925…` in Postgres; the webhook normalizes North American numbers before lookup.

### Demo outbound reminder (5s after booking)

Requires **Render env vars** `TELNYX_API_KEY`, `TELNYX_CONNECTION_ID` (Call Control Application id for `POST https://api.telnyx.com/v2/calls`), and `TELNYX_FROM_NUMBER` (+E.164). After each successful `POST /api/reservations`, the server schedules a dial in **5 seconds** (thread timer). Check **`reminder_call_status`** on the row in `/admin/reservations`: `demo_skipped_no_telnyx_config` means env is missing; `telnyx_error_http_*` includes a Telnyx API error (see service logs). `client_state` is sent **base64**-encoded per Telnyx Call Control.

## Telnyx AI Assistant (paste into instructions)

If `has_upcoming_reservation` is **true**, acknowledge their upcoming booking using `next_reservation_code` and `next_reservation_at`; if **false**, do **not** say they have no account—past visits may still appear (`vip_tier` **returning** or food fields); offer a new reservation or lookup by confirmation code instead.

To fetch the booking row without a confirmation code, call **`GET /api/reservations/lookup?phone=…&guest_name=…`** (both required): use the caller’s number for `phone` (same as `telnyx_end_user_target` from the dynamic-variables webhook) and the name as stored on the reservation. Legacy: **`lookup-by-phone`** when there is only one candidate row for that phone (no name) or for disambiguation. **by-code** remains available but is error-prone for voice; webhook URLs must **not** contain literal `{{code}}`.

## MCP server

See [`mcp_server/README.md`](mcp_server/README.md).
