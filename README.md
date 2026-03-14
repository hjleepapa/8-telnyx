# Convonet Voice AI Productivity System

> **Enterprise-grade voice AI assistant with multi-LLM provider support, team collaboration, and intelligent call transfer**

[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)](https://fastapi.tiangolo.com/)
[![Google Cloud Run](https://img.shields.io/badge/Google%20Cloud-Run-4285F4.svg)](https://cloud.run/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green.svg)](https://langchain-ai.github.io/langgraph/)
[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 🎯 Overview

Convonet is a production-ready voice AI productivity system that combines **LangGraph AI agents**, **team collaboration**, **voice interaction**, and **intelligent call center integration**. It runs as **FastAPI microservices on Google Cloud Run**, with support for **three major LLM providers** (Claude, Gemini, OpenAI) and intent-based routing to **domain agents** (Productivity, Mortgage, Healthcare).

### Key Features

- 🤖 **Multi-LLM Provider Support**: Switch between Claude (Anthropic), Gemini (Google), and OpenAI
- 🎤 **Voice Interfaces**: WebRTC/FastAPI WebSocket (voice-gateway), Twilio phone; streaming STT/TTS (Deepgram, Cartesia, ElevenLabs)
- 🏠 **Domain-Specific Agents**: Productivity (todos, calendar, reminders), Mortgage, Healthcare with sticky context
- 👥 **Team Collaboration**: Multi-tenant team management with role-based access
- 🔄 **Call Transfer**: AI-to-human agent transfer via Twilio/FusionPBX
- 🛠️ **MCP Tools**: Todos, calendar, teams, reminders, mortgage, healthcare, call transfer
- 📊 **Agent Monitor**: Voice response timing and per-tool metrics
- ☁️ **Deployment**: Google Cloud Run (scale-to-zero), path-based routing on a single domain

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- PostgreSQL (e.g. Render.com) and Redis (e.g. Redis Cloud) for agent/session state
- API keys for at least one LLM provider and for STT/TTS (see [Configuration](#configuration))
- Google Cloud project (for Cloud Run deployment)

### Installation

```bash
# Clone the repository
git clone https://github.com/hjleepapa/7-gcconvonet.git
cd 7-gcconvonet

# Install dependencies
pip install -r requirements.txt

# Set up environment variables (see Configuration)
cp .env.cloudrun.example .env.cloudrun
# Edit .env.cloudrun with your API keys (do not commit)
```

### Configuration

Environment variables are **not** baked into the build. Set them per service in **Cloud Run** (Console or `gcloud run services update`); they persist across rebuilds. For the full list of variable **names**, see [`.env.cloudrun.example`](.env.cloudrun.example) and [**docs/CLOUD_RUN_ENV.md**](docs/CLOUD_RUN_ENV.md).

#### Required (per service)

- **Redis**: `REDIS_HOST`, `REDIS_PORT`, `REDIS_PASSWORD`, `REDIS_DB=0`
- **Postgres**: `DB_URI` (e.g. Render connection string)
- **LLM** (agent-llm-service): At least one of `ANTHROPIC_API_KEY`, `GOOGLE_API_KEY`, `OPENAI_API_KEY`
- **Voice** (voice-gateway): `AGENT_LLM_URL` (agent-llm Cloud Run URL), `DEEPGRAM_API_KEY` (STT/TTS), Twilio vars if using phone

#### Optional

- **SuiteCRM** (crm-integration): `SUITECRM_BASE_URL`, `SUITECRM_CLIENT_ID`, `SUITECRM_CLIENT_SECRET`, `SUITECRM_USERNAME`, `SUITECRM_PASSWORD`
- **Other STT/TTS**: `ELEVENLABS_API_KEY`, `CARTESIA_API_KEY`, etc. (see [CLOUD_RUN_ENV.md](docs/CLOUD_RUN_ENV.md))

### Run Locally (all four services)

```bash
# Terminal 1 – voice-gateway (port 8000)
uvicorn convonet.voice_gateway_service:app --reload --port 8000

# Terminal 2 – agent-llm (port 8001)
uvicorn convonet.agent_llm_service:app --reload --port 8001

# Terminal 3 – call-center (port 8002)
uvicorn convonet.call_center_service:app --reload --port 8002

# Terminal 4 – crm-integration (port 8003)
uvicorn convonet.crm_integration_service:app --reload --port 8003
```

Then open the call-center UI at `http://localhost:8002/` (landing). For voice, point the Voice Assistant UI at `ws://localhost:8000/webrtc/ws` (or use the voice-gateway port you set).

### Deploy to Google Cloud Run

```bash
# Deploy all four services
gcloud builds submit --config cloudbuild.yaml .

# Deploy only voice-gateway + call-center
gcloud builds submit --config cloudbuild-voice-callcenter.yaml .
```

Set env vars once per service in Cloud Run (Console → service → Edit & deploy new revision → Variables & secrets). See [docs/CLOUD_RUN_ENV.md](docs/CLOUD_RUN_ENV.md).

---

## 🏗️ Architecture (GCP)

### Microservices

| Service | Purpose | Main entry |
|--------|---------|------------|
| **voice-gateway-service** | WebSocket `/webrtc/ws` (STT→agent→TTS), Twilio webhooks | `convonet/voice_gateway_service.py` |
| **agent-llm-service** | LangGraph agent, `POST /agent/process`, provider APIs | `convonet/agent_llm_service.py` |
| **call-center-service** | Landing, call center UI, voice/mortgage/agent-monitor/tool-execution pages | `convonet/call_center_service.py` |
| **crm-integration-service** | SuiteCRM, patient/meeting/case/note APIs | `convonet/crm_integration_service.py` |

All services listen on **port 8080** in the container. They can be exposed under a **single domain** (e.g. `https://v2.convonetai.com`) with path-based routing.

### Path routing (single domain)

| Path pattern | Backend service |
|--------------|-----------------|
| `/`, `/call-center`, `/voice_assistant`, `/voice-assistant`, `/mortgage_dashboard`, `/agent-monitor`, `/tool-execution`, `/api/*`, `/static/*` | call-center-service |
| `/webrtc/*`, `/twilio/*` | voice-gateway-service |
| `/agent/*`, `/convonet_todo/*` | agent-llm-service |
| `/patient/*`, `/meeting/create`, `/case/create`, `/note/create` | crm-integration-service |

### Technology Stack

- **Backend**: FastAPI (microservices), LangGraph, LangChain
- **LLM**: Claude (Anthropic), Gemini (Google), OpenAI
- **Voice**: WebRTC/FastAPI WebSocket (no LiveKit on GCP), Twilio; Deepgram/Cartesia/ElevenLabs STT/TTS
- **Data**: PostgreSQL (e.g. Render), Redis (e.g. Redis Cloud)
- **Deployment**: Google Cloud Run, Artifact Registry, Cloud Build

---

## 📚 Documentation

- **[FastAPI + GCP Architecture & Validation](docs/FASTAPI_GCP_ARCHITECTURE_AND_VALIDATION.md)** – Service map, intent routing, local run, deploy commands
- **[Cloud Run env vars & API keys](docs/CLOUD_RUN_ENV.md)** – What to set per service, persistence, Secret Manager
- **[LLM Provider Selection Guide](docs/LLM_PROVIDER_SELECTION_GUIDE.md)** – Multi-LLM usage
- **[FusionPBX / SIP](docs/FUSIONPBX_GUIDE.md)** – Call transfer
- **[Team Management](docs/TEAM_MANAGEMENT_GUIDE.md)** – Team collaboration

### Key URLs (single domain, e.g. v2.convonetai.com)

| Feature | Path |
|---------|------|
| Landing | `/` |
| Voice Assistant | `/voice_assistant` or `/voice-assistant` |
| Call Center | `/call-center` |
| Mortgage Dashboard | `/mortgage_dashboard` |
| Agent Monitor | `/agent-monitor` |
| Tool Execution | `/tool-execution` |

---

## 🤖 Multi-LLM Provider Support

Convonet supports **Claude**, **Gemini**, and **OpenAI** with per-user preference (stored in Redis).

### Supported Providers

| Provider | Example model | Best for |
|----------|----------------|----------|
| **Claude (Anthropic)** | `claude-sonnet-4-20250514` | Tool calling, reasoning |
| **Gemini (Google)** | `gemini-2.0-flash` | Cost-effective, fast |
| **OpenAI** | `gpt-4o` | General purpose |

### Configuration (env vars on agent-llm-service)

```bash
ANTHROPIC_API_KEY=sk-ant-...
GOOGLE_API_KEY=...
OPENAI_API_KEY=sk-...
```

Provider selection: **Web UI** (landing page), **API** (`GET/POST /convonet_todo/api/llm-provider(s)`), or Redis default. See [docs/LLM_PROVIDER_SELECTION_GUIDE.md](docs/LLM_PROVIDER_SELECTION_GUIDE.md).

---

## 🎤 Voice

### WebRTC / FastAPI WebSocket (voice-gateway)

- **URL**: Connect to `/webrtc/ws` (same origin as the site when using single-domain routing).
- **Flow**: Browser captures mic → sends `audio_chunk` + `stop_recording` → gateway runs **STT (Deepgram) → agent-llm HTTP → TTS (Deepgram)** → returns transcript, agent text, and audio to the client.
- **No LiveKit** on GCP; voice is WebRTC-style via FastAPI WebSocket.

### Twilio phone

- Use Twilio webhooks (`/twilio/call`, `/twilio/verify_pin`, `/twilio/process_audio`) on voice-gateway-service.
- Set `TWILIO_*` and `AGENT_LLM_URL` on the voice-gateway service.

---

## 🏠 Domain-Specific Agents

Intent is derived from the **prompt text** (and optional sticky context in Redis):

| Agent | When used | Tools (examples) |
|-------|-----------|-------------------|
| **Productivity (todo)** | Default when no mortgage/healthcare keywords | get_todos, get_reminders, get_calendar_events, create_todo, teams, transfer |
| **Mortgage** | Keywords e.g. "mortgage", "apply for the mortgage" | create_mortgage_application, get_mortgage_application_status, DTI, documents |
| **Healthcare** | Healthcare keywords (claims, coverage, eligibility, etc.) | Healthcare MCP tools |

Priority: **healthcare > mortgage > todo**. Sticky context keeps the user in mortgage/healthcare for follow-up turns. See [docs/FASTAPI_GCP_ARCHITECTURE_AND_VALIDATION.md](docs/FASTAPI_GCP_ARCHITECTURE_AND_VALIDATION.md) §3.2.

---

## 👥 Team Collaboration

- Multi-tenant teams, roles (Owner, Admin, Member, Viewer), team todos.
- JWT auth; team and provider APIs exposed via agent-llm and call-center where applicable.

---

## 🔄 Call Transfer

- Transfer intent detected by the agent; Twilio/FusionPBX used to bridge to a human agent.
- Configure `FREEPBX_DOMAIN`, Twilio, and voice-gateway env vars. See [docs/FUSIONPBX_GUIDE.md](docs/FUSIONPBX_GUIDE.md).

---

## 📊 Monitoring

- **Agent Monitor**: `/agent-monitor` (call-center-service) – voice timing, tool execution.
- **Cloud Run logs**: Per-service logs in GCP Console or `gcloud run services logs read SERVICE --region=us-central1`.
- **Sentry**: Optional; configure `SENTRY_DSN` if used.

---

## 🔧 Development

### Project structure (relevant to GCP)

```
convonet/
├── voice_gateway_service.py   # WebSocket + Twilio, STT→agent→TTS pipeline
├── agent_llm_service.py       # POST /agent/process, provider APIs
├── call_center_service.py     # Landing, call center, voice/mortgage/agent-monitor pages
├── crm_integration_service.py # SuiteCRM APIs
├── routes.py                  # _run_agent_async, intent, agent graph
├── assistant_graph_todo.py    # Todo/Mortgage/Healthcare agents
├── mortgage_intent_detection.py
├── healthcare_intent_detection.py
├── deepgram/                  # STT/TTS
├── mcps/                      # MCP tool servers (db_todo, db_mortgage, etc.)
└── ...
docker/                        # Dockerfiles per service
cloudbuild.yaml                # Build + deploy all four services
cloudbuild-voice-callcenter.yaml # Deploy voice-gateway + call-center only
```

### Tests and lint

```bash
pytest tests/
flake8 convonet/
```

---

## 📄 License

MIT License – see the [LICENSE](LICENSE) file.

---

## 🙏 Acknowledgments

- **FastAPI** – API framework  
- **LangGraph / LangChain** – Agent orchestration  
- **Anthropic, Google, OpenAI** – LLM APIs  
- **Twilio** – Voice API  
- **Deepgram, ElevenLabs, Cartesia** – STT/TTS  
- **Google Cloud Run** – Serverless containers  
- **FusionPBX** – Call center integration  

---

**Built for enterprise voice AI productivity on Google Cloud**
