# Convonet Voice AI Productivity System

> **Enterprise-grade voice AI assistant with multi-LLM provider support, team collaboration, and intelligent call transfer**

[![Flask](https://img.shields.io/badge/Flask-2.3+-blue.svg)](https://flask.palletsprojects.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-green.svg)](https://langchain-ai.github.io/langgraph/)
[![Python](https://img.shields.io/badge/Python-3.12+-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 🎯 Overview

Convonet is a production-ready voice AI productivity system that combines **LangGraph AI agents**, **team collaboration**, **voice interaction**, and **intelligent call center integration**. Built for enterprise use, it supports **three major LLM providers** (Claude, Gemini, OpenAI) with seamless switching capabilities.

### Key Features

- 🤖 **Multi-LLM Provider Support**: Switch between Claude (Anthropic), Gemini (Google), and OpenAI
- 🎤 **Voice Interfaces**: Twilio phone calls and WebRTC browser-based voice
- 👥 **Team Collaboration**: Multi-tenant team management with role-based access
- 🔄 **Call Transfer**: Intelligent AI-to-human agent transfer via FusionPBX
- 🛠️ **38 MCP Tools**: Database operations, calendar sync, team management
- 📊 **Production Monitoring**: Sentry error tracking and performance analytics
- ⚡ **Optimized Timeouts**: 15s/20s/25s timeouts for reliable operation

---

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL database
- Redis (for session management)
- API keys for at least one LLM provider (see below)

### Installation

```bash
# Clone the repository
git clone https://github.com/hjleepapa/convonet-anthropic.git
cd convonet-anthropic

# Install dependencies
pip install -r requirements.txt

# Set up environment variables (see Configuration section)
cp .env.example .env
# Edit .env with your API keys and configuration
```

### Configuration

#### Required Environment Variables

```bash
# Database
DB_URI=postgresql://user:password@host:5432/dbname

# Redis
REDIS_URL=redis://localhost:6379

# JWT Authentication
JWT_SECRET_KEY=your-super-secret-jwt-key

# At least ONE LLM provider (see Multi-LLM Provider Support below)
```

#### Optional Environment Variables

```bash
# Google Calendar OAuth2
GOOGLE_OAUTH2_TOKEN_B64=base64_encoded_token
GOOGLE_CLIENT_ID=your_client_id
GOOGLE_CLIENT_SECRET=your_client_secret

# Twilio Voice
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_PHONE_NUMBER=+1234567890

# Deepgram STT/TTS
DEEPGRAM_API_KEY=your_deepgram_api_key

# FusionPBX Call Transfer
FREEPBX_DOMAIN=34.26.59.14

# Sentry Monitoring
SENTRY_DSN=your_sentry_dsn
```

### Run the Application

```bash
# Development
python app.py

# Production (with Gunicorn)
gunicorn --worker-class eventlet -w 1 --threads 4 --bind 0.0.0.0:5000 passenger_wsgi:app
```

---

## 🤖 Multi-LLM Provider Support

Convonet supports **three major LLM providers** with seamless switching capabilities. You can use one, two, or all three providers simultaneously.

### Supported Providers

| Provider | Model | Default Model | Best For |
|----------|-------|---------------|----------|
| **Claude (Anthropic)** | Claude Sonnet 4 | `claude-sonnet-4-20250514` | Best tool calling, complex reasoning |
| **Gemini (Google)** | Gemini 2.0 Flash | `gemini-2.0-flash` | Cost-effective, fast responses |
| **OpenAI** | GPT-4o | `gpt-4o` | General purpose, high accuracy |

### Configuration

#### Claude (Anthropic)

```bash
ANTHROPIC_API_KEY=sk-ant-your-key-here
ANTHROPIC_MODEL=claude-sonnet-4-20250514  # Optional
```

**Recommended for**: Complex tool calling, multi-step reasoning, production workloads

#### Gemini (Google)

```bash
GOOGLE_API_KEY=your-google-api-key
GOOGLE_MODEL=gemini-2.0-flash  # Optional, defaults to gemini-2.0-flash
```

**Available Gemini Models**:
- `gemini-2.0-flash` - **Default**: Best price-performance, well-rounded capabilities
- `gemini-1.5-pro` - Most powerful, best for multimodal and agentic tasks
- `gemini-1.5-flash` - High-speed, cost-efficient
- `gemini-2.0-flash-lite` - Cost-efficient, 1M token context window

**Recommended for**: Cost-effective operations, fast responses, high-volume usage

#### OpenAI

```bash
OPENAI_API_KEY=sk-your-openai-key
OPENAI_MODEL=gpt-4o  # Optional, defaults to gpt-4o
```

**Recommended for**: General purpose tasks, high accuracy requirements

### Provider Selection

#### Via Web UI

1. Navigate to the homepage
2. Click on your preferred provider in the **"🤖 Select LLM Provider"** section
3. Your selection is automatically saved and used for all future conversations

#### Via API

```bash
# Get available providers
GET /anthropic/convonet_todo/api/llm-providers

# Set user provider preference
POST /anthropic/convonet_todo/api/llm-provider
{
  "user_id": "user-uuid",
  "provider": "claude"  # or "gemini" or "openai"
}
```

#### Via Environment Variable

```bash
# Set global default
LLM_PROVIDER=claude  # or "gemini" or "openai"
```

### Provider Selection Priority

The system uses the following priority order:

1. **User-specific preference** (stored in Redis per user)
2. **Global default** (stored in Redis for 'default' user)
3. **Environment variable** (`LLM_PROVIDER`)
4. **Fallback to Claude** (if none specified)

### Provider-Specific Features

#### Claude (Anthropic)
- ✅ Excellent tool calling capabilities
- ✅ Strong reasoning and multi-step problem solving
- ✅ Production-grade reliability
- ✅ Optimized timeout: 15s for execution

#### Gemini (Google)
- ✅ Cost-effective pricing
- ✅ Fast response times
- ✅ Tool limiting support (configurable via `GEMINI_MAX_TOOLS`)
- ✅ Optimized timeout: 12s for execution
- ⚠️ Requires tool binding (can be skipped with `SKIP_GEMINI_TOOL_BINDING=true`)

#### OpenAI
- ✅ High accuracy
- ✅ General purpose excellence
- ✅ Optimized timeout: 15s for execution

### Switching Providers

The system automatically:
- Clears agent graph cache when provider changes
- Reinitializes with the new provider's model
- Maintains conversation context across switches
- Handles provider-specific optimizations

---

## 🏗️ Architecture

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                    Frontend Layer                            │
├─────────────────────────────────────────────────────────────┤
│  Team Dashboard  │  WebRTC Voice  │  Twilio Phone Interface │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                  Core Processing Layer                       │
├─────────────────────────────────────────────────────────────┤
│  LangGraph Agent  │  MCP Tools (38)  │  Call Transfer      │
│  (Multi-LLM)     │  Team API         │  Sentry Monitoring   │
└─────────────────────────────────────────────────────────────┘
                            │
┌─────────────────────────────────────────────────────────────┐
│                 External Services Layer                       │
├─────────────────────────────────────────────────────────────┤
│  Claude/Gemini/OpenAI  │  PostgreSQL  │  Google Calendar    │
│  Deepgram STT/TTS      │  Redis       │  FusionPBX          │
└─────────────────────────────────────────────────────────────┘
```

### Technology Stack

- **Backend**: Flask, Flask-SocketIO, SQLAlchemy
- **AI Framework**: LangGraph, LangChain
- **LLM Providers**: Claude (Anthropic), Gemini (Google), OpenAI
- **Voice**: Twilio Voice API, WebRTC, Deepgram STT/TTS
- **Database**: PostgreSQL (multi-tenant)
- **Cache**: Redis (sessions, audio buffers)
- **Monitoring**: Sentry.io
- **Deployment**: Render.com, Gunicorn + Eventlet

---

## 📚 Documentation

Comprehensive documentation is available in the [`docs/`](docs/) folder:

- **[LLM Provider Selection Guide](docs/LLM_PROVIDER_SELECTION_GUIDE.md)** - Detailed guide on using multiple LLM providers
- **[Deployment Guide](docs/RENDER_DEPLOYMENT.md)** - Production deployment instructions
- **[WebRTC Voice Guide](docs/WEBRTC_VOICE_GUIDE.md)** - Browser-based voice interface
- **[FusionPBX Integration](docs/FUSIONPBX_GUIDE.md)** - Call transfer setup
- **[Team Management Guide](docs/TEAM_MANAGEMENT_GUIDE.md)** - Team collaboration features
- **[Troubleshooting](docs/TRANSFER_TROUBLESHOOTING.md)** - Common issues and solutions

---

## 🎤 Voice Interfaces

### Twilio Phone Integration

Call your Twilio number and interact via voice:

```
User: "Create a high priority todo to review the quarterly report"
AI: "I've created a high priority todo for reviewing the quarterly report."
```

**Features**:
- Speech-to-text via Twilio
- Text-to-speech via Amazon Polly.Amy
- Barge-in capability (interrupt AI)
- 10s speech timeout
- 15s agent processing timeout

### WebRTC Browser Voice

Access the voice assistant directly from your browser:

```
URL: /anthropic/webrtc/voice-assistant
```

**Features**:
- Browser-based audio recording (WebRTC)
- Real-time audio streaming via Socket.IO
- Deepgram STT transcription
- Redis audio buffer management
- Deepgram TTS responses
- PIN authentication

---

## 👥 Team Collaboration

### Features

- **Multi-tenant Architecture**: Teams, users, and todos with proper isolation
- **Role-Based Access**: Owner, Admin, Member, Viewer roles
- **Team Todos**: Assign tasks to teams and specific members
- **JWT Authentication**: Secure token-based authentication
- **Team Dashboard**: Web interface for team management

### Team Roles

| Role | Permissions |
|------|-------------|
| **Owner** | Full control, can delete team |
| **Admin** | Manage members and todos |
| **Member** | Create and edit own todos |
| **Viewer** | Read-only access |

---

## 🛠️ MCP Tools (38 Tools)

The system includes 38 Model Context Protocol (MCP) tools:

### Tool Categories

- **Todo Management** (5 tools): Create, get, update, complete, delete todos
- **Team Tools** (8 tools): Team creation, member management, role changes
- **Reminders** (4 tools): Create, get, update, delete reminders
- **Calendar Events** (6 tools): Calendar operations with Google Calendar sync
- **Call Transfer** (2 tools): Transfer to FusionPBX agents
- **Database Tools** (13 tools): Various database operations

### Tool Execution

- **Timeout**: 20s per tool execution
- **Error Handling**: Graceful failure recovery
- **Streaming**: Real-time execution updates
- **Provider Support**: All tools work with Claude, Gemini, and OpenAI

---

## 🔄 Call Transfer

Intelligent AI-to-human agent transfer:

1. User requests transfer via voice or tool
2. LangGraph detects transfer intent
3. Twilio API bridges call to FusionPBX
4. Agent dashboard receives call with user info
5. Live conversation begins

**Configuration**:
- FusionPBX Extension: 2001
- SIP/WSS connectivity
- Google Cloud VM deployment
- JsSIP browser softphone

---

## 📊 Monitoring & Observability

### Sentry Integration

- Real-time error tracking
- Performance monitoring (agent processing time)
- User context & session tracking
- Timeout & thread reset tracking
- Production-grade observability

### Performance Metrics

- Agent processing time: Tracked per request
- Tool execution time: Monitored per tool
- Timeout rates: Tracked and optimized
- Error rates: Real-time alerting

---

## 🚀 Deployment

### Render.com Deployment

The project includes `render.yaml` for automatic deployment:

```yaml
services:
  - type: web
    name: convonet
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: gunicorn --worker-class eventlet -w 1 --threads 4 --bind 0.0.0.0:$PORT passenger_wsgi:app
    envVars:
      - key: DB_URI
        sync: false
      - key: REDIS_URL
        sync: false
      # ... other environment variables
```

### Production Configuration

- **Worker Class**: Eventlet (for async I/O)
- **Workers**: 1 worker with 4 threads
- **Timeout**: 60s (Gunicorn)
- **Auto-scaling**: Configured via Render.com

---

## 📖 Usage Examples

### Voice Commands

**Personal Productivity**:
- "Create a high priority todo to review the quarterly report"
- "Add a reminder to call the dentist tomorrow at 2 PM"
- "Schedule a meeting for next Friday from 2 to 3 PM"
- "Show me all my pending todos"

**Team Collaboration**:
- "Create a hackathon team"
- "What teams are available?"
- "Who are the members of the development team?"
- "Create a high priority todo for the development team"
- "Add admin@convonet.com to the hackathon team as owner"

**Call Transfer**:
- "Transfer me to an agent"
- "I need to speak with support"
- "Connect me to sales"

### API Examples

See the [API Reference](docs/) for detailed endpoint documentation.

---

## 🔧 Development

### Project Structure

```
convonet/
├── routes.py              # Flask routes & Twilio webhooks
├── assistant_graph_todo.py # LangGraph agent (multi-LLM)
├── llm_provider_manager.py # LLM provider management
├── webrtc_voice_server.py  # WebRTC voice assistant
├── models/                 # Database models
├── api_routes/             # RESTful API endpoints
├── security/               # JWT authentication
└── mcps/                   # MCP tool servers
```

### Running Tests

```bash
# Run tests (if available)
pytest tests/

# Lint code
flake8 convonet/

# Type checking
mypy convonet/
```

---

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

---

## 🙏 Acknowledgments

- **LangGraph** - AI agent orchestration
- **LangChain** - LLM integration framework
- **Anthropic** - Claude API
- **Google** - Gemini API
- **OpenAI** - GPT-4 API
- **Twilio** - Voice API
- **Deepgram** - Speech-to-text and text-to-speech
- **FusionPBX** - Call center integration

---

## 📞 Support

For issues, questions, or contributions:

- **GitHub Issues**: [Create an issue](https://github.com/hjleepapa/convonet-anthropic/issues)
- **Documentation**: See [`docs/`](docs/) folder
- **Email**: admin@convonet-anthropic.com

---

## 🎯 Roadmap

- [ ] Additional LLM provider support (e.g., Mistral, Cohere)
- [ ] Enhanced tool execution monitoring
- [ ] Multi-language support
- [ ] Advanced analytics dashboard
- [ ] Mobile app integration

---

**Built with ❤️ for enterprise voice AI productivity**
