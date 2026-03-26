# Telnyx restaurant service

FastAPI app deployed to **Render** (or run locally) for:

- **Dynamic webhook variables** — `POST /webhooks/telnyx/variables`
- **Health** — `GET /health`
- **Reservations API** — `POST /api/reservations` (optional `preorder`, `source_channel`), `GET /api/reservations/menu/items`, `GET /api/reservations/by-code/{code}`
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

## MCP server

See [`mcp_server/README.md`](mcp_server/README.md).
