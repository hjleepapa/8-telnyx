import json
import logging
import os
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse, Response
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


def _get_sip_config() -> dict:
    """SIP config for call center UI (domain, wss_port). Uses env or defaults."""
    domain = os.getenv("SIP_DOMAIN", "sip.example.com").strip()
    try:
        wss_port = int(os.getenv("SIP_WSS_PORT", "7443"))
    except ValueError:
        wss_port = 7443
    return {"domain": domain, "wss_port": wss_port}


@app.get("/call-center", response_class=HTMLResponse)
async def call_center_ui(request: Request):
    """Serves the Unified Agent Desktop UI"""
    return templates.TemplateResponse(
        "call_center.html",
        {
            "request": request,
            "url_for": _url_for,
            "sip_config": _get_sip_config(),
        },
    )


@app.get("/voice_assistant", response_class=HTMLResponse)
@app.get("/voice-assistant", response_class=HTMLResponse)
async def voice_assistant_ui(request: Request):
    """Voice assistant UI: connects to FastAPI WebSocket at /webrtc/ws (voice-gateway-service). No LiveKit."""
    return templates.TemplateResponse(
        "voice_assistant.html",
        {"request": request, "url_for": _url_for, "websocket_path": "/webrtc/ws"},
    )


@app.get("/vad", response_class=HTMLResponse)
async def voice_assistant_vad_ui(request: Request):
    """Voice assistant UI with simple client-side VAD (auto listening, no Start/Stop button)."""
    return templates.TemplateResponse(
        "voice_assistant_vad.html",
        {"request": request, "url_for": _url_for, "websocket_path": "/webrtc/ws"},
    )


@app.get("/mortgage_dashboard", response_class=HTMLResponse)
async def mortgage_dashboard_ui(request: Request):
    """Mortgage dashboard UI."""
    return templates.TemplateResponse(
        "mortgage_dashboard.html",
        {"request": request, "url_for": _url_for},
    )


@app.get("/agent-monitor", response_class=HTMLResponse)
async def agent_monitor_ui(request: Request):
    """Agent monitor dashboard UI."""
    return templates.TemplateResponse(
        "agent_monitor_dashboard.html",
        {"request": request, "url_for": _url_for},
    )


@app.get("/tool-execution", response_class=HTMLResponse)
async def tool_execution_ui(request: Request):
    """Tool execution dashboard UI."""
    return templates.TemplateResponse(
        "tool_execution_dashboard.html",
        {"request": request, "url_for": _url_for},
    )


@app.get("/convonet_tech_spec", response_class=HTMLResponse)
async def convonet_tech_spec(request: Request):
    """Technical specification page."""
    return templates.TemplateResponse(
        "convonet_tech_spec.html",
        {"request": request, "url_for": _url_for},
    )


@app.get("/convonet_system_architecture", response_class=HTMLResponse)
async def convonet_system_architecture(request: Request):
    """System architecture diagram page."""
    return templates.TemplateResponse(
        "convonet_system_architecture.html",
        {"request": request, "url_for": _url_for},
    )


@app.get("/convonet_sequence_diagram", response_class=HTMLResponse)
async def convonet_sequence_diagram(request: Request):
    """Sequence diagram page."""
    return templates.TemplateResponse(
        "convonet_sequence_diagram.html",
        {"request": request, "url_for": _url_for},
    )


# Agent-monitor dashboard APIs: read from Redis (same store agent-llm-service writes to via AgentMonitor)
def _get_agent_monitor_safe():
    """Return AgentMonitor instance or None if Redis/agent_monitor unavailable."""
    try:
        from convonet.agent_monitor import get_agent_monitor
        return get_agent_monitor()
    except Exception as e:
        logger.warning("Agent monitor unavailable (Redis may be unset on call-center): %s", e)
        return None


@app.get("/agent-monitor/api/stats")
async def agent_monitor_api_stats():
    """Return agent interaction stats from Redis (written by agent-llm-service)."""
    monitor = _get_agent_monitor_safe()
    if not monitor:
        return {
            "success": True,
            "stats": {
                "total_interactions": 0,
                "by_provider": {"claude": 0, "gemini": 0, "openai": 0},
                "total_tool_calls": 0,
                "avg_duration_ms": 0,
            },
        }
    try:
        stats = monitor.get_stats()
        by_provider = stats.get("by_provider") or {}
        # Ensure keys expected by dashboard exist
        by_provider = {
            "claude": by_provider.get("claude", 0),
            "gemini": by_provider.get("gemini", 0),
            "openai": by_provider.get("openai", 0),
            **{k: v for k, v in by_provider.items() if k not in ("claude", "gemini", "openai")},
        }
        return {
            "success": True,
            "stats": {
                "total_interactions": stats.get("total_interactions", 0),
                "by_provider": by_provider,
                "total_tool_calls": stats.get("total_tool_calls", 0),
                "avg_duration_ms": stats.get("avg_duration_ms", 0),
            },
        }
    except Exception as e:
        logger.exception("Error fetching agent-monitor stats")
        return {
            "success": True,
            "stats": {
                "total_interactions": 0,
                "by_provider": {"claude": 0, "gemini": 0, "openai": 0},
                "total_tool_calls": 0,
                "avg_duration_ms": 0,
            },
        }


@app.get("/agent-monitor/api/interactions")
async def agent_monitor_api_interactions(limit: int = 50, provider: Optional[str] = None, agent_type: Optional[str] = None):
    """Return recent agent interactions from Redis (written by agent-llm-service)."""
    monitor = _get_agent_monitor_safe()
    if not monitor:
        return {"success": True, "interactions": []}
    try:
        if provider and provider != "all":
            interactions = monitor.get_interactions_by_provider(provider, limit=limit)
        else:
            interactions = monitor.get_recent_interactions(limit=limit)
        interactions_data = [i.to_dict() for i in interactions]
        if agent_type and agent_type != "all":
            interactions_data = [
                i for i in interactions_data
                if (i.get("metadata") or {}).get("agent_type") == agent_type
            ]
        return {"success": True, "interactions": interactions_data}
    except Exception as e:
        logger.exception("Error fetching agent-monitor interactions")
        return {"success": True, "interactions": []}


# Stub APIs for tool-execution dashboard
@app.get("/tool-execution/api/stats")
async def tool_execution_api_stats():
    """Stub: return empty stats so the dashboard renders until real backend is wired."""
    return {
        "success": True,
        "stats": {
            "total_successful": 0,
            "total_failed": 0,
            "total_timeout": 0,
            "success_rate": 0.0,
            "total_requests": 0,
        },
    }


@app.get("/tool-execution/api/trackers")
async def tool_execution_api_trackers():
    """Stub: return empty list until real backend is wired."""
    return {"success": True, "trackers": []}


@app.get("/tool-execution/api/tracker/{request_id}")
async def tool_execution_api_tracker(request_id: str):
    """Stub: return empty tools until real backend is wired."""
    return {"success": True, "tools": []}


# --- Call-center UI APIs (stubs so /call-center page does not 404 on login/status/call/customer) ---

@app.get("/call-center/api/agent/status")
async def call_center_agent_status():
    """Stub: agent status for call-center UI. No server-side session; always logged_out."""
    return {"logged_in": False, "agent": None}


class CallCenterAgentLogin(BaseModel):
    agent_id: str = ""
    name: str = ""
    sip_username: str = ""
    sip_password: str = ""
    sip_domain: str = "sip.example.com"
    sip_extension: Optional[str] = None
    email: Optional[str] = None


@app.post("/call-center/api/agent/login")
async def call_center_agent_login(data: CallCenterAgentLogin):
    """Stub: accept login so call-center UI can show dashboard and init JsSIP. No server-side session."""
    agent = {
        "agent_id": data.agent_id or "agent-1",
        "name": data.name or data.agent_id,
        "sip_username": data.sip_username,
        "sip_domain": data.sip_domain or "sip.example.com",
        "sip_extension": data.sip_extension or data.agent_id,
    }
    return {"success": True, "agent": agent}


@app.post("/call-center/api/agent/logout")
async def call_center_agent_logout():
    return {"success": True}


@app.post("/call-center/api/agent/ready")
async def call_center_agent_ready():
    return {"success": True}


@app.post("/call-center/api/agent/not-ready")
async def call_center_agent_not_ready():
    return {"success": True}


@app.post("/call-center/api/call/ringing")
async def call_center_call_ringing():
    return {"success": True}


@app.post("/call-center/api/call/answer")
async def call_center_call_answer():
    return {"success": True}


@app.post("/call-center/api/call/drop")
async def call_center_call_drop():
    return {"success": True}


@app.post("/call-center/api/call/hold")
async def call_center_call_hold():
    return {"success": True}


@app.post("/call-center/api/call/unhold")
async def call_center_call_unhold():
    return {"success": True}


@app.post("/call-center/api/call/transfer")
async def call_center_call_transfer():
    return {"success": True}


def _fetch_customer_profile_from_redis(
    extension: Optional[str] = None,
    call_sid: Optional[str] = None,
    call_id: Optional[str] = None,
    customer_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Fetch customer/transfer context from Redis (cached by voice-gateway on transfer). Same keys as Flask call_center."""
    try:
        from convonet.redis_manager import redis_manager
        if not redis_manager.is_available():
            return None
        keys_to_try: List[str] = []
        if extension and call_sid:
            keys_to_try.append(f"callcenter:customer:{extension}:{call_sid}")
        if extension and call_id:
            keys_to_try.append(f"callcenter:customer:{extension}:{call_id}")
        if extension:
            keys_to_try.append(f"callcenter:customer:{extension}")
        if customer_id:
            keys_to_try.append(f"callcenter:customer:{customer_id}")
        for key in keys_to_try:
            raw = redis_manager.redis_client.get(key)
            if raw:
                profile = json.loads(raw) if isinstance(raw, str) else raw
                logger.info("Call center profile HIT: key=%s, has_history=%s", key, bool(profile.get("conversation_history")))
                return profile
        if extension or customer_id:
            logger.debug("Call center profile MISS: extension=%s, call_sid=%s, call_id=%s", extension, call_sid, call_id)
    except Exception as e:
        logger.warning("Failed to read customer cache: %s", e)
    return None


def _enrich_profile_from_suitecrm(profile: Dict[str, Any], fallback_identifier: Optional[str] = None) -> None:
    """
    Look up SuiteCRM contact (by mobile phone) and enrich profile so call-center UI shows
    SuiteCRM contact info (name, mobile, job title, department, email).
    Uses CRM_INTEGRATION_URL if set (recommended so only crm-integration has SuiteCRM creds).
    """
    # Use only profile phone for lookup; never use extension (e.g. "2001") as phone
    phone = profile.get("phone") or ""
    if not phone and fallback_identifier:
        clean_fb = "".join(c for c in str(fallback_identifier) if c.isdigit())
        if len(clean_fb) >= 10:
            phone = fallback_identifier
    if not phone:
        logger.info("SuiteCRM enrichment skipped: no phone in profile (fallback_identifier=%s)", fallback_identifier)
        return
    clean_phone = str(phone).replace("+", "").replace("-", "").replace(" ", "")
    if len(clean_phone) < 10:
        logger.info("SuiteCRM enrichment skipped: phone too short (len=%s)", len(clean_phone))
        return
    result = None
    crm_url = (os.getenv("CRM_INTEGRATION_URL") or "").rstrip("/")
    if crm_url:
        try:
            import requests
            resp = requests.post(f"{crm_url}/patient/search", json={"phone": clean_phone}, timeout=10)
            if resp.status_code == 200:
                result = resp.json()
                logger.info("SuiteCRM enrichment via crm-integration: phone=%s found=%s", clean_phone[:6] + "***", result.get("found"))
            else:
                logger.warning("CRM integration search failed: %s %s", resp.status_code, resp.text[:200])
        except Exception as e:
            logger.warning("CRM integration request failed: %s", e)
    if result is None:
        try:
            from convonet.services.suitecrm_client import SuiteCRMClient
            client = SuiteCRMClient()
            result = client.search_patient(clean_phone)
            if result:
                logger.info("SuiteCRM enrichment via local client: found=%s", result.get("found"))
        except Exception as e:
            logger.warning("Local SuiteCRM lookup failed: %s", e)
    if not result or not result.get("success") or not result.get("found"):
        return
    attrs = result.get("attributes", {}) or {}
    first = attrs.get("first_name", "") or ""
    last = attrs.get("last_name", "") or ""
    full_name = f"{first} {last}".strip()
    if full_name:
        profile["name"] = full_name
    mobile = attrs.get("phone_mobile")
    if mobile:
        profile["phone"] = mobile
    email = attrs.get("email1") or attrs.get("email") or attrs.get("email_address")
    if email:
        profile["email"] = email
    title = attrs.get("title")
    if title:
        profile["job_title"] = title
    dept = attrs.get("department")
    if dept:
        profile["department"] = dept
    if not profile.get("suitecrm_context"):
        profile["suitecrm_context"] = {}
    sc = profile["suitecrm_context"]
    if result.get("patient_id"):
        sc["patient_id"] = result["patient_id"]
    sc["from_lookup"] = True


@app.get("/call-center/api/customer/{customer_id}")
async def call_center_customer(
    customer_id: str,
    extension: Optional[str] = Query(None),
    call_sid: Optional[str] = Query(None),
    call_id: Optional[str] = Query(None),
):
    """Customer popup data. Reads from Redis when call was transferred from voice assistant (conversation_history + context)."""
    profile = _fetch_customer_profile_from_redis(
        extension=extension, call_sid=call_sid, call_id=call_id, customer_id=customer_id
    )
    if profile:
        # Prefer SuiteCRM contact data for display (name, mobile, job title, department, email)
        _enrich_profile_from_suitecrm(profile, fallback_identifier=customer_id)
        return profile
    return {
        "customer_id": customer_id,
        "name": "Customer",
        "phone": customer_id or "",
        "notes": "No context from voice assistant. Call may not be from transfer.",
        "conversation_history": [],
        "activities": [],
    }


@app.get("/call-center/api/customer/data")
async def call_center_customer_data(
    request: Request,
    extension: Optional[str] = Query(None),
    call_sid: Optional[str] = Query(None),
    call_id: Optional[str] = Query(None),
    customer_id: Optional[str] = Query(None),
):
    """Customer data for popup. Prefer Redis cache (transfer context from voice assistant)."""
    profile = _fetch_customer_profile_from_redis(
        extension=extension, call_sid=call_sid, call_id=call_id, customer_id=customer_id
    )
    if profile:
        _enrich_profile_from_suitecrm(profile, fallback_identifier=customer_id)
        return {"customers": [profile], "total": 1}
    return {"customers": [], "total": 0}


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


def _twilio_transfer_base_url() -> str:
    """Base URL for Twilio webhooks (used in transfer_bridge action). Prefer voice-gateway URL so Twilio hits same host."""
    return (
        os.getenv("VOICE_GATEWAY_PUBLIC_URL")
        or os.getenv("WEBHOOK_BASE_URL")
        or os.getenv("CONVONET_API_BASE", "")
    ).rstrip("/")


@app.get("/twilio/voice_assistant/transfer_bridge")
@app.get("/twilio/voice_assistant/transfer_bridge/")
@app.post("/twilio/voice_assistant/transfer_bridge")
@app.post("/twilio/voice_assistant/transfer_bridge/")
async def twilio_transfer_bridge_proxy(request: Request):
    """
    TwiML for Twilio transfer_bridge. When the load balancer sends /twilio/voice_assistant/* to call-center
    instead of voice-gateway, this route returns the same TwiML so Twilio gets 200 and the call continues.
    """
    try:
        from twilio.twiml.voice_response import VoiceResponse
    except ImportError:
        logger.warning("twilio not installed in call-center; transfer_bridge will return 503")
        return Response(content="<Response><Say>Service unavailable.</Say></Response>", media_type="text/xml", status_code=503)
    if request.method == "GET":
        ext = request.query_params.get("extension", "2001")
        return {"status": "ok", "endpoint": "transfer_bridge", "extension": ext, "message": "Use POST for actual transfers."}
    form_data = await request.form()
    extension = form_data.get("extension") or request.query_params.get("extension", "2001")
    call_sid = form_data.get("CallSid", "")
    caller_number = form_data.get("From", "") or os.getenv("TWILIO_PHONE_NUMBER", "")
    logger.info("transfer_bridge (call-center): CallSid=%s From=%s extension=%s", call_sid, caller_number, extension)
    domain = os.getenv("FUSIONPBX_SIP_DOMAIN") or os.getenv("FREEPBX_DOMAIN", "")
    transport = (os.getenv("FUSIONPBX_SIP_TRANSPORT") or "udp").lower()
    timeout = int(os.getenv("TRANSFER_TIMEOUT", "30"))
    sip_user = os.getenv("FREEPBX_SIP_USERNAME") or os.getenv("FUSIONPBX_SIP_USERNAME", "")
    sip_pass = os.getenv("FREEPBX_SIP_PASSWORD") or os.getenv("FUSIONPBX_SIP_PASSWORD", "")
    base_url = _twilio_transfer_base_url()
    callback_url = f"{base_url}/twilio/transfer_callback?extension={extension}" if base_url else None
    response = VoiceResponse()
    if not domain:
        response.say("Transfer is not configured.", voice="Polly.Amy")
        response.hangup()
        return Response(content=str(response), media_type="text/xml")
    sip_uri = f"sip:{extension}@{domain};transport={transport}"
    dial_kwargs = {"answer_on_bridge": True, "timeout": timeout, "caller_id": caller_number}
    if callback_url:
        dial_kwargs["action"] = callback_url
    dial = response.dial(**dial_kwargs)
    if sip_user and sip_pass:
        dial.sip(sip_uri, username=sip_user, password=sip_pass)
    else:
        dial.sip(sip_uri)
    response.say("I'm sorry, the transfer failed. Please try again later.", voice="Polly.Amy")
    response.hangup()
    return Response(content=str(response), media_type="text/xml")


@app.post("/twilio/transfer_callback")
async def twilio_transfer_callback_proxy(request: Request):
    """TwiML callback when Dial ends. Return empty Response so call continues or ends cleanly."""
    try:
        from twilio.twiml.voice_response import VoiceResponse
    except ImportError:
        return Response(content="<Response></Response>", media_type="text/xml")
    form_data = await request.form()
    dial_status = form_data.get("DialCallStatus", "unknown")
    extension = request.query_params.get("extension", "2001")
    logger.info("transfer_callback (call-center): extension=%s DialCallStatus=%s", extension, dial_status)
    response = VoiceResponse()
    if dial_status != "completed":
        response.say("The transfer could not be completed. Please try again later.", voice="Polly.Amy")
        response.hangup()
    return Response(content=str(response), media_type="text/xml")


if __name__ == "__main__":
    import uvicorn
    # Default port 8002 for Call Center Service
    uvicorn.run(app, host="0.0.0.0", port=8002)
