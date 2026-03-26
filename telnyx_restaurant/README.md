# Telnyx restaurant service

FastAPI app deployed to **Render** (or run locally) for:

- **Dynamic webhook variables** — `POST /webhooks/telnyx/variables`
- **Health** — `GET /health`
- **Future:** REST routes for reservations consumed by the MCP server

## Run (from repository root)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn telnyx_restaurant.app:app --host 0.0.0.0 --port 8080
```

(`requirements.txt` at repo root includes `telnyx_restaurant/requirements.txt`.)

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
