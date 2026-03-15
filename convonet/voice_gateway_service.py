import asyncio
import base64
import json
import os
import time
import uuid
import logging
from typing import Dict, Optional, List, Any, Tuple

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError, BaseModel
from twilio.twiml.voice_response import VoiceResponse, Gather
import requests

from convonet.schemas import (
    ClientMessageType,
    AuthMessage,
    StartRecordingMessage,
    AudioChunkMessage,
    AuthOkMessage,
    GreetingMessage,
    ProcessingStartMessage,
    TranscriptPartialMessage,
    TranscriptFinalMessage,
    ErrorMessage,
    ServerMessageType,
    StatusMessage,
    AgentFinalMessage,
    AudioChunkOutMessage,
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice-gateway-service")

app = FastAPI(title="Voice Gateway Service")

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Internal service URL for Agent LLM (Cloud Run uses 8080; set via env for your deployment)
AGENT_LLM_URL = os.getenv("AGENT_LLM_URL", "http://localhost:8080").rstrip("/")

def get_webhook_base_url():
    """Base URL for Twilio webhooks (voice-gateway public URL). Set WEBHOOK_BASE_URL or VOICE_GATEWAY_PUBLIC_URL."""
    return (
        os.getenv("VOICE_GATEWAY_PUBLIC_URL")
        or os.getenv("WEBHOOK_BASE_URL")
        or os.getenv("RENDER_EXTERNAL_URL", "")
    ).rstrip("/")


def _cache_transfer_context_for_call_center(
    extension: str,
    transfer_context: Dict[str, Any],
    call_sid: Optional[str] = None,
    call_id: Optional[str] = None,
) -> None:
    """Store transfer context (conversation history, user) in Redis for call-center UI. Key: callcenter:customer:{extension}:{call_sid|call_id}."""
    if not extension or not transfer_context:
        return
    try:
        from convonet.redis_manager import redis_manager
        if not redis_manager.is_available():
            return
        profile = {
            "extension": extension,
            "customer_id": transfer_context.get("user_id") or "unknown",
            "name": transfer_context.get("user_name") or "Voice Caller",
            "phone": "",
            "notes": "Transferred from voice assistant",
            "conversation_history": transfer_context.get("conversation_history") or [],
            "activities": [],
        }
        if call_sid:
            profile["call_sid"] = call_sid
            key = f"callcenter:customer:{extension}:{call_sid}"
        elif call_id:
            profile["call_id"] = call_id
            key = f"callcenter:customer:{extension}:{call_id}"
        else:
            return
        redis_manager.redis_client.setex(key, 300, json.dumps(profile))
        logger.info("Cached transfer context for call-center: %s", key)
        fallback_key = f"callcenter:customer:{extension}"
        redis_manager.redis_client.setex(fallback_key, 300, json.dumps(profile))
    except Exception as e:
        logger.warning("Failed to cache transfer context: %s", e)


def _parse_transfer_marker(marker: str) -> Tuple[str, str, str]:
    """Parse TRANSFER_INITIATED:extension|department|reason. Returns (extension, department, reason)."""
    if not marker or "TRANSFER_INITIATED:" not in marker:
        return ("2001", "support", "User requested transfer to human agent")
    data = marker.replace("TRANSFER_INITIATED:", "").strip()
    parts = data.split("|")
    extension = (parts[0].strip() or "2001") if parts else "2001"
    department = (parts[1].strip() or "support") if len(parts) > 1 else "support"
    reason = (parts[2].strip() or "User requested transfer") if len(parts) > 2 else "User requested transfer"
    return (extension, department, reason)


def _build_transfer_twiml(extension: str, department: str, reason: str) -> str:
    """
    Build TwiML to transfer the current call to a SIP endpoint (e.g. 2001@FusionPBX).
    Uses FREEPBX_DOMAIN or FUSIONPBX_SIP_DOMAIN, optional SIP auth, TRANSFER_TIMEOUT, and optional callback.
    """
    domain = os.getenv("FUSIONPBX_SIP_DOMAIN") or os.getenv("FREEPBX_DOMAIN", "")
    transport = (os.getenv("FUSIONPBX_SIP_TRANSPORT") or "udp").lower()
    timeout = int(os.getenv("TRANSFER_TIMEOUT", "30"))
    sip_user = os.getenv("FREEPBX_SIP_USERNAME") or os.getenv("FUSIONPBX_SIP_USERNAME", "")
    sip_pass = os.getenv("FREEPBX_SIP_PASSWORD") or os.getenv("FUSIONPBX_SIP_PASSWORD", "")
    caller_id = (
        os.getenv("TWILIO_TRANSFER_CALLER_ID")
        or os.getenv("TWILIO_CALLER_ID")
        or os.getenv("TWILIO_PHONE_NUMBER", "")
    )

    response = VoiceResponse()
    response.say("Transferring you to an agent. Please wait.", voice="Polly.Amy")

    if not domain:
        logger.warning("Twilio transfer: FREEPBX_DOMAIN / FUSIONPBX_SIP_DOMAIN not set; cannot build SIP Dial")
        response.say("Transfer is not configured. Please try again later.", voice="Polly.Amy")
        return str(response)

    sip_uri = f"sip:{extension}@{domain};transport={transport}"
    logger.info("Twilio transfer: dialing SIP URI=%s", sip_uri)

    base_url = get_webhook_base_url()
    dial_kwargs = dict(
        answer_on_bridge=True,
        timeout=timeout,
    )
    if caller_id:
        dial_kwargs["caller_id"] = caller_id
    if base_url:
        dial_kwargs["action"] = f"{base_url}/twilio/transfer_callback?extension={extension}"

    dial = response.dial(**dial_kwargs)
    if sip_user and sip_pass:
        dial.sip(sip_uri, username=sip_user, password=sip_pass)
    else:
        dial.sip(sip_uri)

    response.say("I'm sorry, the transfer failed. Please try again later.", voice="Polly.Amy")
    response.hangup()
    return str(response)

# Greeting text after PIN login (played via TTS)
DEFAULT_GREETING = "Hi, this is Convonet AI. What can I help you with today?"


def _synthesize_greeting_sync(user_name: Optional[str] = None) -> Tuple[str, Optional[bytes]]:
    """Build greeting text and TTS audio. Runs in thread. Returns (text, audio_bytes)."""
    if user_name and user_name.strip():
        text = f"Hi {user_name.strip()}, this is Convonet AI. What can I help you with today?"
    else:
        text = DEFAULT_GREETING
    try:
        from convonet.deepgram import get_deepgram_service
        svc = get_deepgram_service()
        audio = svc.synthesize_speech(text, voice="aura-asteria-en")
        return (text, audio)
    except Exception as e:
        logger.warning("Greeting TTS failed: %s", e)
        return (text, None)


# Active WebSocket connections: session_id -> WebSocket
active_connections: Dict[str, WebSocket] = {}

# Optional PIN authentication for WebSocket voice.
# When ENABLE_VOICE_PIN=true, PIN is required and validated against users_anthropic (DB_URI) if set; else fallback to VOICE_PIN env.
ENABLE_VOICE_PIN = os.getenv("ENABLE_VOICE_PIN", "false").lower() == "true"
VOICE_PIN = os.getenv("VOICE_PIN") or os.getenv("TEST_VOICE_PIN", "1234")

# Per-session state for WebSocket voice: recording flag, authenticated, and accumulated audio chunks (bytes)
_session_state: Dict[str, Dict[str, Any]] = {}

def _get_session(session_id: str) -> Dict[str, Any]:
    if session_id not in _session_state:
        _session_state[session_id] = {
            "recording": False,
            "chunks": [],
            "user_id": None,
            "user_name": None,
            "authenticated": not ENABLE_VOICE_PIN,
        }
    return _session_state[session_id]


def _lookup_user_by_pin(pin: str) -> Optional[Tuple[str, str]]:
    """Look up user in users_anthropic by voice_pin (DB_URI). Returns (user_id, user_name) or None."""
    if not pin or not pin.strip():
        return None
    db_uri = os.getenv("DB_URI")
    if not db_uri:
        return None
    try:
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from convonet.models.user_models import User
        engine = create_engine(db_uri, pool_pre_ping=True)
        Session = sessionmaker(bind=engine)
        with Session() as db:
            user = db.query(User).filter(
                User.voice_pin == pin.strip(),
                User.is_active == True,
            ).first()
            if user:
                name = getattr(user, "full_name", None) or user.first_name or str(user.id)
                return (str(user.id), name)
    except Exception as e:
        logger.warning("PIN lookup failed: %s", e)
    return None


def _validate_pin_and_get_user(pin: Optional[str]) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate PIN: first try users_anthropic (DB_URI); else env VOICE_PIN when ENABLE_VOICE_PIN.
    Returns (ok, user_id, user_name). user_id/user_name are set when DB lookup succeeds or env PIN matches.
    """
    pin_clean = (pin or "").strip()
    # 1) Try DB lookup (users_anthropic.voice_pin)
    user_info = _lookup_user_by_pin(pin_clean)
    if user_info:
        return (True, user_info[0], user_info[1])
    # 2) PIN required but not found in DB: allow env fallback only when ENABLE_VOICE_PIN and PIN matches VOICE_PIN
    if ENABLE_VOICE_PIN and pin_clean and pin_clean == VOICE_PIN:
        return (True, "test_user", "Test User")
    if ENABLE_VOICE_PIN:
        return (False, None, None)
    # 3) PIN not required: allow (no PIN or any PIN that didn't match DB)
    return (True, "voice-ws", None)


def _run_stt_tts_pipeline_sync(
    session_id: str,
    audio_bytes: bytes,
    language: str,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    t0: Optional[float] = None,
) -> Tuple[Optional[str], Optional[str], Optional[bytes], Optional[str]]:
    """Run STT -> agent -> TTS synchronously (call from thread). Returns (transcript, agent_text, tts_audio_bytes, transfer_marker)."""
    transcript = None
    agent_text = None
    tts_audio = None
    transfer_marker = None
    uid = user_id or "voice-ws"
    if t0 is None:
        t0 = time.time()
    try:
        # Buffer captured / process_audio_async entered (same moment in batch pipeline)
        buffer_capture_ms = (time.time() - t0) * 1000
        # STT (Deepgram batch)
        from convonet.deepgram import transcribe_audio_with_deepgram_webrtc
        transcript = transcribe_audio_with_deepgram_webrtc(audio_bytes, language=language or "en")
        stt_ms = (time.time() - t0) * 1000
        if not transcript or not transcript.strip():
            return (None, None, None, None)
        # Agent LLM: send metadata so agent-monitor can show tool calls and voice response timing
        agent_url = f"{AGENT_LLM_URL}/agent/process"
        payload = {
            "prompt": transcript,
            "user_id": uid,
            "session_id": session_id,
        }
        if user_name:
            payload["user_name"] = user_name
        payload["metadata"] = {
            "source": "voice",
            "t0": t0,
            "stt_provider": "deepgram",
            "tts_provider": "deepgram",
            "voice_timing": {
                "buffer_capture_ms": round(buffer_capture_ms, 0),
                "process_audio_async_ms": round(buffer_capture_ms, 0),
                "stt_ms": round(stt_ms, 0),
            },
        }
        resp = requests.post(agent_url, json=payload, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        agent_text = data.get("response") or ""
        transfer_marker = data.get("transfer_marker")
        transfer_context = data.get("transfer_context")
        if transfer_marker and transfer_context:
            ext, _dept, _reason = _parse_transfer_marker(transfer_marker)
            _cache_transfer_context_for_call_center(ext, transfer_context, call_sid=None, call_id=session_id)
        if not agent_text.strip():
            return (transcript, "", None, transfer_marker)
        # TTS (Deepgram)
        from convonet.deepgram import get_deepgram_service
        svc = get_deepgram_service()
        tts_audio = svc.synthesize_speech(agent_text, voice="aura-asteria-en")
        return (transcript, agent_text, tts_audio, transfer_marker)
    except Exception as e:
        logger.exception("Pipeline error: %s", e)
        return (transcript, agent_text, tts_audio, None)


async def _run_pipeline_and_send(
    websocket: WebSocket,
    session_id: str,
    audio_bytes: bytes,
    language: str,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    t0: Optional[float] = None,
) -> None:
    """Run STT -> agent -> TTS in executor and send results over WebSocket. t0 = when user clicked stop (for voice_timing)."""
    if t0 is None:
        t0 = time.time()
    loop = asyncio.get_event_loop()
    try:
        await websocket.send_json(
            StatusMessage(session_id=session_id, message="Transcribing…").model_dump(mode="json")
        )
        await websocket.send_json(
            StatusMessage(session_id=session_id, message="Please wait…").model_dump(mode="json")
        )
        await websocket.send_json(
            ProcessingStartMessage(
                session_id=session_id,
                started_at_ts=time.time(),
            ).model_dump(mode="json")
        )
        result = await loop.run_in_executor(
            None,
            _run_stt_tts_pipeline_sync,
            session_id,
            audio_bytes,
            language,
            user_id,
            user_name,
            t0,
        )
        transcript, agent_text, tts_audio, transfer_marker = (result + (None, None, None))[:4]
        if not transcript or not transcript.strip():
            await websocket.send_json(
                ErrorMessage(session_id=session_id, message="No speech detected. Please try again.").model_dump(mode="json")
            )
            return
        await websocket.send_json(
            TranscriptFinalMessage(session_id=session_id, text=transcript).model_dump(mode="json")
        )
        if not agent_text or not agent_text.strip():
            await websocket.send_json(
                ErrorMessage(session_id=session_id, message="Agent returned no response.").model_dump(mode="json")
            )
            return
        await websocket.send_json(
            AgentFinalMessage(session_id=session_id, text=agent_text, transfer_marker=transfer_marker).model_dump(mode="json")
        )
        if tts_audio:
            b64 = base64.b64encode(tts_audio).decode("utf-8")
            await websocket.send_json(
                AudioChunkOutMessage(
                    session_id=session_id, chunk_index=0, total_chunks=1, data_b64=b64, is_final=True
                ).model_dump(mode="json")
            )
    except Exception as e:
        logger.exception("Pipeline send error: %s", e)
        try:
            await websocket.send_json(
                ErrorMessage(session_id=session_id, message=str(e)).model_dump(mode="json")
            )
        except Exception:
            pass

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "voice-gateway-service"}

@app.post("/twilio/call")
async def twilio_call(request: Request):
    """Handles initial incoming call from Twilio"""
    form_data = await request.form()
    call_sid = form_data.get("CallSid")
    logger.info(f"Incoming call: {call_sid}")
    
    response = VoiceResponse()
    gather = Gather(
        input='dtmf speech',
        action='/twilio/verify_pin',
        method='POST',
        timeout=10,
        finish_on_key='#'
    )
    gather.say("Welcome to Convonet productivity assistant. Please enter your 4 pin, then press pound.", voice='Polly.Amy')
    response.append(gather)
    
    response.say("I didn't receive a pin. Please try again.", voice='Polly.Amy')
    response.redirect('/twilio/call')
    
    return Response(content=str(response), media_type="text/xml")

@app.post("/twilio/verify_pin")
async def verify_pin(request: Request):
    """Verifies PIN and starts processing conversation"""
    form_data = await request.form()
    pin = form_data.get("Digits") or form_data.get("SpeechResult")
    call_sid = form_data.get("CallSid")
    
    # In a real implementation, this would look up the user in the DB
    # For now, we'll assume authentication is successful for demo purposes
    # or make a call to an identity service.
    
    user_id = "user-123" # Mock user_id
    
    response = VoiceResponse()
    gather = Gather(
        input='speech',
        action=f'/twilio/process_audio?user_id={user_id}',
        method='POST',
        speech_timeout='auto',
        timeout=10,
        barge_in=True
    )
    gather.say("Welcome back! How can I help you today?", voice='Polly.Amy')
    response.append(gather)
    
    return Response(content=str(response), media_type="text/xml")

@app.post("/twilio/process_audio")
async def process_audio(request: Request, user_id: str = Query(...)):
    """Sends transcription to Agent LLM and returns TwiML response"""
    form_data = await request.form()
    transcription = form_data.get("SpeechResult")
    call_sid = form_data.get("CallSid")
    
    if not transcription:
        response = VoiceResponse()
        response.say("I didn't hear anything. Please try again.", voice='Polly.Amy')
        response.redirect(f'/twilio/process_audio?user_id={user_id}')
        return Response(content=str(response), media_type="text/xml")
    
    # Call Agent LLM microservice
    try:
        agent_req = {
            "prompt": transcription,
            "user_id": user_id,
            "session_id": call_sid
        }
        logger.info(f"Calling Agent LLM for {user_id}")
        resp = requests.post(f"{AGENT_LLM_URL}/agent/process", json=agent_req, timeout=15)
        resp.raise_for_status()
        agent_data = resp.json()
        agent_response = agent_data.get("response")
        transfer_marker = agent_data.get("transfer_marker")
        transfer_context = agent_data.get("transfer_context")
        if transfer_marker and transfer_context and call_sid:
            ext, _dept, _reason = _parse_transfer_marker(transfer_marker)
            _cache_transfer_context_for_call_center(ext, transfer_context, call_sid=call_sid, call_id=None)
        
        response = VoiceResponse()
        
        if transfer_marker:
            ext, dept, reason = _parse_transfer_marker(transfer_marker)
            twiml = _build_transfer_twiml(ext, dept, reason)
            return Response(content=twiml, media_type="text/xml")
        else:
            gather = Gather(
                input='speech',
                action=f'/twilio/process_audio?user_id={user_id}',
                method='POST',
                speech_timeout='auto',
                timeout=10,
                barge_in=True
            )
            gather.say(agent_response, voice='Polly.Amy')
            response.append(gather)
            
        return Response(content=str(response), media_type="text/xml")
        
    except Exception as e:
        logger.error(f"Error calling Agent LLM: {e}")
        response = VoiceResponse()
        response.say("I'm sorry, I'm having trouble connecting to the brain. Please try again later.", voice='Polly.Amy')
        response.hangup()
        return Response(content=str(response), media_type="text/xml")


@app.post("/twilio/transfer_callback")
async def twilio_transfer_callback(request: Request):
    """
    Twilio callback when the Dial to SIP completes (success, no-answer, busy, failed).
    Returns TwiML to speak status and hang up if the transfer did not complete.
    """
    form_data = await request.form()
    dial_status = form_data.get("DialCallStatus", "unknown")
    call_sid = form_data.get("CallSid", "")
    extension = request.query_params.get("extension", "2001")

    logger.info("Twilio transfer_callback: call_sid=%s extension=%s DialCallStatus=%s", call_sid, extension, dial_status)

    response = VoiceResponse()
    if dial_status == "completed":
        # Call was connected; nothing more to do
        pass
    elif dial_status == "busy":
        response.say("The agent is currently busy. Please try again later.", voice="Polly.Amy")
        response.hangup()
    elif dial_status == "no-answer":
        response.say("The agent did not answer. Please try again later.", voice="Polly.Amy")
        response.hangup()
    else:
        response.say("The transfer could not be completed. Please try again later.", voice="Polly.Amy")
        response.hangup()

    return Response(content=str(response), media_type="text/xml")


@app.websocket("/webrtc/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    session_id = str(uuid.uuid4())
    logger.info(f"New WebSocket connection: {session_id}")
    
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            
            if "type" not in message:
                await websocket.send_json(ErrorMessage(message="Missing message type").model_dump(mode="json"))
                continue
            
            msg_type = message["type"]
            
            try:
                if msg_type == ClientMessageType.AUTHENTICATE:
                    auth = AuthMessage(**message)
                    session_id = auth.session_id or session_id
                    active_connections[session_id] = websocket
                    state = _get_session(session_id)
                    pin = (getattr(auth, "pin", None) or "")
                    ok, uid, name = _validate_pin_and_get_user(pin)
                    if not ok:
                        await websocket.send_json(
                            ErrorMessage(
                                session_id=session_id,
                                message="Invalid or missing PIN. Please try again.",
                                code="auth_failed",
                            ).model_dump(mode="json")
                        )
                        logger.warning(f"Session {session_id} auth failed: invalid or missing PIN")
                        continue
                    state["authenticated"] = True
                    state["user_id"] = uid
                    state["user_name"] = name
                    await websocket.send_json(
                        AuthOkMessage(
                            session_id=session_id,
                            user_id=state["user_id"],
                            user_name=state.get("user_name"),
                        ).model_dump(mode="json")
                    )
                    logger.info(f"Session {session_id} authenticated (user_id={uid})")
                    # Send greeting TTS so caller hears "Hi, this is Convonet AI. What can I help you with today?"
                    async def send_greeting():
                        try:
                            loop = asyncio.get_event_loop()
                            greeting_text, greeting_audio = await loop.run_in_executor(
                                None, _synthesize_greeting_sync, state.get("user_name")
                            )
                            if greeting_audio:
                                b64 = base64.b64encode(greeting_audio).decode("utf-8")
                                await websocket.send_json(
                                    GreetingMessage(
                                        session_id=session_id,
                                        text=greeting_text,
                                        data_b64=b64,
                                    ).model_dump(mode="json")
                                )
                                logger.info("Greeting sent to %s", session_id)
                        except Exception as e:
                            logger.warning("Failed to send greeting: %s", e)
                    asyncio.create_task(send_greeting())
                    
                elif msg_type == ClientMessageType.START_RECORDING:
                    start = StartRecordingMessage(**message)
                    sid = start.session_id or session_id
                    state = _get_session(sid)
                    if ENABLE_VOICE_PIN and not state.get("authenticated"):
                        await websocket.send_json(
                            ErrorMessage(
                                session_id=sid,
                                message="Please authenticate with your PIN first.",
                                code="auth_required",
                            ).model_dump(mode="json")
                        )
                        continue
                    state["recording"] = True
                    state["chunks"] = []
                    state["language"] = (start.language or "en-US").split("-")[0] or "en"
                    await websocket.send_json(
                        StatusMessage(session_id=sid, message="Recording…").model_dump(mode="json")
                    )
                    logger.info(f"Recording started for session {sid}")
                    
                elif msg_type == ClientMessageType.AUDIO_CHUNK:
                    chunk = AudioChunkMessage(**message)
                    sid = chunk.session_id or session_id
                    state = _get_session(sid)
                    if state.get("recording"):
                        try:
                            data_bytes = base64.b64decode(chunk.data_b64)
                            state["chunks"].append(data_bytes)
                        except Exception as e:
                            logger.warning("Invalid audio chunk b64: %s", e)
                    if chunk.sequence % 50 == 0:
                        logger.debug("Audio chunk %s for %s", chunk.sequence, sid)
                        
                elif msg_type == ClientMessageType.STOP_RECORDING:
                    stop_sid = message.get("session_id") or session_id
                    state = _get_session(stop_sid)
                    state["recording"] = False
                    chunks = state.get("chunks") or []
                    language = state.get("language") or "en"
                    logger.info(f"Stop recording for {stop_sid}, {len(chunks)} chunks")
                    audio_bytes = b"".join(chunks) if chunks else b""
                    min_bytes = 2000  # ~0.1s at 16kHz mono 16-bit
                    if len(audio_bytes) < min_bytes:
                        await websocket.send_json(
                            ErrorMessage(
                                session_id=stop_sid,
                                message="Recording too short. Speak for at least a second and try again.",
                            ).model_dump(mode="json")
                        )
                    else:
                        t0 = time.time()  # T0 = user clicked "Click to stop" for agent-monitor voice_timing
                        asyncio.create_task(
                            _run_pipeline_and_send(
                                websocket,
                                stop_sid,
                                audio_bytes,
                                language,
                                user_id=state.get("user_id"),
                                user_name=state.get("user_name"),
                                t0=t0,
                            )
                        )
                    
                elif msg_type == ClientMessageType.HEARTBEAT:
                    pass
                    
            except ValidationError as e:
                await websocket.send_json(
                    ErrorMessage(session_id=session_id, message=str(e)).model_dump(mode="json")
                )
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
        if session_id in active_connections:
            del active_connections[session_id]
        _session_state.pop(session_id, None)
    except Exception as e:
        logger.error("WebSocket error: %s", e)
        _session_state.pop(session_id, None)
        try:
            await websocket.close()
        except Exception:
            pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
