import asyncio
import base64
import json
import os
import uuid
import logging
from typing import Dict, Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
import requests
import time
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request, Response, Query
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError, BaseModel
from twilio.twiml.voice_response import VoiceResponse, Gather

from convonet.schemas import (
    ClientMessageType,
    AuthMessage,
    StartRecordingMessage,
    AudioChunkMessage,
    AuthOkMessage,
    TranscriptPartialMessage,
    TranscriptFinalMessage,
    ErrorMessage,
    ServerMessageType
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

# Internal service URL for Agent LLM
AGENT_LLM_URL = os.getenv("AGENT_LLM_URL", "http://agent-llm-service:8001")

def get_webhook_base_url():
    return os.getenv('WEBHOOK_BASE_URL', os.getenv('RENDER_EXTERNAL_URL', ''))

# Active WebSocket connections: session_id -> WebSocket
active_connections: Dict[str, WebSocket] = {}

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
        
        response = VoiceResponse()
        
        if transfer_marker:
            # Handle transfer logic
            response.say("Transferring you to an agent. Please wait.", voice='Polly.Amy')
            # Assuming FusionPBX or similar setup
            response.dial("2001") # Placeholder
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
                await websocket.send_json(ErrorMessage(message="Missing message type").model_dump())
                continue
                
            msg_type = message["type"]
            
            try:
                if msg_type == ClientMessageType.AUTHENTICATE:
                    auth = AuthMessage(**message)
                    # Logic for authentication (PIN/Token)
                    session_id = auth.session_id or session_id
                    active_connections[session_id] = websocket
                    await websocket.send_json(AuthOkMessage(session_id=session_id).model_dump())
                    logger.info(f"Session {session_id} authenticated")
                    
                elif msg_type == ClientMessageType.START_RECORDING:
                    start = StartRecordingMessage(**message)
                    logger.info(f"Starting recording for session {start.session_id}")
                    # Initialize STT resources here
                    
                elif msg_type == ClientMessageType.AUDIO_CHUNK:
                    chunk = AudioChunkMessage(**message)
                    # Process audio chunk (send to STT)
                    # For now, just logging sequence
                    if chunk.sequence % 50 == 0:
                        logger.info(f"Received audio chunk {chunk.sequence} for {chunk.session_id}")
                        
                elif msg_type == ClientMessageType.STOP_RECORDING:
                    logger.info(f"Stopping recording for session {session_id}")
                    # Finalize STT and trigger LLM
                    
                elif msg_type == ClientMessageType.HEARTBEAT:
                    pass # Keep-alive
                    
            except ValidationError as e:
                await websocket.send_json(ErrorMessage(session_id=session_id, message=str(e)).model_dump())
                
    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {session_id}")
        if session_id in active_connections:
            del active_connections[session_id]
    except Exception as e:
        logger.error(f"Error in WebSocket handler: {e}")
        await websocket.close()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
