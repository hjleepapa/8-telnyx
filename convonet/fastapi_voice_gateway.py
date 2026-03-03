from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from typing import List
import time

router = APIRouter()

@router.get("/health")
async def health_check():
    """FastAPI Health Check"""
    return {
        "status": "healthy",
        "framework": "FastAPI",
        "timestamp": time.time()
    }

@router.websocket("/voice-stream")
async def voice_stream_endpoint(websocket: WebSocket):
    """
    Experimental Low-Latency Voice Streaming Endpoint.
    This will eventually replace the Socket.IO based streaming.
    """
    await websocket.accept()
    print("🎙️ FastAPI Voice Stream: WebSocket connected")
    
    try:
        while True:
            # Receive audio data from client
            data = await websocket.receive_bytes()
            
            # Placeholder for real-time STT streaming logic
            # Here we will integrate Deepgram, Cartesia, etc. natively
            
            # For now, echo back a "received" message for verification
            await websocket.send_json({
                "type": "audio_received",
                "size": len(data),
                "server_time": time.time()
            })
            
    except WebSocketDisconnect:
        print("🎙️ FastAPI Voice Stream: WebSocket disconnected")
    except Exception as e:
        print(f"❌ FastAPI Voice Stream Error: {e}")
        try:
            await websocket.close()
        except:
            pass
