import logging
import os
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("call-center-service")

from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Call Center Service")

# Mount static files from the project root
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")
# Mount call_center static so url_for('call_center.static', filename='...') works
if os.path.exists("call_center/static"):
    app.mount("/call_center/static", StaticFiles(directory="call_center/static"), name="call_center_static")

# Setup templates - use absolute paths so they work from any cwd (e.g. Docker /app)
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_template_dirs = [
    os.path.join(_root, "call_center", "templates"),
    os.path.join(_root, "templates"),
]
templates = Jinja2Templates(directory=[d for d in _template_dirs if os.path.isdir(d)] or ["templates"])


def _url_for(name: str, **kwargs) -> str:
    """Flask-style url_for for Jinja so index.html and base.html render without errors."""
    if name == "static" and "filename" in kwargs:
        return f"/static/{kwargs['filename'].lstrip('/')}"
    if name == "call_center.static" and "filename" in kwargs:
        return f"/call_center/static/{kwargs['filename'].lstrip('/')}"
    # Route names used by index.html / base.html (links can point to # or same-host paths)
    if name == "convonet_tech_spec":
        return "/convonet_tech_spec"
    if name == "convonet_system_architecture":
        return "/convonet_system_architecture"
    if name == "convonet_sequence_diagram":
        return "/convonet_sequence_diagram"
    return "#"


@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "call-center-service"}


@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Serves the main landing page for FastAPI/GCP microservices."""
    # Optional: set CONVONET_API_BASE to agent-llm-service URL if provider APIs are on another origin (e.g. https://agent-llm-xxx.run.app)
    convonet_api_base = os.getenv("CONVONET_API_BASE", "").rstrip("/")
    voice_assistant_url = os.getenv("VOICE_ASSISTANT_URL", "").strip() or None
    mortgage_dashboard_url = os.getenv("MORTGAGE_DASHBOARD_URL", "").strip() or None
    context = {
        "request": request,
        "url_for": _url_for,
        "convonet_api_base": convonet_api_base,
        "voice_assistant_url": voice_assistant_url,
        "mortgage_dashboard_url": mortgage_dashboard_url,
        "llm_providers": ["Gemini", "GPT-4", "Claude"],
        "stt_providers": ["Google", "Deepgram", "OpenAI"],
        "tts_providers": ["Google", "ElevenLabs", "Cartesia"],
    }
    return templates.TemplateResponse("index.html", context)


@app.get("/call-center", response_class=HTMLResponse)
async def call_center_ui(request: Request):
    """Serves the Unified Agent Desktop UI"""
    return templates.TemplateResponse(
        "call_center.html",
        {"request": request, "url_for": _url_for},
    )

class AgentStatusUpdate(BaseModel):
    agent_id: str
    status: str # e.g., "Available", "Busy", "Offline"

@app.post("/api/agent/status")
async def update_agent_status(data: AgentStatusUpdate):
    logger.info(f"Updating agent {data.agent_id} status to {data.status}")
    # In a real implementation, this would update SQLAlchemy models In DB
    return {"success": True, "agent_id": data.agent_id, "new_status": data.status}

class CallInfo(BaseModel):
    call_sid: str
    direction: str
    from_number: str
    to_number: str
    status: str

@app.post("/api/call/event")
async def handle_call_event(call: CallInfo):
    logger.info(f"Call event received: {call.call_sid} - {call.status}")
    # Log call activity to DB
    return {"success": True}

@app.get("/api/customer/profile")
async def get_customer_profile(phone: str):
    """Fetches customer profile, enriched by CRM microservice if needed"""
    logger.info(f"Fetching profile for {phone}")
    # Real implementation would call CRM microservice
    return {
        "phone": phone,
        "name": "Jane Doe",
        "last_interaction": str(datetime.datetime.now()),
        "summary": "Potential mortgage lead."
    }

if __name__ == "__main__":
    import uvicorn
    # Default port 8002 for Call Center Service
    uvicorn.run(app, host="0.0.0.0", port=8002)
