# FastAPI + GCP Architecture: File Map, Function Map, and Validation Guide

This document explains how the current FastAPI microservices map to the reference architecture, which files and functions implement (or stub) each feature, and how to start, test, and validate everything locally and on GCP.

---

## 1. Service Overview and Entry Points

| Service | Entry module | Dockerfile | Port (local) | Cloud Run URL (after deploy) |
|--------|---------------|------------|--------------|------------------------------|
| **voice-gateway-service** | `convonet.voice_gateway_service:app` | `docker/voice-gateway.Dockerfile` | 8000 | `voice-gateway-service-...run.app` |
| **agent-llm-service** | `convonet.agent_llm_service:app` | `docker/agent-llm.Dockerfile` | 8001 | `agent-llm-service-...run.app` |
| **call-center-service** | `convonet.call_center_service:app` | `docker/call-center.Dockerfile` | 8002 | `call-center-service-...run.app` |
| **crm-integration-service** | `convonet.crm_integration_service:app` | `docker/crm-integration.Dockerfile` | 8003 | `crm-integration-service-...run.app` |

All Cloud Run services listen on **port 8080** inside the container (set in each Dockerfile `CMD`).

### 1.1 Single domain: v2.convonetai.com

All four services are exposed under **one domain**, `https://v2.convonetai.com`, using path-based routing (e.g. Google Cloud Load Balancer or API Gateway). Configure the router so:

| Path pattern | Backend Cloud Run service | Purpose |
|--------------|----------------------------|---------|
| `/`, `/call-center`, `/voice_assistant`, `/mortgage_dashboard`, `/agent-monitor`, `/tool-execution`, `/api/*`, `/static/*`, `/call_center/static/*` | **call-center-service** | Landing, call center UI, voice assistant UI, dashboards, APIs, static |
| `/webrtc/*`, `/twilio/*` | **voice-gateway-service** | WebSocket `/webrtc/ws` (no LiveKit), Twilio webhooks |
| `/agent/*`, `/convonet_todo/*` | **agent-llm-service** | `POST /agent/process`, provider APIs (`/convonet_todo/api/llm-providers`, etc.) |
| `/patient/*`, `/meeting/create`, `/case/create`, `/note/create` | **crm-integration-service** | CRM (patient search/create, meeting, case, note) |
| `/health` | Any (or each backend by path) | Health checks; can route `/health` to a single backend or use per-service paths |

With this setup, **no cross-origin env vars** are needed on call-center-service: leave `CONVONET_API_BASE`, `VOICE_ASSISTANT_URL`, and `MORTGAGE_DASHBOARD_URL` unset so the landing page uses same-origin URLs (e.g. `/convonet_todo/api/llm-providers`, `/webrtc/voice-assistant`, `/call-center`).

---

## 2. Voice Gateway Service

**Purpose:** Replaces Flask Socket.IO `/voice` and WebRTC/Twilio webhooks with FastAPI WebSockets and (future) HTTP webhook routes.

### 2.1 Files and Functions

| File | Role |
|------|------|
| `convonet/voice_gateway_service.py` | FastAPI app: health, WebSocket `/webrtc/ws`, message handling. |
| `convonet/schemas.py` | Pydantic models for client↔server WebSocket messages (plan §10.2). |
| `convonet/fastapi_voice_gateway.py` | Alternative router with `/voice-stream` (binary WebSocket); used by hybrid `asgi_main` only. |

**Key functions in `voice_gateway_service.py`:**

| Function | What it does |
|----------|----------------|
| `health_check()` | `GET /health` → `{"status":"ok","service":"voice-gateway"}`. |
| `websocket_endpoint(websocket)` | `WS /webrtc/ws`: accepts connection, assigns `session_id`, loops on `receive_text()`; dispatches by `message["type"]` to authenticate, start_recording, audio_chunk, stop_recording, heartbeat. **STT/TTS/LLM not wired yet** (logging only). |

**Message flow (current):**

- **Client → Server:** JSON with `type` + payload. Parsed via `convonet.schemas`: `AuthMessage`, `StartRecordingMessage`, `AudioChunkMessage`, `StopRecordingMessage`, `HeartbeatMessage`, `TransferRequestMessage`.
- **Server → Client:** `AuthOkMessage`, `ErrorMessage` used; other server message types (e.g. `TranscriptFinalMessage`, `AgentStreamChunkMessage`, `AudioChunkOutMessage`) are defined in `schemas.py` but **not yet sent** by the gateway (to be wired when STT/agent/TTS are integrated).

### 2.2 Mapping to Legacy Code (for future wiring)

| Legacy (Flask/Socket.IO) | Target in voice-gateway-service |
|--------------------------|----------------------------------|
| `convonet/webrtc_voice_server_socketio.py` | WebSocket session handling, STT streaming, agent calls, TTS streaming, transfer. |
| `convonet/routes.py`: `twilio_call_webhook`, `verify_pin_webhook`, `process_audio_webhook`, `transfer_bridge` | Future FastAPI routes: e.g. `POST /twilio/call`, `POST /twilio/verify_pin`, `POST /twilio/process_audio`, `GET/POST /twilio/voice_assistant/transfer_bridge`. |

Twilio routes **are** implemented in `voice_gateway_service.py`: `POST /twilio/call`, `/twilio/verify_pin`, `/twilio/process_audio` (the latter calls agent-llm at `AGENT_LLM_URL/agent/process`).

---

## 3. Agent LLM Service

**Purpose:** Async HTTP API for agent/LLM processing; returns text and optional `transfer_marker`.

### 3.1 Files and Functions

| File | Role |
|------|------|
| `convonet/agent_llm_service.py` | FastAPI app: health, `POST /agent/process`. |
| `convonet/routes.py` | Defines `_run_agent_async()`, `_get_agent_graph()` (real implementation). |
| `convonet/assistant_graph_todo.py` | `get_agent()`, agent graphs (Todo, Mortgage, Healthcare). |
| `convonet/gemini_streaming.py` | `stream_gemini_with_tools()` for Gemini path. |
| `convonet/llm_provider_manager.py` | LLM provider and model selection. |

**Key functions in `agent_llm_service.py`:**

| Function | What it does |
|----------|----------------|
| `health_check()` | `GET /health` → `{"status":"ok","service":"agent-llm-service"}`. |
| `process_agent` | `POST /agent/process`: calls `_run_agent_async()` from `convonet.routes`; returns `AgentResponse` with `response`, `transfer_marker`, `processing_time_ms`. |
| Provider APIs | Under `APIRouter(prefix="/convonet_todo")`: `GET/POST /api/llm-provider(s)`, `GET/POST /api/stt-provider(s)`, `GET/POST /api/tts-provider(s)`; use Redis and `llm_provider_manager`. |

**Request/Response models (in `agent_llm_service.py`):**

- `AgentRequest`: `prompt`, `user_id`, `user_name`, `session_id`, `reset_thread`, `model`, `metadata`.
- `AgentResponse`: `response`, `transfer_marker`, `processing_time_ms`.

**Mapping to legacy:**

- **LangGraph path:** `_run_agent_async()` in `convonet/routes.py` (lines ~1722+) uses `_get_agent_graph()` and invokes the graph; supports Gemini streaming via `stream_gemini_with_tools` from `convonet/gemini_streaming.py`.
- **To complete the service:** Import and call `_run_agent_async` (or a refactored async entry point that uses `get_agent()` / `stream_gemini_with_tools`), then map the string (and any transfer marker) to `AgentResponse`.

---

## 4. Call Center Service

**Purpose:** Serves the JsSIP call center UI and API (agent login, call state, customer profile).

### 4.1 Files and Functions

| File | Role |
|------|------|
| `convonet/call_center_service.py` | FastAPI app: health, `GET /call-center`, `POST /api/agent/login`. |
| `call_center/routes.py` | Full Flask implementation: session, DB, Redis, all `/api/*` and customer endpoints. |
| `call_center/models.py` | SQLAlchemy: Agent, Call, AgentActivity, etc. |
| `call_center/security.py` | Session expiry, PHI audit, etc. |
| `extensions.py` | Flask `db` (SQLAlchemy) used by call_center. |

**Key functions in `call_center_service.py`:**

| Function | What it does |
|----------|----------------|
| `health_check()` | `GET /health` → `{"status":"ok","service":"call-center-service"}`. |
| `landing_page()` | `GET /`: serves `index.html` from `templates` with context (llm/stt/tts providers). |
| `call_center_ui()` | `GET /call-center`: serves `call_center.html` from `call_center/templates` (Jinja2). |
| `update_agent_status()` | `POST /api/agent/status`: logs status; DB not wired. |
| `handle_call_event()` | `POST /api/call/event`: logs call event; DB not wired. |
| `get_customer_profile(phone)` | `GET /api/customer/profile`: returns stub JSON; CRM not called. |

**Mapping to legacy (`call_center/routes.py`):**

| Legacy route | Purpose | FastAPI status |
|--------------|---------|----------------|
| `GET /` | Call center UI (JsSIP) | Placeholder only; real UI is in Flask template. |
| `POST /api/agent/login` | Agent login, session, DB | Stub in FastAPI. |
| `POST /api/agent/logout` | Logout | Not in FastAPI. |
| `POST /api/agent/ready`, `POST /api/agent/not-ready` | Agent state | Not in FastAPI. |
| `POST /api/call/ringing`, `answer`, `drop`, `transfer`, `hold`, `unhold` | Call lifecycle | Not in FastAPI. |
| `GET /api/agent/status` | Agent + active calls | Not in FastAPI. |
| `GET /api/customer/<id>`, `GET /api/customer/data` | Customer popup (Redis + SuiteCRM) | Not in FastAPI. |

To fully port the call center, the FastAPI app would need to mount the same SQLAlchemy/Redis stack, use `call_center/models` and `call_center/security`, and expose the same routes (with async where applicable).

**Landing page (FastAPI/GCP):** `GET /` serves `templates/index.html` with Jinja context for the microservices environment. When all services are on **v2.convonetai.com** (see §1.1), leave these unset so same-origin paths are used. For a different deployment (e.g. each service on its own subdomain), set on call-center-service:

- `CONVONET_API_BASE`: Base URL of agent-llm-service so the landing page’s LLM/STT/TTS provider selectors can call `/convonet_todo/api/*`.
- `VOICE_ASSISTANT_URL`: URL for “Try Voice Assistant”.
- `MORTGAGE_DASHBOARD_URL`: URL for Mortgage Dashboard link.

---

## 5. CRM Integration Service

**Purpose:** Thin FastAPI wrapper around SuiteCRM for contacts/cases/appointments.

### 5.1 Files and Functions

| File | Role |
|------|------|
| `convonet/crm_integration_service.py` | FastAPI app: health, `POST /crm/contact/create`. |
| `convonet/services/suitecrm_client.py` | `SuiteCRMClient`: authenticate, search_patient, create_patient, create_meeting, create_case, create_note. |

**Key functions in `crm_integration_service.py`:**

| Function | What it does |
|----------|----------------|
| `health_check()` | `GET /health`: authenticates with SuiteCRM, returns `crm_auth`. |
| `search_patient(phone)` | `POST /patient/search`: calls `SuiteCRMClient.search_patient`. |
| `create_patient(data)` | `POST /patient/create`: calls `SuiteCRMClient.create_patient`. |
| `create_meeting(data)` | `POST /meeting/create`: calls `SuiteCRMClient.create_meeting`. |
| `create_case(data)` | `POST /case/create`: calls `SuiteCRMClient.create_case`. |
| `create_note(data)` | `POST /note/create`: calls `SuiteCRMClient.create_note`. |

---

## 6. WebSocket Message Schemas (Plan §10.2)

All client/server message types from the plan live in **`convonet/schemas.py`**:

- **Client → Server:** `ClientMessageType` enum; `AuthMessage`, `StartRecordingMessage`, `AudioChunkMessage`, `StopRecordingMessage`, `TransferRequestMessage`, `HeartbeatMessage`.
- **Server → Client:** `ServerMessageType` enum; `AuthOkMessage`, `AuthFailedMessage` (missing in file; only `ErrorMessage`), `StatusMessage`, `TranscriptPartialMessage`, `TranscriptFinalMessage`, `AgentStreamChunkMessage`, `AgentFinalMessage`, `AudioChunkOutMessage`, `TransferInitiatedMessage`, `TransferStatusMessage`, `ErrorMessage`.

**Note:** Schemas use `Field(..., Literal=True)`. In Pydantic v2, the correct way to fix a literal value is usually with `Literal["value"]` in the type annotation (e.g. `type: Literal["authenticate"]`). If you see validation errors on the `type` field, switch to `typing.Literal` and the enum value.

---

## 7. How to Start and Run Locally

### 7.1 Prerequisites

- Python 3.11+, virtualenv recommended.
- Env vars (optional for health-only): `DB_URI`, `REDIS_HOST` for DB/Redis; Cloud Run injects these in GCP.

### 7.2 Run each service (separate terminals)

From repo root:

```bash
# Voice Gateway (port 8000)
uvicorn convonet.voice_gateway_service:app --host 0.0.0.0 --port 8000

# Agent LLM (port 8001)
uvicorn convonet.agent_llm_service:app --host 0.0.0.0 --port 8001

# Call Center (port 8002)
uvicorn convonet.call_center_service:app --host 0.0.0.0 --port 8002

# CRM Integration (port 8003)
uvicorn convonet.crm_integration_service:app --host 0.0.0.0 --port 8003
```

Or run in background:

```bash
uvicorn convonet.voice_gateway_service:app --host 0.0.0.0 --port 8000 &
uvicorn convonet.agent_llm_service:app --host 0.0.0.0 --port 8001 &
uvicorn convonet.call_center_service:app --host 0.0.0.0 --port 8002 &
uvicorn convonet.crm_integration_service:app --host 0.0.0.0 --port 8003 &
```

### 7.3 Hybrid monolith (Flask + FastAPI) with existing Socket.IO

```bash
uvicorn asgi_main:api --host 0.0.0.0 --port 8000 --reload
```

This mounts the FastAPI voice router at `/fastapi` (e.g. `/fastapi/voice-stream`) and the Flask app at `/`.

---

## 8. How to Test and Validate

### 8.1 Health checks (local)

```bash
curl -s http://localhost:8000/health  # voice-gateway
curl -s http://localhost:8001/health  # agent-llm
curl -s http://localhost:8002/health  # call-center
curl -s http://localhost:8003/health  # crm-integration
```

Expected: `{"status":"ok","service":"<service-name>"}`.

### 8.2 Health checks (GCP Cloud Run)

Replace with your Cloud Run URLs:

```bash
curl -s https://voice-gateway-service-XXXXX-uc.a.run.app/health
curl -s https://agent-llm-service-XXXXX-uc.a.run.app/health
curl -s https://call-center-service-XXXXX-uc.a.run.app/health
curl -s https://crm-integration-service-XXXXX-uc.a.run.app/health
```

### 8.3 WebSocket (voice-gateway)

Use a WebSocket client (e.g. `websocat`, or browser JS):

```bash
# Install websocat if needed, then:
websocat ws://localhost:8000/webrtc/ws
```

Send JSON messages:

1. **Authenticate:** `{"type":"authenticate","session_id":"test-1"}`  
   Expect: `{"type":"auth_ok","session_id":"test-1",...}`

2. **Start recording:** `{"type":"start_recording","session_id":"test-1","stt_mode":"streaming","language":"en-US"}`  
   (Server only logs; no STT yet.)

3. **Audio chunk:** `{"type":"audio_chunk","session_id":"test-1","sequence":1,"timestamp_ms":0,"data_b64":"..."}`  
   (Server logs every 50th chunk.)

4. **Heartbeat:** `{"type":"heartbeat","session_id":"test-1","ts_ms":12345}`

5. **Stop recording:** `{"type":"stop_recording","session_id":"test-1"}`

### 8.4 Agent LLM

```bash
curl -s -X POST http://localhost:8001/agent/process \
  -H "Content-Type: application/json" \
  -d '{"session_id":"s1","prompt":"Hello","user_id":"u1"}'
```

Expected: JSON with `response`, `transfer_marker`, `processing_time_ms`. Also: `GET/POST http://localhost:8001/convonet_todo/api/llm-provider(s)` (and stt/tts) for provider management.

### 8.5 Call Center

```bash
curl -s http://localhost:8002/           # landing (index.html)
curl -s http://localhost:8002/call-center # agent desktop UI
curl -s -X POST http://localhost:8002/api/agent/status \
  -H "Content-Type: application/json" -d '{"agent_id":"a1","status":"Available"}'
curl -s "http://localhost:8002/api/customer/profile?phone=+15551234567"
```

### 8.6 CRM

```bash
curl -s -X POST http://localhost:8003/patient/create \
  -H "Content-Type: application/json" \
  -d '{"first_name":"Jane","last_name":"Doe","phone":"+15551234567"}'
curl -s -X POST http://localhost:8003/patient/search -H "Content-Type: application/json" -d '{"phone":"+15551234567"}'
# Also: POST /meeting/create, /case/create, /note/create
```

Expected: JSON with `success` and SuiteCRM result (e.g. `id`).

### 8.7 Docker build (same as CI)

From repo root:

```bash
docker build -f docker/voice-gateway.Dockerfile -t voice-gateway-service .
docker build -f docker/agent-llm.Dockerfile -t agent-llm-service .
docker build -f docker/call-center.Dockerfile -t call-center-service .
docker build -f docker/crm-integration.Dockerfile -t crm-integration-service .
```

Run one:

```bash
docker run -p 8000:8080 -e REDIS_HOST= -e DB_URI= voice-gateway-service
```

Then `curl http://localhost:8000/health`.

---

## 9. Feature Completeness vs Plan

| Area | Implemented | Pending |
|------|--------------|---------|
| **Voice gateway** | WebSocket `/webrtc/ws`, Twilio `/twilio/call`, `verify_pin`, `process_audio` (calls agent-llm), schemas | WebSocket path: real STT/TTS, call agent-llm on final transcript; PIN auth (DB); transfer by extension/marker |
| **Agent LLM** | `POST /agent/process` (real `_run_agent_async`), `/convonet_todo/api/*` provider APIs (LLM/STT/TTS), Redis | Optional: streaming endpoint, auth |
| **Call center** | Health, `GET /` (index), `GET /call-center` (template), static, `/api/agent/status`, `/api/call/event`, `/api/customer/profile` (stub) | Agent login/logout, DB/Redis, real customer profile via CRM service |
| **CRM** | Health (with CRM auth), `POST /patient/search`, `/patient/create`, `/meeting/create`, `/case/create`, `/note/create` | Optional: path prefix `/crm/*`, richer error codes |
| **Infra** | Cloud SQL, Memorystore, Cloud Build, Cloud Run deploy | Load balancer path-based routing, custom domains |

---

## 10. Quick Reference: Where Things Live

- **WebSocket message types and Pydantic models:** `convonet/schemas.py`
- **Voice WebSocket handler and (future) Twilio routes:** `convonet/voice_gateway_service.py`
- **Agent HTTP API:** `convonet/agent_llm_service.py` (`/agent/process` + `/convonet_todo/api/*` provider APIs)
- **Real agent logic:** `convonet/routes.py` (`_run_agent_async`, `_get_agent_graph`), `convonet/assistant_graph_todo.py`, `convonet/gemini_streaming.py`
- **Call center:** `convonet/call_center_service.py` (UI + stub APIs); full logic in `call_center/routes.py`
- **SuiteCRM:** `convonet/services/suitecrm_client.py`; FastAPI wrapper: `convonet/crm_integration_service.py`
- **CI/CD:** `cloudbuild.yaml` (builds and deploys all four services to Cloud Run with `REDIS_HOST`, `DB_URI`).

Using this map you can start all four services locally, hit health and stub endpoints, validate WebSocket message handling, and then incrementally replace mocks with real logic (agent, CRM, call center DB/session, and voice STT/TTS/transfer).
