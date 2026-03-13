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

# Setup templates - we'll look in both the call_center and monolith templates
templates = Jinja2Templates(directory=["call_center/templates", "templates"])

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "call-center-service"}

@app.get("/", response_class=HTMLResponse)
async def landing_page(request: Request):
    """Serves the main landing page (same as monolith index.html)"""
    # In monolith, index.html might expect some context variables
    context = {
        "request": request,
        "llm_providers": ["Gemini", "GPT-4", "Claude"], # Example providers
        "stt_providers": ["Google", "Deepgram", "OpenAI"],
        "tts_providers": ["Google", "ElevenLabs", "Cartesia"]
    }
    return templates.TemplateResponse("index.html", context)

@app.get("/call-center", response_class=HTMLResponse)
async def call_center_ui(request: Request):
    """Serves the Unified Agent Desktop UI"""
    return templates.TemplateResponse("call_center.html", {"request": request})

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
