"""FastAPI entrypoint: health, telemetry, and Telnyx dynamic webhook routes."""

from fastapi import FastAPI

from telnyx_restaurant.routers import webhook

app = FastAPI(
    title="Telnyx Restaurant Reservation API",
    description="Dynamic webhooks and REST backing store for MCP tools (Telnyx AI challenge).",
    version="0.1.0",
)

app.include_router(webhook.router, prefix="/webhooks/telnyx", tags=["telnyx"])


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
