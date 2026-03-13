import logging
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException, Body
from pydantic import BaseModel
from convonet.services.suitecrm_client import SuiteCRMClient
import os

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("crm-integration-service")

app = FastAPI(title="CRM Integration Service")

# Singleton client instance
_client: Optional[SuiteCRMClient] = None

def get_client() -> SuiteCRMClient:
    global _client
    if _client is None:
        _client = SuiteCRMClient()
    return _client

class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    phone: str
    additional_attributes: Optional[Dict[str, Any]] = None

class MeetingCreate(BaseModel):
    patient_id: str
    subject: str
    date_start: str
    duration_minutes: int = 30

class CaseCreate(BaseModel):
    patient_id: str
    subject: str
    description: str
    priority: str = "P3"

class NoteCreate(BaseModel):
    patient_id: str
    subject: str
    content: str

@app.get("/health")
async def health_check():
    client = get_client()
    auth_ok = client.authenticate()
    return {
        "status": "ok", 
        "service": "crm-integration-service",
        "crm_auth": auth_ok
    }

@app.post("/patient/search")
async def search_patient(phone: str = Body(..., embed=True)):
    client = get_client()
    result = client.search_patient(phone)
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Search failed"))
    return result

@app.post("/patient/create")
async def create_patient(data: PatientCreate):
    client = get_client()
    kwargs = data.additional_attributes or {}
    result = client.create_patient(
        first_name=data.first_name,
        last_name=data.last_name,
        phone=data.phone,
        **kwargs
    )
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Patient creation failed"))
    return result

@app.post("/meeting/create")
async def create_meeting(data: MeetingCreate):
    client = get_client()
    result = client.create_meeting(
        patient_id=data.patient_id,
        subject=data.subject,
        date_start=data.date_start,
        duration_minutes=data.duration_minutes
    )
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Meeting creation failed"))
    return result

@app.post("/case/create")
async def create_case(data: CaseCreate):
    client = get_client()
    result = client.create_case(
        patient_id=data.patient_id,
        subject=data.subject,
        description=data.description,
        priority=data.priority
    )
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Case creation failed"))
    return result

@app.post("/note/create")
async def create_note(data: NoteCreate):
    client = get_client()
    result = client.create_note(
        patient_id=data.patient_id,
        subject=data.subject,
        content=data.content
    )
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Note creation failed"))
    return result

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
