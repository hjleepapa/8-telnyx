import asyncio
import logging
import os
import uuid
import time
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Body, Query, APIRouter
from pydantic import BaseModel
from convonet.redis_manager import redis_manager
from convonet.llm_provider_manager import get_llm_provider_manager

# Import core agent logic
from convonet.routes import _run_agent_async

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agent-llm-service")

app = FastAPI(title="Agent LLM Service")
api_router = APIRouter(prefix="/convonet_todo")

# --- Models ---

class AgentRequest(BaseModel):
    prompt: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    session_id: Optional[str] = None
    reset_thread: bool = False
    model: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

class AgentResponse(BaseModel):
    response: str
    transfer_marker: Optional[str] = None
    processing_time_ms: float

class ProviderUpdate(BaseModel):
    user_id: str
    provider: str

# --- Endpoints ---

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "agent-llm-service"}

async def _process_agent_impl(request: AgentRequest) -> AgentResponse:
    logger.info(f"Processing agent request for user {request.user_id} (session: {request.session_id})")
    start_time = time.time()
    try:
        result = await _run_agent_async(
            prompt=request.prompt,
            user_id=request.user_id,
            user_name=request.user_name,
            reset_thread=request.reset_thread,
            model=request.model,
            session_id=request.session_id,
            metadata=request.metadata,
            include_metadata=True
        )
        elapsed_ms = (time.time() - start_time) * 1000
        if isinstance(result, dict):
            return AgentResponse(
                response=result.get("response", ""),
                transfer_marker=result.get("transfer_marker"),
                processing_time_ms=elapsed_ms
            )
        return AgentResponse(
            response=result,
            transfer_marker=None,
            processing_time_ms=elapsed_ms
        )
    except Exception as e:
        logger.error(f"Error processing agent request: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# Top-level route for voice-gateway and other callers (expect /agent/process)
@app.post("/agent/process", response_model=AgentResponse)
async def process_agent(request: AgentRequest):
    return await _process_agent_impl(request)

# --- Provider Management APIs (under /convonet_todo) ---

@api_router.post("/api/agent/process", response_model=AgentResponse)
async def process_agent_router(request: AgentRequest):
    return await _process_agent_impl(request)

@api_router.get("/api/llm-providers")
async def get_llm_providers():
    try:
        provider_manager = get_llm_provider_manager()
        return {"success": True, "providers": provider_manager.get_available_providers()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/api/llm-provider")
async def get_user_llm_provider(user_id: str = "default"):
    try:
        provider = redis_manager.get(f"user:{user_id}:llm_provider") or "claude"
        return {"success": True, "provider": provider}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.post("/api/llm-provider")
async def set_user_llm_provider(data: ProviderUpdate):
    try:
        provider = data.provider.lower()
        if provider not in ['claude', 'gemini', 'openai']:
            raise HTTPException(status_code=400, detail="Invalid provider")
            
        provider_manager = get_llm_provider_manager()
        if not provider_manager.is_provider_available(provider):
            raise HTTPException(status_code=400, detail=f"Provider {provider} not configured")
            
        redis_manager.set(f"user:{data.user_id}:llm_provider", provider)
        return {"success": True, "message": f"LLM provider set to {provider}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@api_router.get("/api/stt-providers")
async def get_stt_providers():
    providers = [
        {"id": "deepgram", "name": "Deepgram", "available": bool(os.getenv('DEEPGRAM_API_KEY'))},
        {"id": "elevenlabs", "name": "ElevenLabs", "available": bool(os.getenv('ELEVENLABS_API_KEY'))},
        {"id": "cartesia", "name": "Cartesia", "available": bool(os.getenv('CARTESIA_API_KEY'))}
    ]
    return {"success": True, "providers": providers}

@api_router.get("/api/stt-provider")
async def get_user_stt_provider(user_id: str = "default"):
    provider = redis_manager.get(f"user:{user_id}:stt_provider") or "deepgram"
    return {"success": True, "provider": provider}

@api_router.post("/api/stt-provider")
async def set_user_stt_provider(data: ProviderUpdate):
    redis_manager.set(f"user:{data.user_id}:stt_provider", data.provider.lower())
    return {"success": True, "message": f"STT provider set to {data.provider}"}

@api_router.get("/api/tts-providers")
async def get_tts_providers():
    providers = [
        {"id": "elevenlabs", "name": "ElevenLabs", "available": bool(os.getenv('ELEVENLABS_API_KEY'))},
        {"id": "cartesia", "name": "Cartesia", "available": bool(os.getenv('CARTESIA_API_KEY'))},
        {"id": "deepgram", "name": "Deepgram", "available": bool(os.getenv('DEEPGRAM_API_KEY'))}
    ]
    return {"success": True, "providers": providers}

@api_router.get("/api/tts-provider")
async def get_user_tts_provider(user_id: str = "default"):
    provider = redis_manager.get(f"user:{user_id}:tts_provider") or "elevenlabs"
    return {"success": True, "provider": provider}

@api_router.post("/api/tts-provider")
async def set_user_tts_provider(data: ProviderUpdate):
    redis_manager.set(f"user:{data.user_id}:tts_provider", data.provider.lower())
    return {"success": True, "message": f"TTS provider set to {data.provider}"}

app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
