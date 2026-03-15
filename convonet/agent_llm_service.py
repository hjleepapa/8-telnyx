import asyncio
import logging
import os
import re
import time
import uuid
from urllib.parse import urlparse, urlunparse
from typing import Optional, List, Dict, Any
from fastapi import FastAPI, HTTPException, Body, Query, APIRouter
from pydantic import BaseModel
from convonet.redis_manager import redis_manager
from convonet.llm_provider_manager import get_llm_provider_manager

# Defer routes import to first request so the container can start and listen on PORT quickly (Cloud Run timeout).
# from convonet.routes import _run_agent_async  # imported lazily in process_agent

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

class TransferContext(BaseModel):
    """Context for call-center UI when call is transferred to human agent."""
    conversation_history: List[Dict[str, str]] = []  # [{"role": "user"|"assistant", "content": "..."}]
    user_id: Optional[str] = None
    user_name: Optional[str] = None


class AgentResponse(BaseModel):
    response: str
    transfer_marker: Optional[str] = None
    transfer_context: Optional[TransferContext] = None  # Call history for agent UI when transfer_marker is set
    processing_time_ms: float

class ProviderUpdate(BaseModel):
    user_id: str
    provider: str

# --- Endpoints ---

@app.get("/health")
async def health_check():
    return {"status": "ok", "service": "agent-llm-service"}


def _get_run_agent_async():
    """Lazy import to avoid loading routes (and heavy deps) at startup."""
    from convonet.routes import _run_agent_async
    return _run_agent_async


async def _process_agent_impl(request: AgentRequest) -> AgentResponse:
    logger.info(f"Processing agent request for user {request.user_id} (session: {request.session_id})")
    start_time = time.time()
    try:
        _run_agent_async = _get_run_agent_async()
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
            tc = result.get("transfer_context")
            transfer_context = TransferContext(**tc) if isinstance(tc, dict) and tc else None
            return AgentResponse(
                response=result.get("response", ""),
                transfer_marker=result.get("transfer_marker"),
                transfer_context=transfer_context,
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


# --- Mortgage dashboard API (GET list + GET by id) ---

def _get_mortgage_db_uri() -> Optional[str]:
    """Return DB_URI with optional Render host suffix for DNS resolution."""
    db_uri = os.getenv("DB_URI")
    if not db_uri:
        return None
    suffix = os.getenv("RENDER_POSTGRES_HOST_SUFFIX", "").strip()
    if suffix and not suffix.startswith("."):
        suffix = "." + suffix
    if not suffix:
        return db_uri
    try:
        parsed = urlparse(db_uri)
        host = (parsed.hostname or "").strip()
        if host and "." not in host and re.match(r"dpg-[a-z0-9]+-a", host):
            netloc_parts = []
            if parsed.username is not None:
                netloc_parts.append(f"{parsed.username}:{parsed.password or ''}@" if parsed.password else f"{parsed.username}@")
            netloc_parts.append(host + suffix)
            if parsed.port is not None:
                netloc_parts.append(f":{parsed.port}")
            return urlunparse((parsed.scheme, "".join(netloc_parts), parsed.path or "", "", parsed.query or "", parsed.fragment or ""))
    except Exception as e:
        logger.warning("Mortgage API: could not normalize DB_URI: %s", e)
    return db_uri


def _list_mortgage_applications_sync() -> Dict[str, Any]:
    """Sync: query DB for all mortgage applications (run in executor)."""
    from sqlalchemy import create_engine, func
    from sqlalchemy.orm import sessionmaker
    from convonet.models.mortgage_models import MortgageApplication, MortgageDocument, MortgageDebt

    db_uri = _get_mortgage_db_uri()
    if not db_uri:
        return {"success": False, "error": "DB_URI not configured"}
    engine = create_engine(db_uri, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        applications = db.query(
            MortgageApplication,
            func.count(MortgageDocument.id).label("documents_count"),
        ).outerjoin(
            MortgageDocument, MortgageDocument.application_id == MortgageApplication.id
        ).group_by(MortgageApplication.id).order_by(
            MortgageApplication.created_at.desc()
        ).all()

        result = []
        for app, doc_count in applications:
            debts_count = db.query(MortgageDebt).filter(MortgageDebt.application_id == app.id).count()
            result.append({
                "application_id": str(app.id),
                "user_id": str(app.user_id),
                "status": app.status.value if hasattr(app.status, "value") else str(app.status),
                "credit_score": app.credit_score,
                "dti_ratio": float(app.dti_ratio) if app.dti_ratio else None,
                "monthly_income": float(app.monthly_income) if app.monthly_income else None,
                "monthly_debt": float(app.monthly_debt) if app.monthly_debt else None,
                "down_payment_amount": float(app.down_payment_amount) if app.down_payment_amount else None,
                "total_savings": float(app.total_savings) if app.total_savings else None,
                "completion_percentage": app.get_completion_percentage(),
                "documents_count": doc_count or 0,
                "debts_count": debts_count,
                "created_at": app.created_at.isoformat() if app.created_at else None,
            })
        return {"success": True, "applications": result}
    except Exception as e:
        logger.exception("Error getting mortgage applications")
        return {"success": False, "error": str(e)}
    finally:
        db.close()


def _get_mortgage_application_sync(application_id: str) -> Dict[str, Any]:
    """Sync: query DB for one mortgage application (run in executor)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from uuid import UUID
    from convonet.models.mortgage_models import MortgageApplication, MortgageDocument, MortgageDebt

    db_uri = _get_mortgage_db_uri()
    if not db_uri:
        return {"success": False, "error": "DB_URI not configured"}
    try:
        app_uuid = UUID(application_id)
    except (ValueError, TypeError):
        return {"success": False, "error": "Invalid application_id", "status_code": 404}
    engine = create_engine(db_uri, pool_pre_ping=True)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        application = db.query(MortgageApplication).filter(MortgageApplication.id == app_uuid).first()
        if not application:
            return {"success": False, "error": "Application not found", "status_code": 404}
        documents = db.query(MortgageDocument).filter(MortgageDocument.application_id == application.id).all()
        debts = db.query(MortgageDebt).filter(MortgageDebt.application_id == application.id).all()
        return {
            "success": True,
            "application": {
                "application_id": str(application.id),
                "user_id": str(application.user_id),
                "status": application.status.value if hasattr(application.status, "value") else str(application.status),
                "credit_score": application.credit_score,
                "dti_ratio": float(application.dti_ratio) if application.dti_ratio else None,
                "monthly_income": float(application.monthly_income) if application.monthly_income else None,
                "monthly_debt": float(application.monthly_debt) if application.monthly_debt else None,
                "down_payment_amount": float(application.down_payment_amount) if application.down_payment_amount else None,
                "closing_costs_estimate": float(application.closing_costs_estimate) if application.closing_costs_estimate else None,
                "total_savings": float(application.total_savings) if application.total_savings else None,
                "loan_type": application.loan_type,
                "loan_amount": float(application.loan_amount) if application.loan_amount else None,
                "property_value": float(application.property_value) if application.property_value else None,
                "completion_percentage": application.get_completion_percentage(),
                "financial_review_completed": application.financial_review_completed,
                "document_collection_completed": application.document_collection_completed,
                "document_verification_completed": application.document_verification_completed,
                "documents_count": len(documents),
                "debts_count": len(debts),
                "documents": [
                    {
                        "document_id": str(doc.id),
                        "document_type": doc.document_type.value if hasattr(doc.document_type, "value") else str(doc.document_type),
                        "document_name": doc.document_name,
                        "status": doc.status.value if hasattr(doc.status, "value") else str(doc.status),
                        "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                        "verified_at": doc.verified_at.isoformat() if doc.verified_at else None,
                    }
                    for doc in documents
                ],
                "debts": [
                    {
                        "debt_id": str(d.id),
                        "debt_type": d.debt_type,
                        "creditor_name": d.creditor_name,
                        "monthly_payment": float(d.monthly_payment),
                        "outstanding_balance": float(d.outstanding_balance) if d.outstanding_balance else None,
                        "interest_rate": float(d.interest_rate) if d.interest_rate else None,
                    }
                    for d in debts
                ],
                "created_at": application.created_at.isoformat() if application.created_at else None,
                "updated_at": application.updated_at.isoformat() if application.updated_at else None,
            },
        }
    except Exception as e:
        logger.exception("Error getting mortgage application %s", application_id)
        return {"success": False, "error": str(e)}
    finally:
        db.close()


@api_router.get("/api/mortgage/applications")
async def get_mortgage_applications():
    """List all mortgage applications for the dashboard."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _list_mortgage_applications_sync)
    if not result.get("success"):
        raise HTTPException(
            status_code=500 if result.get("error") != "DB_URI not configured" else 503,
            detail=result.get("error", "Unknown error"),
        )
    return result


@api_router.get("/api/mortgage/applications/{application_id}")
async def get_mortgage_application(application_id: str):
    """Get one mortgage application by ID."""
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _get_mortgage_application_sync, application_id)
    if not result.get("success"):
        status = result.get("status_code", 500)
        if status == 404:
            raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
        raise HTTPException(status_code=500, detail=result.get("error", "Unknown error"))
    return result


app.include_router(api_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
