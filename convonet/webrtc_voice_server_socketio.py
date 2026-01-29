"""
WebRTC Voice Assistant Server
Provides high-quality audio streaming and real-time speech recognition
"""

import asyncio
import html
import json
import os
import base64
import time
import re
import threading
import struct
from typing import Optional
from uuid import UUID
from urllib.parse import quote
from flask import Blueprint, render_template, request, jsonify, Response
from flask_socketio import SocketIO, emit, join_room, leave_room
import requests

# Apply nest_asyncio to allow nested event loops (needed for eventlet compatibility)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass  # nest_asyncio not available, may cause issues with eventlet
# Note: OpenAI import removed - using Claude LLM and Deepgram TTS
from convonet.assistant_graph_todo import get_agent
from convonet.state import AgentState
from convonet.voice_intent_utils import has_transfer_intent
from langchain_core.messages import HumanMessage
from twilio.rest import Client

# Deepgram WebRTC integration
from deepgram_webrtc_integration import transcribe_audio_with_deepgram_webrtc, get_deepgram_webrtc_info
from deepgram_service import get_deepgram_service

# Deepgram streaming SDK (async)
try:
    from deepgram import AsyncDeepgramClient
    from deepgram.core.events import EventType
    from deepgram.extensions.types.sockets import (
        ListenV2MediaMessage,
        ListenV2ControlMessage,
        SpeakV1TextMessage,
        SpeakV1ControlMessage,
        SpeakV1SocketClientResponse,
    )
    DEEPGRAM_STREAMING_AVAILABLE = True
except Exception as e:
    print(f"⚠️ Deepgram streaming SDK not available: {e}")
    DEEPGRAM_STREAMING_AVAILABLE = False

# ElevenLabs integration
try:
    from convonet.elevenlabs_service import get_elevenlabs_service, EmotionType
    from convonet.voice_preferences import get_voice_preferences
    from convonet.emotion_detection import get_emotion_detector
    ELEVENLABS_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ ElevenLabs not available: {e}")
    ELEVENLABS_AVAILABLE = False

# Import the blueprint (optional - not used in this module)
# from convonet.routes import convonet_todo_bp

# Sentry integration for monitoring Redis interactions and errors
try:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    from sentry_sdk.integrations.socketio import SocketIOIntegration
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False
# Optional Redis imports - app should work without them
try:
    from convonet.redis_manager import redis_manager, create_session, get_session, update_session, delete_session
    REDIS_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Redis not available: {e}")
    REDIS_AVAILABLE = False
    # Create dummy functions for fallback
    class DummyRedisManager:
        def is_available(self):
            return False
    redis_manager = DummyRedisManager()
    def create_session(*args, **kwargs):
        return False
    def get_session(*args, **kwargs):
        return None
    def update_session(*args, **kwargs):
        return False
    def delete_session(*args, **kwargs):
        return False

# Optional test PIN support (disabled by default unless explicitly enabled)
ENABLE_TEST_PIN = os.getenv('ENABLE_TEST_PIN', 'false').lower() == 'true'
TEST_VOICE_PIN = os.getenv('TEST_VOICE_PIN', '1234')

# Streaming STT/TTS flags (enable full-duplex low-latency pipeline)
STREAMING_STT_ENABLED = os.getenv('STREAMING_STT_ENABLED', 'true').lower() == 'true'
STREAMING_TTS_ENABLED = os.getenv('STREAMING_TTS_ENABLED', 'true').lower() == 'true'
STREAMING_STT_ENDPOINTING_MS = int(os.getenv('STREAMING_STT_ENDPOINTING_MS', '300'))
STREAMING_STT_MODEL = os.getenv('STREAMING_STT_MODEL', 'nova-2')
STREAMING_TTS_MODEL = os.getenv('STREAMING_TTS_MODEL', 'aura-2-asteria-en')

# LiveKit configuration (audio transport)
LIVEKIT_ENABLED = os.getenv('LIVEKIT_ENABLED', 'false').lower() == 'true'
# Force LiveKit input enabled if LiveKit itself is enabled
LIVEKIT_INPUT_ENABLED = os.getenv("LIVEKIT_INPUT_ENABLED", "True").lower() == "true"
LIVEKIT_URL = os.getenv('LIVEKIT_URL', '').strip()
LIVEKIT_API_KEY = os.getenv('LIVEKIT_API_KEY', '').strip()
LIVEKIT_API_SECRET = os.getenv('LIVEKIT_API_SECRET', '').strip()
LIVEKIT_ROOM_PREFIX = os.getenv('LIVEKIT_ROOM_PREFIX', 'voice-')

# LiveKit client CDN fallback URLs (used for proxying to same origin)
LIVEKIT_CLIENT_URLS = [
    "https://cdn.jsdelivr.net/npm/livekit-client/dist/livekit-client.umd.min.js",
    "https://unpkg.com/livekit-client/dist/livekit-client.umd.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/livekit-client/1.0.23/livekit-client.umd.min.js",
    "https://app.unpkg.com/livekit-client@1.2.11/files/dist/livekit-client.umd.js",
]
LIVEKIT_CLIENT_JS_CACHE = None

# LLM model used for voice responses (warm-up target)
VOICE_MODEL = os.getenv("VOICE_MODEL", "").strip()

webrtc_bp = Blueprint('webrtc_voice', __name__, url_prefix='/webrtc')

@webrtc_bp.route('/livekit-debug')
def livekit_debug():
    """Return debug info about LiveKit configuration"""
    import inspect
    
    status = {
        "enabled_env": LIVEKIT_ENABLED,
        "available_import": LIVEKIT_AVAILABLE,
        "manager_class_exists": LiveKitSessionManager is not None,
        "manager_instance_exists": livekit_manager is not None,
        "manager_is_available": livekit_manager.is_available() if livekit_manager else False,
        "env_vars": {
            "URL": bool(LIVEKIT_URL),
            "API_KEY": bool(LIVEKIT_API_KEY),
            "API_SECRET": bool(LIVEKIT_API_SECRET),
        }
    }
    
    # Try to get import error if available
    if not LIVEKIT_AVAILABLE:
        try:
            from livekit import rtc
            status["import_rtc"] = "Success"
            status["rtc_dir"] = dir(rtc)  # List everything in rtc module
        except ImportError as e:
            status["import_rtc"] = str(e)
            
    return jsonify(status)

# Initialize Deepgram service for STT and TTS
# Note: Using Deepgram for both STT and TTS, Claude for LLM
deepgram_service = None
def get_deepgram_tts_service():
    """Get Deepgram service for TTS"""
    global deepgram_service
    if deepgram_service is None:
        deepgram_service = get_deepgram_service()
    return deepgram_service

def _synthesize_deepgram_linear16(text: str, voice: str = "aura-asteria-en", sample_rate: int = 24000) -> Optional[bytes]:
    deepgram_tts = get_deepgram_tts_service()
    if not deepgram_tts:
        return None
    return deepgram_tts.synthesize_speech(
        text,
        voice=voice,
        encoding="linear16",
        sample_rate=sample_rate,
        container="none"
    )

# Active sessions storage (fallback for when Redis is unavailable)
active_sessions = {}

# Active streaming sessions (per Socket.IO session)
streaming_sessions = {}

# Track active response streams for barge-in cancellation
active_response_controls = {}
BARge_IN_MIN_INTERVAL_SEC = 0.25

# One-time model warm-up flag
MODEL_WARMED = False
MODEL_WARMUP_LOCK = threading.Lock()

# LiveKit helpers
def _livekit_active() -> bool:
    return bool(LIVEKIT_ENABLED and livekit_manager and livekit_manager.is_available())

def _livekit_input_active() -> bool:
    return bool(_livekit_active() and LIVEKIT_INPUT_ENABLED)

def _livekit_room_name(session_id: str) -> str:
    return f"{LIVEKIT_ROOM_PREFIX}{session_id}"

def _get_llm_provider_for_user(user_id: Optional[str]) -> str:
    provider = None
    if redis_manager.is_available():
        try:
            if user_id:
                provider = redis_manager.redis_client.get(f"user:{user_id}:llm_provider")
            if not provider:
                provider = redis_manager.redis_client.get("user:default:llm_provider")
        except Exception as e:
            print(f"⚠️ Error reading LLM provider preference: {e}", flush=True)
    if not provider:
        provider = os.getenv("LLM_PROVIDER", "claude").lower()
    if provider not in ["claude", "gemini", "openai"]:
        provider = "claude"
    return provider

def _select_voice_model(user_id: Optional[str]) -> str:
    provider = _get_llm_provider_for_user(user_id)
    if VOICE_MODEL:
        if provider == "claude" and VOICE_MODEL.startswith("claude-"):
            return VOICE_MODEL
        if provider == "gemini" and VOICE_MODEL.startswith("gemini-"):
            return VOICE_MODEL
        if provider == "openai" and (VOICE_MODEL.startswith("gpt-") or VOICE_MODEL.startswith("o1") or VOICE_MODEL.startswith("o3")):
            return VOICE_MODEL
        print(f"⚠️ Ignoring VOICE_MODEL='{VOICE_MODEL}' for provider '{provider}'", flush=True)
    if provider == "gemini":
        return os.getenv("GOOGLE_VOICE_MODEL") or os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
    if provider == "openai":
        return os.getenv("OPENAI_VOICE_MODEL") or os.getenv("OPENAI_MODEL", "gpt-4o")
    return os.getenv("ANTHROPIC_VOICE_MODEL") or os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

def _get_livekit_info(session_id: str, user_id: str) -> Optional[dict]:
    if not _livekit_active() or not generate_livekit_token:
        return None
    room_name = _livekit_room_name(session_id)
    identity = f"user:{user_id}"
    token = generate_livekit_token(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, identity, room_name)
    return {
        "url": LIVEKIT_URL,
        "room": room_name,
        "token": token,
        "identity": identity
    }

def _ensure_livekit_session(session_id: str, user_id: str):
    if not _livekit_active():
        return None
    room_name = _livekit_room_name(session_id)
    assistant_identity = f"assistant:{session_id}"
    return livekit_manager.ensure_session(session_id, room_name, assistant_identity)

def _send_livekit_pcm(session_id: str, pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1):
    if _livekit_active() and pcm_bytes:
        livekit_manager.send_pcm(session_id, pcm_bytes, sample_rate=sample_rate, channels=channels)

# LiveKit session manager (optional)
try:
    from convonet.livekit_audio_bridge import LiveKitSessionManager, generate_livekit_token, LIVEKIT_AVAILABLE
except Exception as e:
    print(f"⚠️ LiveKit bridge not available: {e}")
    LiveKitSessionManager = None
    generate_livekit_token = None
    LIVEKIT_AVAILABLE = False

livekit_manager = None


class StreamingTTSStream:
    """Deepgram streaming TTS connection to emit audio chunks as text arrives."""
    def __init__(self, session_id: str, socketio_instance: SocketIO, model: str, use_livekit_audio: bool = False):
        self.session_id = session_id
        self.socketio = socketio_instance
        self.model = model
        self.use_livekit_audio = use_livekit_audio
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.text_queue = None
        self.ready = threading.Event()
        self.stop_event = threading.Event()
        self.chunk_index = 0

    def start(self):
        self.thread.start()
        self.ready.wait(timeout=5.0)

    def send_text(self, text: str):
        if not text or not text.strip() or not self.text_queue:
            return
        asyncio.run_coroutine_threadsafe(self.text_queue.put(text), self.loop)

    def flush_and_close(self):
        if self.text_queue:
            asyncio.run_coroutine_threadsafe(self.text_queue.put(None), self.loop)

    def stop(self):
        self.flush_and_close()
        self.stop_event.set()

    def _emit_audio_chunk(self, chunk_bytes: bytes):
        try:
            if self.use_livekit_audio and _livekit_active():
                _send_livekit_pcm(self.session_id, chunk_bytes, sample_rate=24000, channels=1)
                return
            # Deepgram streaming TTS returns raw PCM (linear16); wrap as WAV for browser decode.
            if not (chunk_bytes[:4] == b'RIFF' and b'WAVE' in chunk_bytes[:12]):
                chunk_bytes = self._wrap_linear16_wav(chunk_bytes, sample_rate=24000, channels=1, sample_width=2)
            chunk_base64 = base64.b64encode(chunk_bytes).decode('utf-8')
            self.socketio.emit('audio_chunk', {
                'success': True,
                'chunk_index': self.chunk_index,
                'total_chunks': -1,
                'audio': chunk_base64,
                'is_final': False
            }, namespace='/voice', room=self.session_id)
            self.chunk_index += 1
        except Exception as emit_error:
            print(f"⚠️ Error emitting streaming TTS chunk: {emit_error}", flush=True)

    @staticmethod
    def _wrap_linear16_wav(pcm_bytes: bytes, sample_rate: int, channels: int, sample_width: int) -> bytes:
        """Wrap raw PCM bytes in a WAV header for browser playback."""
        data_size = len(pcm_bytes)
        byte_rate = sample_rate * channels * sample_width
        block_align = channels * sample_width
        bits_per_sample = sample_width * 8
        riff_size = 36 + data_size
        header = struct.pack(
            '<4sI4s4sIHHIIHH4sI',
            b'RIFF',
            riff_size,
            b'WAVE',
            b'fmt ',
            16,
            1,
            channels,
            sample_rate,
            byte_rate,
            block_align,
            bits_per_sample,
            b'data',
            data_size
        )
        return header + pcm_bytes

    def _run_loop(self):
        if not DEEPGRAM_STREAMING_AVAILABLE:
            print("⚠️ Deepgram streaming SDK unavailable - cannot start streaming TTS", flush=True)
            return
        try:
            asyncio.set_event_loop(self.loop)
            self.text_queue = asyncio.Queue()
            client = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

            async def run_connection():
                async with client.speak.v1.connect(
                    model=self.model,
                    encoding="linear16",
                    sample_rate=24000
                ) as connection:
                    def on_message(message: SpeakV1SocketClientResponse) -> None:
                        if isinstance(message, bytes):
                            self._emit_audio_chunk(message)

                    connection.on(EventType.MESSAGE, on_message)
                    connection.on(EventType.ERROR, lambda error: print(f"❌ Deepgram TTS error: {error}", flush=True))
                    await connection.start_listening()
                    self.ready.set()

                    while not self.stop_event.is_set():
                        text = await self.text_queue.get()
                        if text is None:
                            await connection.send_control(SpeakV1ControlMessage(type="Flush"))
                            await connection.send_control(SpeakV1ControlMessage(type="Close"))
                            break
                        await connection.send_text(SpeakV1TextMessage(text=text))

            self.loop.run_until_complete(run_connection())
        except Exception as e:
            print(f"❌ Streaming TTS loop error: {e}", flush=True)
        finally:
            try:
                self.socketio.emit('audio_stream_complete', {
                    'success': True,
                    'total_chunks': self.chunk_index,
                    'successful_chunks': self.chunk_index,
                    'failed_chunks': 0
                }, namespace='/voice', room=self.session_id)
            except Exception:
                pass
            try:
                self.loop.close()
            except Exception:
                pass


class StreamingSTTSession:
    """Deepgram streaming STT session for low-latency transcription."""
    def __init__(self, session_id: str, socketio_instance: SocketIO, on_final_transcript, on_user_speech):
        self.session_id = session_id
        self.socketio = socketio_instance
        self.on_final_transcript = on_final_transcript
        self.on_user_speech = on_user_speech
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.audio_queue = None
        self.stop_event = threading.Event()
        self.active = threading.Event()
        self.partial_segments = []
        self.last_speech_time = 0.0

    def start(self):
        self.thread.start()
        # Non-blocking wait if needed, but usually not required for async connection start

    def send_audio(self, audio_chunk: bytes):
        if not self.audio_queue:
            return
        asyncio.run_coroutine_threadsafe(self.audio_queue.put(audio_chunk), self.loop)

    def stop(self):
        if self.audio_queue:
            asyncio.run_coroutine_threadsafe(self.audio_queue.put(None), self.loop)
        self.stop_event.set()

    def _handle_message(self, message):
        try:
            transcript = ""
            is_final = False
            speech_final = False

            if hasattr(message, "channel") and message.channel and message.channel.alternatives:
                transcript = message.channel.alternatives[0].transcript or ""
            is_final = bool(getattr(message, "is_final", False))
            speech_final = bool(getattr(message, "speech_final", False))
            msg_type = getattr(message, "type", "")
            if msg_type and str(msg_type).lower() in {"speech_final", "speechfinal"}:
                speech_final = True

            if transcript:
                now = time.time()
                if now - self.last_speech_time > 0.25:
                    self.last_speech_time = now
                    if self.on_user_speech:
                        self.on_user_speech()

                if is_final:
                    self.partial_segments.append(transcript.strip())

                if speech_final:
                    full_text = " ".join(self.partial_segments).strip()
                    self.partial_segments = []
                    if full_text and self.on_final_transcript:
                        self.on_final_transcript(full_text)
        except Exception as e:
            print(f"⚠️ Streaming STT message handling error: {e}", flush=True)

    def _run_loop(self):
        if not DEEPGRAM_STREAMING_AVAILABLE:
            print("⚠️ Deepgram streaming SDK unavailable - cannot start streaming STT", flush=True)
            return
        try:
            asyncio.set_event_loop(self.loop)
            self.audio_queue = asyncio.Queue()
            client = AsyncDeepgramClient(api_key=os.getenv("DEEPGRAM_API_KEY"))

            async def run_connection():
                async with client.listen.v2.connect(
                    model=STREAMING_STT_MODEL,
                    encoding="opus",
                    sample_rate="48000",
                    container="webm",
                    language="en-US",
                    interim_results=True,
                    vad_events=True,
                    endpointing=str(STREAMING_STT_ENDPOINTING_MS),
                    smart_format=True,
                ) as connection:
                    connection.on(EventType.MESSAGE, self._handle_message)
                    connection.on(EventType.ERROR, lambda error: print(f"❌ Deepgram STT error: {error}", flush=True))
                    await connection.start_listening()
                    self.active.set()

                    while not self.stop_event.is_set():
                        audio_chunk = await self.audio_queue.get()
                        if audio_chunk is None:
                            await connection.send_control(ListenV2ControlMessage(type="CloseStream"))
                            break
                        await connection.send_media(ListenV2MediaMessage(data=audio_chunk))

            self.loop.run_until_complete(run_connection())
        except Exception as e:
            print(f"❌ Streaming STT loop error: {e}", flush=True)
        finally:
            try:
                self.loop.close()
            except Exception:
                pass


def warmup_llm_model():
    """Warm up the LLM to reduce time-to-first-token on first real request."""
    global MODEL_WARMED
    with MODEL_WARMUP_LOCK:
        if MODEL_WARMED:
            return
        MODEL_WARMED = True

    def run_warmup():
        try:
            from convonet.routes import _run_agent_async
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(
                _run_agent_async(
                    prompt="Warm up. Reply with OK.",
                    user_id="warmup",
                    user_name="warmup",
                    reset_thread=True,
                    include_metadata=False,
                    socketio=None,
                    session_id=None,
                    model=VOICE_MODEL,
                    text_chunk_callback=None
                )
            )
        except Exception as e:
            print(f"⚠️ LLM warm-up failed: {e}", flush=True)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    threading.Thread(target=run_warmup, daemon=True).start()


def register_active_response(session_id: str, cancel_event: threading.Event, tts_stream: Optional[StreamingTTSStream]):
    active_response_controls[session_id] = {
        "cancel_event": cancel_event,
        "tts_stream": tts_stream,
        "last_barge_in": 0.0
    }


def cancel_active_response(session_id: str, reason: str = "barge_in"):
    control = active_response_controls.get(session_id)
    if control:
        control["cancel_event"].set()
        tts_stream = control.get("tts_stream")
        if tts_stream:
            tts_stream.stop()
    emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
    if emit_socketio:
        emit_socketio.emit('stop_audio', {'reason': reason}, namespace='/voice', room=session_id)

# Global references for background tasks
socketio = None
flask_app = None


def build_customer_profile_from_session(session_data: dict | None) -> dict | None:
    """
    Build a comprehensive customer profile for the call center popup.
    Includes conversation history from LangGraph and activities from tool calls.
    """
    if not session_data:
        return None
    
    profile = {
        "customer_id": session_data.get('user_id') or session_data.get('user_name') or "convonet_caller",
        "name": session_data.get('user_name') or "Convonet Caller",
        "email": None,
        "phone": None,
        "account_status": "Active",
        "tier": "Standard",
        "notes": "Captured from Convonet voice assistant",
        "conversation_history": [],
        "activities": []
    }
    
    user_id = session_data.get('user_id')
    if user_id:
        try:
            from convonet.mcps.local_servers import db_todo
            from convonet.models.user_models import User as UserModel
            
            db_todo._init_database()
            with db_todo.SessionLocal() as db_session:
                # Add validation for UUID
                is_valid_uuid = False
                try:
                    target_uuid = UUID(user_id) if user_id else None
                    is_valid_uuid = True
                except (ValueError, TypeError):
                    target_uuid = None
                
                if not is_valid_uuid:
                    print(f"⚠️ Invalid user_id format for DB lookup: {user_id}")
                    user = None
                else:
                    user = db_session.query(UserModel).filter(UserModel.id == target_uuid).first()
                
                if user:
                    profile.update({
                        "customer_id": str(user.id),
                        "name": user.full_name if hasattr(user, "full_name") else f"{user.first_name} {user.last_name}",
                        "email": user.email,
                        "voice_pin": user.voice_pin,
                        "account_status": "Verified" if user.is_verified else "Unverified",
                    })
        except Exception as e:
            print(f"⚠️ Unable to load customer profile for call center: {e}")
    
    # Retrieve conversation history from LangGraph
    try:
        from convonet.assistant_graph_todo import get_agent
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        
        thread_id = f"user-{user_id}" if user_id else None
        if thread_id:
            agent = get_agent(user_id=user_id, agent_type="todo")  # Default to todo agent
            if agent and hasattr(agent, 'graph'):
                config = {"configurable": {"thread_id": thread_id}}
                try:
                    state = agent.graph.get_state(config=config)
                    if state and hasattr(state, 'values') and state.values:
                        messages = state.values.get('messages', [])
                        
                        # Build tool_call_id -> tool_name map for ToolMessage lookups
                        tool_name_by_id = {}
                        for msg in messages:
                            if isinstance(msg, AIMessage) and hasattr(msg, "tool_calls") and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    if isinstance(tc, dict):
                                        tc_id = tc.get("id") or tc.get("tool_call_id")
                                        tc_name = tc.get("name") or tc.get("functionName") or tc.get("function")
                                    else:
                                        tc_id = getattr(tc, "id", getattr(tc, "tool_call_id", None))
                                        tc_name = getattr(tc, "name", getattr(tc, "functionName", None))
                                    if tc_id and tc_name:
                                        tool_name_by_id[tc_id] = tc_name

                        # Parse messages into conversation history
                        conversation = []
                        for msg in messages:
                            if isinstance(msg, HumanMessage):
                                conversation.append({
                                    "role": "user",
                                    "content": msg.content if hasattr(msg, 'content') else str(msg),
                                    "timestamp": getattr(msg, 'timestamp', None)
                                })
                            elif isinstance(msg, AIMessage):
                                content = msg.content if hasattr(msg, 'content') else str(msg)
                                # Extract text from content (might be list of dicts)
                                if isinstance(content, list):
                                    text_parts = [part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text']
                                    content = ' '.join(text_parts) if text_parts else str(content)
                                conversation.append({
                                    "role": "assistant",
                                    "content": content,
                                    "timestamp": getattr(msg, 'timestamp', None)
                                })
                            elif isinstance(msg, ToolMessage):
                                # Extract activities from tool calls
                                tool_call_id = getattr(msg, "tool_call_id", None)
                                tool_name = (
                                    tool_name_by_id.get(tool_call_id)
                                    or getattr(msg, 'name', None)
                                    or 'unknown_tool'
                                )
                                tool_content = msg.content if hasattr(msg, 'content') else str(msg)
                                
                                # Parse tool content to extract activity info
                                activity = {
                                    "type": "tool_call",
                                    "tool": tool_name,
                                    "result": tool_content[:200] if isinstance(tool_content, str) else str(tool_content)[:200],  # Truncate long results
                                    "timestamp": getattr(msg, 'timestamp', None)
                                }
                                
                                # Identify specific activity types
                                if 'calendar' in tool_name.lower() or 'event' in tool_name.lower():
                                    activity["activity_type"] = "calendar_event"
                                    try:
                                        if isinstance(tool_content, str):
                                            import json
                                            result_data = json.loads(tool_content) if tool_content.startswith('{') else {}
                                            if 'title' in result_data:
                                                activity["title"] = result_data.get('title', '')
                                            if 'event_from' in result_data or 'start' in result_data:
                                                activity["date"] = result_data.get('event_from') or result_data.get('start', '')
                                    except:
                                        pass
                                elif 'todo' in tool_name.lower():
                                    activity["activity_type"] = "todo"
                                    try:
                                        if isinstance(tool_content, str):
                                            import json
                                            result_data = json.loads(tool_content) if tool_content.startswith('{') else {}
                                            if 'title' in result_data or 'task' in result_data:
                                                activity["title"] = result_data.get('title') or result_data.get('task', '')
                                    except:
                                        pass
                                elif 'mortgage' in tool_name.lower():
                                    activity["activity_type"] = "mortgage"
                                    try:
                                        if isinstance(tool_content, str):
                                            import json
                                            result_data = json.loads(tool_content) if tool_content.startswith('{') else {}
                                            if 'application_id' in result_data:
                                                activity["title"] = f"Mortgage Application {result_data.get('application_id', '')[:8]}"
                                    except:
                                        pass
                                
                                profile["activities"].append(activity)
                        
                        profile["conversation_history"] = conversation[-20:]  # Last 20 messages
                        profile["activities"] = profile["activities"][-10:]  # Last 10 activities
                        
                except Exception as e:
                    print(f"⚠️ Unable to retrieve LangGraph conversation history: {e}")
    except Exception as e:
        print(f"⚠️ Error building conversation history: {e}")
    
    return profile


def cache_call_center_profile(extension: str, session_data: dict | None, call_sid: str = None, call_id: str = None):
    """
    Store customer info in Redis so the call-center popup can display real data.
    
    Uses unique cache keys with call_sid or call_id to prevent overwrites when multiple calls
    from the same user transfer to the same extension.
    
    Args:
        extension: Agent extension number
        session_data: Session data containing user info
        call_sid: Twilio Call SID (for Twilio calls)
        call_id: Call ID (for WebRTC calls, typically session_id)
    """
    if not extension or not REDIS_AVAILABLE or not redis_manager.is_available():
        return
    
    profile = build_customer_profile_from_session(session_data)
    if not profile:
        return
    
    profile["extension"] = extension
    if call_sid:
        profile["call_sid"] = call_sid
    if call_id:
        profile["call_id"] = call_id
    
    try:
        # Store with unique key if call_sid or call_id provided
        if call_sid:
            unique_key = f"callcenter:customer:{extension}:{call_sid}"
            redis_manager.redis_client.setex(unique_key, 300, json.dumps(profile))
            print(f"💾 Cached customer profile with unique key: {unique_key}")
        elif call_id:
            unique_key = f"callcenter:customer:{extension}:{call_id}"
            redis_manager.redis_client.setex(unique_key, 300, json.dumps(profile))
            print(f"💾 Cached customer profile with unique key: {unique_key}")
        
        # Also store with extension-only key for backward compatibility (most recent call)
        fallback_key = f"callcenter:customer:{extension}"
        redis_manager.redis_client.setex(fallback_key, 300, json.dumps(profile))
        print(f"💾 Cached customer profile with fallback key: {fallback_key}")
    except Exception as e:
        print(f"⚠️ Failed to cache call center profile: {e}")


def is_transfer_in_progress(session_id: str, session_record: dict | None = None) -> bool:
    """Check whether a transfer is already in progress for this WebRTC session."""
    try:
        if session_record and 'transfer_in_progress' in session_record:
            return str(session_record['transfer_in_progress']).lower() == 'true'
        
        if redis_manager.is_available():
            value = redis_manager.redis_client.hget(f"session:{session_id}", "transfer_in_progress")
            if value is not None:
                return str(value).lower() == 'true'
        else:
            if session_id in active_sessions:
                return bool(active_sessions[session_id].get('transfer_in_progress'))
    except Exception as e:
        print(f"⚠️ Unable to read transfer flag for session {session_id}: {e}")
    return False


def set_transfer_flag(session_id: str, value: bool, session_record: dict | None = None):
    """Persist the transfer_in_progress flag for this WebRTC session."""
    str_value = 'True' if value else 'False'
    try:
        if session_record is not None:
            session_record['transfer_in_progress'] = str_value
        
        if redis_manager.is_available():
            redis_manager.redis_client.hset(f"session:{session_id}", "transfer_in_progress", str_value)
        else:
            if session_id not in active_sessions:
                active_sessions[session_id] = {}
            active_sessions[session_id]['transfer_in_progress'] = value
    except Exception as e:
        print(f"⚠️ Unable to set transfer flag for session {session_id}: {e}")


def initiate_agent_transfer(session_id: str, extension: str, department: str, reason: str, session_data: dict | None):
    """
    Use Twilio Programmable Voice to originate a real call path to the target agent (and optionally the user).

    Returns:
        (success: bool, details: dict)
    """
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token = os.getenv('TWILIO_AUTH_TOKEN')
    caller_id = (
        os.getenv('TWILIO_TRANSFER_CALLER_ID')
        or os.getenv('TWILIO_CALLER_ID')
        or os.getenv('TWILIO_NUMBER')
    )
    # Get base URL - prefer explicit transfer URL, then public URL, then Render URL
    base_url = (
        os.getenv('VOICE_ASSISTANT_TRANSFER_BASE_URL') 
        or os.getenv('PUBLIC_BASE_URL')
        or os.getenv('RENDER_EXTERNAL_URL')  # Render automatically sets this
        or 'https://convonet-anthropic.onrender.com'  # Fallback to Render service URL
    )
    freepbx_domain = os.getenv('FREEPBX_DOMAIN', '136.115.41.45')

    if not (account_sid and auth_token and caller_id and base_url):
        missing = []
        if not account_sid:
            missing.append('TWILIO_ACCOUNT_SID')
        if not auth_token:
            missing.append('TWILIO_AUTH_TOKEN')
        if not caller_id:
            missing.append('TWILIO_TRANSFER_CALLER_ID / TWILIO_CALLER_ID / TWILIO_NUMBER')
        if not base_url:
            missing.append('VOICE_ASSISTANT_TRANSFER_BASE_URL / PUBLIC_BASE_URL')
        message = f"Transfer aborted: missing configuration values: {', '.join(missing)}"
        print(f"⚠️ {message}")
        return False, {'error': message}

    # For WebRTC transfers, we directly dial the FusionPBX extension
    # The WebRTC user can't join a Twilio conference, so we just connect the agent
    transfer_url = f"{base_url.rstrip('/')}/convonet_todo/twilio/voice_assistant/transfer_bridge?extension={quote(extension)}"

    client = Client(account_sid, auth_token)
    response_details = {
        'extension': extension,
        'transfer_url': transfer_url,
        'agent_call_sid': None,
        'user_call_sid': None
    }

    try:
        # Use domain/IP for Twilio (Twilio needs resolvable domain/IP)
        # FusionPBX dialplan must be configured to route external calls to extensions
        sip_target = f"sip:{extension}@{freepbx_domain};transport=udp"
        print(f"📞 Creating Twilio call:")
        print(f"   To: {sip_target}")
        print(f"   From: {caller_id}")
        print(f"   URL: {transfer_url}")
        agent_call = client.calls.create(
            to=sip_target,
            from_=caller_id,
            url=transfer_url,
            method='POST'  # Explicitly set POST method
        )
        response_details['agent_call_sid'] = agent_call.sid
        print(f"📞 ✅ Initiated agent call via Twilio (Call SID: {agent_call.sid}) to {sip_target}")
        print(f"📞 Call status: {agent_call.status}")
        print(f"📞 Twilio will POST to: {transfer_url}")
        
        # Cache customer profile with call_sid after getting Call SID
        if agent_call.sid and session_data:
            cache_call_center_profile(extension, session_data, call_sid=agent_call.sid)
    except Exception as agent_error:
        message = f"Failed to originate agent call: {agent_error}"
        print(f"❌ {message}")
        response_details['error'] = message
        return False, response_details

    # For WebRTC transfers, we don't call the user back because:
    # 1. WebRTC is browser-based, not a phone number
    # 2. The user needs to manually call the agent or use a different method
    # Instead, we provide instructions to the user via the WebRTC interface
    print(f"ℹ️ WebRTC transfer: Agent call initiated to extension {extension}. User should contact agent separately or use call center dashboard.")
    response_details['user_instructions'] = f"Please contact extension {extension} via the call center dashboard at {base_url}/call-center/"

    return True, response_details

# Sentry helper functions
def sentry_capture_redis_operation(operation: str, session_id: str, success: bool, error: str = None):
    """Capture Redis operations in Sentry for monitoring"""
    if SENTRY_AVAILABLE:
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("component", "webrtc_voice_server")
            scope.set_tag("operation", f"redis_{operation}")
            scope.set_context("redis_operation", {
                "session_id": session_id,
                "operation": operation,
                "success": success,
                "error": error
            })
            if success:
                sentry_sdk.add_breadcrumb(
                    message=f"Redis {operation} successful",
                    category="redis",
                    level="info"
                )
            else:
                sentry_sdk.capture_message(f"Redis {operation} failed: {error}", level="error")

def sentry_capture_voice_event(event: str, session_id: str, user_id: str = None, details: dict = None):
    """Capture voice assistant events in Sentry"""
    if SENTRY_AVAILABLE:
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("component", "webrtc_voice_server")
            scope.set_tag("event", event)
            scope.set_context("voice_event", {
                "session_id": session_id,
                "user_id": user_id,
                "event": event,
                "details": details or {}
            })
            sentry_sdk.add_breadcrumb(
                message=f"Voice event: {event}",
                category="voice_assistant",
                level="info"
            )


@webrtc_bp.route('/voice-assistant')
def voice_assistant():
    """Render the WebRTC voice assistant interface"""
    streaming_stt_available = STREAMING_STT_ENABLED and DEEPGRAM_STREAMING_AVAILABLE
    streaming_tts_available = STREAMING_TTS_ENABLED and DEEPGRAM_STREAMING_AVAILABLE
    return render_template(
        'webrtc_voice_assistant.html',
        streaming_stt_enabled=streaming_stt_available,
        streaming_tts_enabled=streaming_tts_available,
        livekit_enabled=_livekit_active(),
        livekit_url=LIVEKIT_URL,
    )


@webrtc_bp.route('/livekit-client.umd.min.js')
def livekit_client_js():
    """Serve LiveKit client JS from same origin with CDN fallback."""
    global LIVEKIT_CLIENT_JS_CACHE
    if LIVEKIT_CLIENT_JS_CACHE:
        print("[livekit] Serving cached client JS")
        return Response(LIVEKIT_CLIENT_JS_CACHE, mimetype="application/javascript")

    def _extract_unpkg_js(raw_text: str) -> Optional[str]:
        if "<pre" not in raw_text.lower():
            return None
        match = re.search(r"<pre[^>]*>(.*?)</pre>", raw_text, re.IGNORECASE | re.DOTALL)
        if not match:
            return None
        extracted = html.unescape(match.group(1)).strip()
        if extracted.startswith("!function") and "LivekitClient" in extracted:
            return extracted
        return None

    for url in LIVEKIT_CLIENT_URLS:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200 and resp.text:
                body = resp.text
                if "<pre" in body.lower():
                    extracted = _extract_unpkg_js(body)
                    if extracted:
                        LIVEKIT_CLIENT_JS_CACHE = extracted
                        print(f"[livekit] Extracted client JS from {url}")
                        return Response(LIVEKIT_CLIENT_JS_CACHE, mimetype="application/javascript")
                if body.lstrip().startswith("!function"):
                    LIVEKIT_CLIENT_JS_CACHE = body
                else:
                    LIVEKIT_CLIENT_JS_CACHE = body
                print(f"[livekit] Fetched client JS from {url}")
                return Response(LIVEKIT_CLIENT_JS_CACHE, mimetype="application/javascript")
            print(f"[livekit] CDN fetch failed ({resp.status_code}) for {url}")
        except Exception:
            print(f"[livekit] CDN fetch error for {url}")
            continue

    # Fall back to a tiny loader that tries the CDN in the browser.
    fallback_js = (
        "/* LiveKit client unavailable - fallback loader */\n"
        "(function(){\n"
        "  if (window.LiveKit) { return; }\n"
        f"  var sources = {json.dumps(LIVEKIT_CLIENT_URLS)};\n"
        "  var index = 0;\n"
        "  function loadNext(){\n"
        "    if (index >= sources.length) {\n"
        "      console.warn('LiveKit SDK failed to load');\n"
        "      return;\n"
        "    }\n"
        "    var src = sources[index++];\n"
        "    var script = document.createElement('script');\n"
        "    script.src = src;\n"
        "    script.onload = function(){\n"
        "      console.log('LiveKit SDK loaded from ' + src);\n"
        "    };\n"
        "    script.onerror = loadNext;\n"
        "    document.head.appendChild(script);\n"
        "  }\n"
        "  loadNext();\n"
        "})();\n"
    )
    response = Response(fallback_js, status=503, mimetype="application/javascript")
    response.headers["X-LiveKit-Proxy"] = "fallback"
    return response


def chunk_text_by_sentences(text: str, min_chunk_size: int = 100, max_chunk_size: int = 500) -> list[str]:
    """
    Split text into sentence-based chunks for streaming TTS.
    
    Args:
        text: Text to chunk
        min_chunk_size: Minimum chunk size in characters (chunks smaller than this will be merged)
        max_chunk_size: Maximum chunk size in characters (sentences beyond this will be split)
    
    Returns:
        List of text chunks (sentences or groups of sentences)
    """
    if not text or len(text.strip()) == 0:
        return []
    
    # Split by sentence endings (period, exclamation, question mark)
    # Keep the punctuation with the sentence using positive lookbehind
    sentences = re.split(r'(?<=[.!?])\s+', text)
    
    chunks = []
    current_chunk = ""
    
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        
        # If adding this sentence would exceed max_chunk_size, finalize current chunk
        if current_chunk and len(current_chunk) + len(sentence) + 1 > max_chunk_size:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = sentence
        
        # If single sentence is larger than max_chunk_size, split it further by commas or add as-is
        elif len(sentence) > max_chunk_size:
            # If we have a current chunk, finalize it first
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            
            # Split long sentence by commas or add as-is if no commas
            comma_parts = re.split(r'(?<=,)\s+', sentence)
            for part in comma_parts:
                if len(part) > max_chunk_size:
                    # Too long even after comma split, add as-is
                    chunks.append(part.strip())
                else:
                    if current_chunk and len(current_chunk) + len(part) + 1 > max_chunk_size:
                        chunks.append(current_chunk.strip())
                        current_chunk = part
                    else:
                        current_chunk += (" " + part if current_chunk else part)
        else:
            # Normal case: add sentence to current chunk
            if current_chunk:
                # Check if current chunk is already above min_size, if so we can start a new one if needed
                if len(current_chunk) >= min_chunk_size and len(current_chunk) + len(sentence) + 1 > max_chunk_size:
                    chunks.append(current_chunk.strip())
                    current_chunk = sentence
                else:
                    current_chunk += " " + sentence
            else:
                current_chunk = sentence
    
    # Add the last chunk if it exists
    if current_chunk:
        chunks.append(current_chunk.strip())
    
    # Filter out very small chunks (merge them with previous if possible)
    filtered_chunks = []
    for chunk in chunks:
        if len(chunk) < min_chunk_size and filtered_chunks:
            # Merge with previous chunk
            filtered_chunks[-1] += " " + chunk
        else:
            filtered_chunks.append(chunk)
    
    return filtered_chunks if filtered_chunks else [text.strip()] if text.strip() else []


@webrtc_bp.route('/debug-session/<session_id>')
def debug_session(session_id):
    """Debug endpoint to check Redis session data"""
    try:
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if session_data:
                # Convert bytes to strings for JSON serialization
                debug_data = {}
                for key, value in session_data.items():
                    if isinstance(value, bytes):
                        debug_data[key] = value.decode('utf-8', errors='ignore')
                    else:
                        debug_data[key] = str(value)
                
                # Add audio buffer info
                audio_buffer = session_data.get('audio_buffer', '')
                debug_data['audio_buffer_length'] = len(audio_buffer)
                debug_data['audio_buffer_preview'] = audio_buffer[:100] + "..." if len(audio_buffer) > 100 else audio_buffer
                
                # Test base64 decoding
                try:
                    if audio_buffer:
                        decoded = base64.b64decode(audio_buffer)
                        debug_data['decoded_audio_length'] = len(decoded)
                        debug_data['base64_valid'] = True
                    else:
                        debug_data['decoded_audio_length'] = 0
                        debug_data['base64_valid'] = True
                except Exception as e:
                    debug_data['base64_valid'] = False
                    debug_data['base64_error'] = str(e)
                
                return jsonify({
                    'success': True,
                    'session_id': session_id,
                    'data': debug_data,
                    'storage': 'redis'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Session not found in Redis',
                    'session_id': session_id
                })
        else:
            # Check in-memory storage
            if session_id in active_sessions:
                session_data = active_sessions[session_id]
                debug_data = {
                    'authenticated': session_data.get('authenticated', False),
                    'user_id': session_data.get('user_id'),
                    'user_name': session_data.get('user_name'),
                    'is_recording': session_data.get('is_recording', False),
                    'audio_buffer_length': len(session_data.get('audio_buffer', b'')),
                    'storage': 'memory'
                }
                return jsonify({
                    'success': True,
                    'session_id': session_id,
                    'data': debug_data,
                    'storage': 'memory'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Session not found in memory',
                    'session_id': session_id
                })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'session_id': session_id
        })


@webrtc_bp.route('/clear-session/<session_id>')
def clear_session(session_id):
    """Clear Redis session data for testing"""
    try:
        if redis_manager.is_available():
            # Clear the session
            delete_session(session_id)
            return jsonify({
                'success': True,
                'message': f'Session {session_id} cleared from Redis'
            })
        else:
            # Clear from memory
            if session_id in active_sessions:
                del active_sessions[session_id]
                return jsonify({
                    'success': True,
                    'message': f'Session {session_id} cleared from memory'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': f'Session {session_id} not found'
                })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'session_id': session_id
        })


def init_socketio(socketio_instance: SocketIO, app):
    """Initialize Socket.IO event handlers"""
    
    # Store socketio instance and Flask app for background tasks
    global socketio, flask_app, socketio_instance_global, livekit_manager
    socketio = socketio_instance
    socketio_instance_global = socketio_instance  # Store for use in nested functions
    flask_app = app  # Store Flask app directly (passed as parameter)
    
    print(f"🔧 LiveKit Config Check:", flush=True)
    print(f"   - LIVEKIT_ENABLED (env): {LIVEKIT_ENABLED}", flush=True)
    print(f"   - LIVEKIT_AVAILABLE (import): {LIVEKIT_AVAILABLE}", flush=True)
    print(f"   - LiveKitSessionManager (class): {LiveKitSessionManager is not None}", flush=True)
    
    if LIVEKIT_ENABLED and LIVEKIT_AVAILABLE and LiveKitSessionManager and not livekit_manager:
        print(f"🔧 Initializing LiveKitSessionManager...", flush=True)
        try:
            livekit_manager = LiveKitSessionManager(LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
            print(f"   - LiveKit Manager Initialized: {livekit_manager is not None}", flush=True)
            print(f"   - LiveKit Manager Available: {livekit_manager.is_available()}", flush=True)
            if not livekit_manager.is_available():
                print(f"   ⚠️ Manager reports unavailable (Check URL/Keys)", flush=True)
                print(f"     URL present: {bool(LIVEKIT_URL)}", flush=True)
                print(f"     API Key present: {bool(LIVEKIT_API_KEY)}", flush=True)
                print(f"     API Secret present: {bool(LIVEKIT_API_SECRET)}", flush=True)
        except Exception as e:
            print(f"   ❌ LiveKit Initialization failed: {e}", flush=True)
    else:
        print(f"⚠️ LiveKit Config Skipped (One or more conditions failed)", flush=True)

    # Global guards for this namespace to throttle overlapping agent runs
    processing_guards = {}
    
    @socketio.on('connect', namespace='/voice')
    def handle_connect():
        """Handle client connection"""
        session_id = request.sid
        print(f"✅ WebRTC client connected: {session_id}")
        
        # Capture connection event in Sentry
        sentry_capture_voice_event("client_connected", session_id)
        
        # Initialize session in Redis (with fallback to in-memory)
        session_data = {
            'authenticated': 'False',
            'user_id': '',
            'user_name': '',
            'audio_buffer': '',
            'is_recording': 'False',
            'connected_at': str(time.time())
        }
        
        try:
            if redis_manager.is_available():
                success = create_session(session_id, session_data, ttl=3600)  # 1 hour TTL
                if success:
                    print(f"✅ Session stored in Redis: {session_id}")
                    sentry_capture_redis_operation("create_session", session_id, True)
                else:
                    print(f"❌ Failed to store session in Redis: {session_id}")
                    sentry_capture_redis_operation("create_session", session_id, False, "Redis create_session returned False")
            else:
                # Fallback to in-memory storage
                active_sessions[session_id] = {
                    'authenticated': False,
                    'user_id': None,
                    'user_name': None,
                    'audio_buffer': b'',
                    'is_recording': False
                }
                print(f"⚠️ Using in-memory storage (Redis unavailable): {session_id}")
                sentry_capture_voice_event("redis_fallback", session_id, details={"storage": "in_memory"})
        except Exception as e:
            print(f"❌ Error creating session: {e}")
            sentry_capture_redis_operation("create_session", session_id, False, str(e))
            # Fallback to in-memory storage on error
            active_sessions[session_id] = {
                'authenticated': False,
                'user_id': None,
                'user_name': None,
                'audio_buffer': b'',
                'is_recording': False
            }
        
        emit('connected', {'session_id': session_id})

        # Warm up LLM model on first client connection
        warmup_llm_model()
    
    
    @socketio.on('disconnect', namespace='/voice')
    def handle_disconnect():
        """Handle client disconnection"""
        session_id = request.sid
        print(f"❌ WebRTC client disconnected: {session_id}")

        # Stop any active streaming STT session
        if session_id in streaming_sessions:
            try:
                streaming_sessions[session_id].stop()
                del streaming_sessions[session_id]
            except Exception as stop_error:
                print(f"⚠️ Error stopping streaming session on disconnect: {stop_error}", flush=True)

        # Clear active response controls on disconnect
        if session_id in active_response_controls:
            active_response_controls.pop(session_id, None)

        # Close LiveKit session if active
        try:
            if _livekit_active():
                livekit_manager.close_session(session_id)
        except Exception as livekit_close_error:
            print(f"⚠️ Error closing LiveKit session: {livekit_close_error}", flush=True)
        
        # Get user_id before deleting session (for pending response handling)
        user_id = None
        try:
            session_data = get_session(session_id)
            if session_data:
                user_id = session_data.get('user_id')
        except:
            pass
        
        # Capture disconnection event in Sentry
        sentry_capture_voice_event("client_disconnected", session_id, user_id)
        set_transfer_flag(session_id, False)
        
        try:
            if redis_manager.is_available():
                success = delete_session(session_id)
                if success:
                    print(f"✅ Session deleted from Redis: {session_id}")
                    sentry_capture_redis_operation("delete_session", session_id, True)
                else:
                    print(f"❌ Failed to delete session from Redis: {session_id}")
                    sentry_capture_redis_operation("delete_session", session_id, False, "Redis delete_session returned False")
            else:
                if session_id in active_sessions:
                    del active_sessions[session_id]
                    print(f"✅ Session deleted from memory: {session_id}")
                    sentry_capture_voice_event("session_deleted_memory", session_id)
        except Exception as e:
            print(f"❌ Error deleting session: {e}")
            sentry_capture_redis_operation("delete_session", session_id, False, str(e))
        
        # Note: If there's a pending response being processed for this user_id,
        # it will be stored when TTS completes and session is found to be gone
    
    
    @socketio.on('authenticate', namespace='/voice')
    def handle_authenticate(data):
        """Handle user authentication"""
        session_id = request.sid
        pin = data.get('pin', '')
        
        print(f"🔐 Authentication request for session {session_id}: PIN={pin}")
        
        # Capture authentication attempt in Sentry
        if callable(globals().get('sentry_capture_voice_event')):
            sentry_capture_voice_event("authentication_attempt", session_id, details={"pin_provided": bool(pin)})
        else:
            print(f"⚠️ sentry_capture_voice_event is not callable: {type(globals().get('sentry_capture_voice_event'))}")
        
        try:
            # TEST MODE (optional): allow a configurable PIN when explicitly enabled
            if ENABLE_TEST_PIN and pin == TEST_VOICE_PIN:
                print(f"✅ Test authentication successful with PIN: {pin}")
                auth_updates = {
                    'authenticated': 'True',
                    'user_id': 'test_user',
                    'user_name': 'Test User',
                    'authenticated_at': str(time.time())
                }
                
                try:
                    if redis_manager.is_available():
                        success = update_session(session_id, auth_updates)
                        if success:
                            print(f"✅ Test authentication stored in Redis")
                            sentry_capture_redis_operation("update_session", session_id, True)
                            sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "redis", "mode": "test"})
                        else:
                            print(f"❌ Failed to update session in Redis")
                            sentry_capture_redis_operation("update_session", session_id, False, "Redis update_session returned False")
                            # Fallback to in-memory
                            active_sessions[session_id]['authenticated'] = True
                            active_sessions[session_id]['user_id'] = 'test_user'
                            active_sessions[session_id]['user_name'] = 'Test User'
                            print(f"✅ Test authentication stored in memory (Redis fallback)")
                            sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "memory_fallback", "mode": "test"})
                    else:
                        # Fallback to in-memory
                        active_sessions[session_id]['authenticated'] = True
                        active_sessions[session_id]['user_id'] = 'test_user'
                        active_sessions[session_id]['user_name'] = 'Test User'
                        print(f"✅ Test authentication stored in memory")
                        sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "memory", "mode": "test"})
                except Exception as redis_error:
                    print(f"❌ Redis error during test authentication: {redis_error}")
                    sentry_capture_redis_operation("update_session", session_id, False, str(redis_error))
                    # Fallback to in-memory storage
                    active_sessions[session_id]['authenticated'] = True
                    active_sessions[session_id]['user_id'] = 'test_user'
                    active_sessions[session_id]['user_name'] = 'Test User'
                    print(f"✅ Test authentication stored in memory (Redis error fallback)")
                    sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "memory_error_fallback", "mode": "test"})
                
                # Check if user was recently authenticated (re-authentication scenario)
                # We check by user_id, not session_id, because sessions are recreated on disconnect
                was_already_authenticated = False
                try:
                    if redis_manager.is_available():
                        # Check if test_user was authenticated recently (within last 5 minutes)
                        recent_auth_key = f"recent_auth:test_user"
                        recent_auth_data = redis_manager.redis_client.get(recent_auth_key)
                        if recent_auth_data:
                            was_already_authenticated = True
                            print(f"🔄 Re-authentication detected for test_user (session {session_id})")
                except Exception as auth_check_error:
                    print(f"⚠️ Error checking recent authentication: {auth_check_error}")
                    pass
                
                emit('authenticated', {
                    'success': True,
                    'user_name': 'Test User',
                    'user_id': 'test_user',  # Include user_id for pending response polling
                    'message': "Welcome! You're in test mode." if not was_already_authenticated else "Reconnected! You're in test mode."
                })
                
                # Check for pending responses for test user
                try:
                    import json
                    redis_key = f"pending_response:test_user"
                    if redis_manager.is_available():
                        pending_data = redis_manager.redis_client.get(redis_key)
                        if pending_data:
                            pending_response = json.loads(pending_data)
                            print(f"📬 Found pending response for test_user, sending to session {session_id}", flush=True)
                            
                            # Send pending response with a small delay to ensure client is ready
                            def send_pending_response_test():
                                import eventlet
                                eventlet.sleep(0.5)  # Small delay to ensure client is ready
                                
                                # Check if session still exists before sending
                                current_session = get_session(session_id)
                                if current_session:
                                    print(f"📤 Sending pending response to test_user session {session_id} (delayed)", flush=True)
                                    socketio.emit('agent_response', {
                                        'success': True,
                                        'text': pending_response['text'],
                                        'audio': pending_response['audio'],
                                        'pending': True
                                    }, namespace='/voice', room=session_id)
                                    redis_manager.redis_client.delete(redis_key)
                                    print(f"✅ Pending response sent and cleared for test_user", flush=True)
                                else:
                                    print(f"⚠️ Session {session_id} no longer exists, cannot send pending response", flush=True)
                            
                            socketio.start_background_task(send_pending_response_test)
                except Exception as pending_error:
                    print(f"⚠️ Error checking/sending pending response: {pending_error}", flush=True)
                    import traceback
                    traceback.print_exc()
                
                # Track recent authentication for re-authentication detection
                try:
                    if redis_manager.is_available() and not was_already_authenticated:
                        # Store recent authentication timestamp (5 minute TTL)
                        recent_auth_key = f"recent_auth:test_user"
                        redis_manager.redis_client.setex(recent_auth_key, 300, str(time.time()))
                except Exception as track_error:
                    print(f"⚠️ Error tracking recent authentication: {track_error}")
                
                # Only send welcome greeting on first authentication, not on re-authentication
                if not was_already_authenticated:
                    # Send welcome greeting with audio (background task)
                    socketio.start_background_task(
                        send_welcome_greeting, 
                        session_id, 
                        'Test User'
                    )
                else:
                    print(f"⏭️ Skipping welcome greeting (re-authentication)")
                return
            
            # Import here to avoid circular imports
            from convonet.mcps.local_servers import db_todo
            from convonet.models.user_models import User as UserModel
            
            db_todo._init_database()
            
            with db_todo.SessionLocal() as db_session:
                user = db_session.query(UserModel).filter(
                    UserModel.voice_pin == pin,
                    UserModel.is_active == True
                ).first()
                
                if user:
                    # Check if user was recently authenticated (re-authentication scenario)
                    # We check by user_id, not session_id, because sessions are recreated on disconnect
                    was_already_authenticated = False
                    user_id_str = str(user.id)
                    try:
                        if redis_manager.is_available():
                            # Check if user was authenticated recently (within last 5 minutes)
                            recent_auth_key = f"recent_auth:{user_id_str}"
                            recent_auth_data = redis_manager.redis_client.get(recent_auth_key)
                            if recent_auth_data:
                                was_already_authenticated = True
                                print(f"🔄 Re-authentication detected for user {user.id} (session {session_id})")
                    except Exception as auth_check_error:
                        print(f"⚠️ Error checking recent authentication: {auth_check_error}")
                        pass
                    
                    # Authentication successful
                    auth_updates = {
                        'authenticated': 'True',
                        'user_id': str(user.id),
                        'user_name': user.first_name,
                        'authenticated_at': str(time.time())
                    }
                    
                    try:
                        if redis_manager.is_available():
                            success = update_session(session_id, auth_updates)
                            if success:
                                print(f"✅ Authentication stored in Redis: {user.email}")
                                sentry_capture_redis_operation("update_session", session_id, True)
                                sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "redis", "re_authentication": was_already_authenticated})
                                
                                # Check for pending responses for this user
                                try:
                                    import json
                                    redis_key = f"pending_response:{user.id}"
                                    pending_data = redis_manager.redis_client.get(redis_key)
                                    if pending_data:
                                        pending_response = json.loads(pending_data)
                                        print(f"📬 Found pending response for user {user.id}, sending to new session {session_id}", flush=True)
                                        
                                        # Store pending response info to send when client is ready
                                        # We'll wait for client_ready event instead of using a fixed delay
                                        if not hasattr(socketio, '_pending_responses'):
                                            socketio._pending_responses = {}
                                        
                                        socketio._pending_responses[session_id] = {
                                            'response': pending_response,
                                            'redis_key': redis_key,
                                            'user_id': str(user.id),
                                            'original_session_id': pending_response.get('original_session_id')
                                        }
                                        print(f"💾 Stored pending response info for session {session_id}", flush=True)
                                        
                                        # Send immediately on authentication (don't wait for client_ready)
                                        # The client might disconnect quickly, so send ASAP
                                        def send_pending_response_immediate():
                                            import eventlet
                                            # Small delay to ensure authentication is complete
                                            eventlet.sleep(0.3)
                                            
                                            # Check if session still exists
                                            current_session = get_session(session_id)
                                            if not current_session:
                                                print(f"⚠️ Session {session_id} no longer exists for immediate send", flush=True)
                                                return
                                            
                                            # Check if client is in room
                                            try:
                                                participants = list(socketio.server.manager.get_participants('/voice', session_id))
                                                if not participants or len(participants) == 0:
                                                    print(f"⚠️ Client not in room for immediate send to session {session_id}", flush=True)
                                                    return
                                            except Exception as room_error:
                                                print(f"⚠️ Error checking room for immediate send: {room_error}", flush=True)
                                                return
                                            
                                            print(f"📤 Sending pending response notification via Socket.IO (client will fetch via HTTP)", flush=True)
                                            
                                            # DON'T send large audio payload via Socket.IO - it causes WebSocket errors
                                            # Instead, send a small notification and let HTTP polling handle the actual delivery
                                            socketio.emit('pending_response_available', {
                                                'success': True,
                                                'message': 'Pending response available - fetching via HTTP...',
                                                'user_id': str(user.id)
                                            }, namespace='/voice', room=session_id)
                                            
                                            print(f"✅ Pending response notification sent - client will fetch via HTTP polling", flush=True)
                                        
                                        socketio.start_background_task(send_pending_response_immediate)
                                        print(f"💾 Also waiting for client_ready signal as backup", flush=True)
                                        
                                        # Also send with a fallback delay in case client_ready is not received
                                        def send_pending_response_fallback():
                                            import eventlet
                                            # Fallback: send after 3 seconds if client_ready wasn't received
                                            eventlet.sleep(3.0)
                                            
                                            # Check if still pending (not sent via client_ready)
                                            if session_id in getattr(socketio, '_pending_responses', {}):
                                                current_session = get_session(session_id)
                                                if not current_session:
                                                    print(f"⚠️ Session {session_id} no longer exists in fallback, keeping pending response in Redis", flush=True)
                                                    # Clean up pending response info but keep in Redis
                                                    try:
                                                        del socketio._pending_responses[session_id]
                                                    except:
                                                        pass
                                                    return
                                                
                                                # Check if client is actually connected
                                                try:
                                                    participants = list(socketio.server.manager.get_participants('/voice', session_id))
                                                    if not participants or len(participants) == 0:
                                                        print(f"⚠️ Client not in Socket.IO room in fallback, keeping pending response in Redis", flush=True)
                                                        # Clean up pending response info but keep in Redis
                                                        try:
                                                            del socketio._pending_responses[session_id]
                                                        except:
                                                            pass
                                                        return
                                                except Exception as room_check_error:
                                                    print(f"⚠️ Error checking Socket.IO room in fallback: {room_check_error}", flush=True)
                                                
                                                print(f"📤 Sending pending response notification via fallback (client will fetch via HTTP)", flush=True)
                                                
                                                # DON'T send large audio payload via Socket.IO - it causes WebSocket errors
                                                # Instead, send a small notification and let HTTP polling handle the actual delivery
                                                socketio.emit('pending_response_available', {
                                                    'success': True,
                                                    'message': 'Pending response available - fetching via HTTP...',
                                                    'user_id': str(user.id)
                                                }, namespace='/voice', room=session_id)
                                                
                                                print(f"✅ Fallback notification sent - client will fetch via HTTP polling", flush=True)
                                        
                                        socketio.start_background_task(send_pending_response_fallback)
                                except Exception as pending_error:
                                    print(f"⚠️ Error checking/sending pending response: {pending_error}", flush=True)
                                    import traceback
                                    traceback.print_exc()
                            else:
                                print(f"❌ Failed to update session in Redis: {user.email}")
                                sentry_capture_redis_operation("update_session", session_id, False, "Redis update_session returned False")
                                # Fallback to in-memory
                                active_sessions[session_id]['authenticated'] = True
                                active_sessions[session_id]['user_id'] = str(user.id)
                                active_sessions[session_id]['user_name'] = user.first_name
                                print(f"✅ Authentication stored in memory (Redis fallback): {user.email}")
                                sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "memory_fallback", "re_authentication": was_already_authenticated})
                        else:
                            # Fallback to in-memory
                            active_sessions[session_id]['authenticated'] = True
                            active_sessions[session_id]['user_id'] = str(user.id)
                            active_sessions[session_id]['user_name'] = user.first_name
                            print(f"✅ Authentication stored in memory: {user.email}")
                            sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "memory", "re_authentication": was_already_authenticated})
                    except Exception as redis_error:
                        print(f"❌ Redis error during authentication: {redis_error}")
                        sentry_capture_redis_operation("update_session", session_id, False, str(redis_error))
                        # Fallback to in-memory storage
                        active_sessions[session_id]['authenticated'] = True
                        active_sessions[session_id]['user_id'] = str(user.id)
                        active_sessions[session_id]['user_name'] = user.first_name
                        print(f"✅ Authentication stored in memory (Redis error fallback): {user.email}")
                        sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "memory_error_fallback", "re_authentication": was_already_authenticated})
                    
                    emit('authenticated', {
                        'success': True,
                        'user_name': user.first_name,
                        'user_id': str(user.id),  # Include user_id for pending response polling
                        'message': f"Welcome back, {user.first_name}!" if not was_already_authenticated else f"Reconnected, {user.first_name}!"
                    })
                    
                    # Track recent authentication for re-authentication detection
                    try:
                        if redis_manager.is_available() and not was_already_authenticated:
                            # Store recent authentication timestamp (5 minute TTL)
                            recent_auth_key = f"recent_auth:{user_id_str}"
                            redis_manager.redis_client.setex(recent_auth_key, 300, str(time.time()))
                    except Exception as track_error:
                        print(f"⚠️ Error tracking recent authentication: {track_error}")
                    
                    # Only send welcome greeting on first authentication, not on re-authentication
                    if not was_already_authenticated:
                        if _livekit_active():
                            try:
                                if redis_manager.is_available():
                                    if 'update_session' in globals() and update_session:
                                        update_session(session_id, {
                                            'pending_welcome_greeting': 'True',
                                            'pending_welcome_name': user.first_name
                                        })
                                    else:
                                        print("⚠️ update_session global missing or None during pending welcome greeting storage")
                                else:
                                    active_sessions[session_id]['pending_welcome_greeting'] = True
                                    active_sessions[session_id]['pending_welcome_name'] = user.first_name
                                print(f"💾 Stored pending welcome greeting for session {session_id}", flush=True)
                            except Exception as pending_welcome_error:
                                print(f"⚠️ Failed to store pending welcome greeting: {pending_welcome_error}", flush=True)
                                if 'send_welcome_greeting' in globals() and send_welcome_greeting:
                                    socketio.start_background_task(send_welcome_greeting, session_id, user.first_name)
                                else:
                                    print("⚠️ send_welcome_greeting global missing or None during fallback")
                        else:
                            # Send welcome greeting with audio (background task)
                            socketio.start_background_task(
                                send_welcome_greeting,
                                session_id,
                                user.first_name
                            )
                    else:
                        print(f"⏭️ Skipping welcome greeting (re-authentication)")
                else:
                    # Authentication failed
                    print(f"❌ Authentication failed: Invalid PIN")
                    sentry_capture_voice_event("authentication_failed", session_id, details={"reason": "invalid_pin"})
                    emit('authenticated', {
                        'success': False,
                        'message': "Invalid PIN. Please try again."
                    })
        
        except Exception as e:
            print(f"❌ Authentication error: {e}")
            import traceback
            traceback.print_exc()
            sentry_capture_voice_event("authentication_error", session_id, details={"error": str(e)})
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
            emit('authenticated', {
                'success': False,
                'message': "Authentication error. Please try again."
            })

    @socketio.on('get_livekit_info', namespace='/voice')
    def handle_get_livekit_info():
        """Provide LiveKit connection info for the current session"""
        session_id = request.sid
        print(f"[livekit] Token request from session {session_id}", flush=True)

        if not _livekit_active():
            print("[livekit] LiveKit not configured or unavailable", flush=True)
            emit('livekit_info', {'success': False, 'message': 'LiveKit not configured.'})
            return

        session_data = None
        if redis_manager.is_available():
            session_data = get_session(session_id)
        else:
            session_data = active_sessions.get(session_id)

        if not session_data:
            print(f"[livekit] Session not found for token request: {session_id}", flush=True)
            emit('livekit_info', {'success': False, 'message': 'Session not found.'})
            return

        user_id = session_data.get('user_id') or session_id
        info = _get_livekit_info(session_id, user_id)
        if not info:
            print(f"[livekit] Token unavailable for session {session_id}", flush=True)
            emit('livekit_info', {'success': False, 'message': 'LiveKit token unavailable.'})
            return

        try:
            _ensure_livekit_session(session_id, user_id)
        except Exception as livekit_error:
            print(f"[livekit] Session error: {livekit_error}", flush=True)

        emit('livekit_info', {'success': True, **info})
    
    @socketio.on('client_ready', namespace='/voice')
    def handle_client_ready(data):
        """Handle client ready signal - send pending responses if any"""
        session_id = request.sid
        print(f"✅ Client ready signal received from session {session_id}", flush=True)

        # Send pending welcome greeting (LiveKit) once client is ready
        try:
            session_data = get_session(session_id) if redis_manager.is_available() else active_sessions.get(session_id)
            pending_welcome = None
            pending_name = None
            if session_data:
                pending_welcome = session_data.get('pending_welcome_greeting')
                pending_name = session_data.get('pending_welcome_name')
            if pending_welcome in ['True', True] and pending_name:
                print(f"📣 Sending pending welcome greeting for {pending_name}", flush=True)
                socketio.start_background_task(send_welcome_greeting, session_id, pending_name)
                if redis_manager.is_available():
                    update_session(session_id, {
                        'pending_welcome_greeting': 'False',
                        'pending_welcome_name': ''
                    })
                else:
                    active_sessions[session_id]['pending_welcome_greeting'] = False
                    active_sessions[session_id]['pending_welcome_name'] = ''
        except Exception as pending_welcome_error:
            print(f"⚠️ Failed to send pending welcome greeting: {pending_welcome_error}", flush=True)
        
        # Check if there's a pending response for this session
        if hasattr(socketio, '_pending_responses') and session_id in socketio._pending_responses:
            pending_info = socketio._pending_responses[session_id]
            pending_response = pending_info['response']
            redis_key = pending_info['redis_key']
            user_id = pending_info['user_id']
            original_session_id = pending_info.get('original_session_id')
            
            print(f"📤 Sending pending response to ready client (session {session_id})", flush=True)
            print(f"📤 Response text length: {len(pending_response.get('text', ''))}", flush=True)
            print(f"📤 Response audio length: {len(pending_response.get('audio', ''))}", flush=True)
            
            # Verify session exists and client is actually connected before sending
            current_session = get_session(session_id)
            if not current_session:
                print(f"⚠️ Session {session_id} no longer exists, cannot send pending response", flush=True)
                # Clean up pending response info, but keep in Redis for next reconnect
                try:
                    del socketio._pending_responses[session_id]
                except:
                    pass
                return
            
            # Check if client is actually in the Socket.IO room (actually connected)
            try:
                participants = list(socketio.server.manager.get_participants('/voice', session_id))
                if not participants or len(participants) == 0:
                    print(f"⚠️ Client not in Socket.IO room for session {session_id}, cannot send pending response", flush=True)
                    # Clean up pending response info, but keep in Redis for next reconnect
                    try:
                        del socketio._pending_responses[session_id]
                    except:
                        pass
                    return
                print(f"✅ Client is in Socket.IO room for session {session_id} ({len(participants)} participant(s))", flush=True)
            except Exception as room_check_error:
                print(f"⚠️ Error checking Socket.IO room: {room_check_error}", flush=True)
                # Continue anyway, but log the error
            
            # Send the pending response with acknowledgment callback
            def delivery_callback(ack_data):
                if ack_data and ack_data.get('received'):
                    print(f"✅ Pending response delivery confirmed by client acknowledgment for session {session_id}", flush=True)
                    print(f"✅ Acknowledgment data: {ack_data}", flush=True)
                    # Clean up after confirmed delivery
                    try:
                        if redis_manager.is_available():
                            redis_manager.redis_client.delete(redis_key)
                        if hasattr(socketio, '_pending_responses') and session_id in socketio._pending_responses:
                            del socketio._pending_responses[session_id]
                        print(f"✅ Pending response cleared for user {user_id} (via client_ready, delivery confirmed)", flush=True)
                        sentry_capture_voice_event("pending_response_delivered", session_id, user_id, details={"original_session": original_session_id, "method": "client_ready", "delivery_confirmed": True})
                    except Exception as cleanup_error:
                        print(f"⚠️ Error cleaning up pending response: {cleanup_error}", flush=True)
                else:
                    print(f"⚠️ Pending response delivery NOT confirmed for session {session_id} (ack_data: {ack_data})", flush=True)
                    # Keep pending response in Redis for retry, but clean up session-specific storage
                    try:
                        if hasattr(socketio, '_pending_responses') and session_id in socketio._pending_responses:
                            del socketio._pending_responses[session_id]
                    except:
                        pass
                    sentry_capture_voice_event("pending_response_delivery_failed", session_id, user_id, details={"original_session": original_session_id, "method": "client_ready", "ack_data": ack_data})
            
            # DON'T send large audio payload via Socket.IO - it causes WebSocket encoding errors
            # Instead, send a small notification and let HTTP polling handle the actual delivery
            print(f"📤 Sending pending response notification (client will fetch via HTTP)", flush=True)
            socketio.emit('pending_response_available', {
                'success': True,
                'message': 'Pending response available - fetching via HTTP...',
                'user_id': user_id
            }, namespace='/voice', room=session_id)
            
            print(f"✅ Pending response notification sent - client will fetch via HTTP polling", flush=True)

    @socketio.on('livekit_client_state', namespace='/voice')
    def handle_livekit_client_state(data):
        session_id = request.sid
        try:
            print(f"📡 LiveKit client state ({session_id}): {data}", flush=True)
        except Exception as e:
            print(f"⚠️ LiveKit client state log failed: {e}", flush=True)
    
    
    @socketio.on('start_recording', namespace='/voice')
    def handle_start_recording():
        """Start audio recording"""
        session_id = request.sid
        
        # Guard: Ignore start recording if already processing a previous request
        is_busy = processing_guards.get(session_id)
        if is_busy:
            print(f"⏩ Session {session_id} is BUSY (processing_guard=True), ignoring start_recording", flush=True)
            return
        
        print(f"🎤 [SocketIO] start_recording event received from {session_id}", flush=True)
        
        # Get session data
        session_data = None
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if not session_data:
                emit('error', {'message': 'Session not found'})
                return
        else:
            if session_id not in active_sessions:
                emit('error', {'message': 'Session not found'})
                return
            session_data = active_sessions[session_id]
        
        # Check authentication
        is_authenticated = session_data.get('authenticated') == 'True' if redis_manager.is_available() else session_data.get('authenticated', False)
        if not is_authenticated:
            emit('error', {'message': 'Please authenticate first'})
            return
        
        # Barge-in: stop any active response immediately when user starts speaking
        if session_id in active_response_controls:
            cancel_active_response(session_id, reason="barge_in_start_recording")
        
        print(f"🎤 Recording started: {session_id}")
        
        # Update recording state and clear audio buffer
        if redis_manager.is_available():
            # Clear the audio buffer completely
            redis_client = redis_manager.redis_client
            if redis_client:
                redis_client.hset(f"session:{session_id}", "audio_buffer", "")
                redis_client.hset(f"session:{session_id}", "is_recording", "True")
                print(f"🔍 Debug: cleared Redis audio buffer for session: {session_id}")
            else:
                update_session(session_id, {
                    'is_recording': 'True',
                    'audio_buffer': ''  # Start with empty string for base64 concatenation
                })
        else:
            active_sessions[session_id]['is_recording'] = True
            active_sessions[session_id]['audio_buffer'] = b''  # Start with empty bytes for binary concatenation
            print(f"🔍 Debug: cleared in-memory audio buffer for session: {session_id}")

        # Enable LiveKit input recording (if enabled)
        if _livekit_active():
            try:
                # Use current session_data or fetch fresh
                curr_user_id = session_data.get('user_id') if session_data else session_id
                _ensure_livekit_session(session_id, curr_user_id)
                livekit_manager.set_recording(session_id, True)
                print(f"🎧 LiveKit input recording ACTIVATED for session {session_id}", flush=True)
            except Exception as livekit_error:
                print(f"⚠️ Failed to activate LiveKit recording: {livekit_error}", flush=True)


        # Start streaming STT session for low-latency transcription
        if STREAMING_STT_ENABLED and DEEPGRAM_STREAMING_AVAILABLE and not _livekit_input_active():
            try:
                # Clean up any previous streaming session
                if session_id in streaming_sessions:
                    streaming_sessions[session_id].stop()
                    del streaming_sessions[session_id]

                def on_final_transcript(final_text: str):
                    # Trigger agent processing immediately on speech final
                    socketio.start_background_task(
                        process_audio_async,
                        session_id,
                        None,
                        transcribed_text_override=final_text,
                        use_streaming_tts=True
                    )

                def on_user_speech():
                    # Barge-in: stop current response if user speaks
                    if session_id in active_response_controls:
                        cancel_active_response(session_id, reason="barge_in")

                streaming_session = StreamingSTTSession(
                    session_id=session_id,
                    socketio_instance=socketio,
                    on_final_transcript=on_final_transcript,
                    on_user_speech=on_user_speech
                )
                streaming_session.start()
                streaming_sessions[session_id] = streaming_session
                print(f"✅ Streaming STT session started for {session_id}", flush=True)
            except Exception as stream_error:
                print(f"⚠️ Failed to start streaming STT: {stream_error}", flush=True)
        
        emit('recording_started', {'success': True})
    
    
    @socketio.on('audio_data', namespace='/voice')
    def handle_audio_data(data):
        """Receive audio data chunks from client"""
        session_id = request.sid
        
        # Get session data
        session_data = None
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if not session_data:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "audio_data"})
                return
        else:
            if session_id not in active_sessions:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "audio_data", "storage": "memory"})
                return
            session_data = active_sessions[session_id]
        
        # Check if recording
        is_recording = session_data.get('is_recording') == 'True' if redis_manager.is_available() else session_data.get('is_recording', False)
        if not is_recording:
            sentry_capture_voice_event("audio_received_not_recording", session_id, details={"is_recording": is_recording})
            return
        
        # Append audio chunk to buffer
        audio_chunk = base64.b64decode(data['audio'])
        print(f"🔍 Debug: received audio chunk: {len(audio_chunk)} bytes")

        # Barge-in: cancel current response as soon as user speaks again
        control = active_response_controls.get(session_id)
        if control:
            now = time.time()
            last_barge_in = control.get("last_barge_in", 0.0)
            if now - last_barge_in >= BARge_IN_MIN_INTERVAL_SEC:
                control["last_barge_in"] = now
                cancel_active_response(session_id, reason="barge_in_audio_chunk")

        # Stream chunk to Deepgram STT for low-latency transcription
        streaming_session = streaming_sessions.get(session_id)
        if streaming_session:
            try:
                streaming_session.send_audio(audio_chunk)
            except Exception as stream_error:
                print(f"⚠️ Streaming STT send error: {stream_error}", flush=True)
        
        try:
            if redis_manager.is_available():
                # For Redis, we need to handle binary data differently
                # Store as base64 string in Redis
                current_buffer = session_data.get('audio_buffer', '')
                
                # Decode current buffer to binary, append new chunk, then re-encode
                if current_buffer:
                    try:
                        # Decode current buffer to binary
                        current_binary = base64.b64decode(current_buffer)
                        print(f"🔍 Debug: current buffer decoded to binary, length: {len(current_binary)} bytes")
                        
                        # Append new chunk to binary data
                        combined_binary = current_binary + audio_chunk
                        print(f"🔍 Debug: combined binary length: {len(combined_binary)} bytes")
                        
                        # Re-encode to base64
                        updated_buffer = base64.b64encode(combined_binary).decode('utf-8')
                        print(f"🔍 Debug: re-encoded to base64, length: {len(updated_buffer)} chars")
                        
                    except Exception as e:
                        print(f"⚠️ Error processing current buffer, using only new chunk: {e}")
                        # If current buffer is corrupted, use only new chunk
                        updated_buffer = base64.b64encode(audio_chunk).decode('utf-8')
                        print(f"🔍 Debug: using only new chunk, base64 length: {len(updated_buffer)} chars")
                else:
                    # No current buffer, just encode new chunk
                    updated_buffer = base64.b64encode(audio_chunk).decode('utf-8')
                    print(f"🔍 Debug: new chunk encoded to base64, length: {len(updated_buffer)} chars")
                
                # Validate the final buffer
                try:
                    test_decode = base64.b64decode(updated_buffer)
                    print(f"🔍 Debug: final buffer validation - decoded length: {len(test_decode)} bytes")
                except Exception as e:
                    print(f"❌ Final buffer validation failed: {e}")
                    # This should not happen, but if it does, use only new chunk
                    updated_buffer = base64.b64encode(audio_chunk).decode('utf-8')
                    print(f"🔍 Debug: fallback to new chunk only, length: {len(updated_buffer)} chars")
                
                # Use Redis append operation for better performance
                try:
                    # Get the Redis client directly for append operation
                    redis_client = redis_manager.redis_client
                    if redis_client:
                        # Use Redis HSET to update the audio buffer
                        redis_client.hset(f"session:{session_id}", "audio_buffer", updated_buffer)
                        print(f"🔍 Debug: updated Redis audio buffer: {len(updated_buffer)} chars")
                        sentry_capture_redis_operation("update_audio_buffer", session_id, True)
                    else:
                        # Fallback to update_session method
                        success = update_session(session_id, {'audio_buffer': updated_buffer})
                        if success:
                            print(f"🔍 Debug: updated Redis audio buffer (fallback): {len(updated_buffer)} chars")
                            sentry_capture_redis_operation("update_audio_buffer", session_id, True)
                        else:
                            print(f"❌ Failed to update Redis audio buffer")
                            sentry_capture_redis_operation("update_audio_buffer", session_id, False, "Redis update_session returned False")
                except Exception as redis_error:
                    print(f"❌ Redis direct operation failed: {redis_error}")
                    # Fallback to update_session method
                    success = update_session(session_id, {'audio_buffer': updated_buffer})
                    if success:
                        print(f"🔍 Debug: updated Redis audio buffer (error fallback): {len(updated_buffer)} chars")
                        sentry_capture_redis_operation("update_audio_buffer", session_id, True)
                    else:
                        print(f"❌ Failed to update Redis audio buffer (error fallback)")
                        sentry_capture_redis_operation("update_audio_buffer", session_id, False, f"Redis error: {redis_error}")
            else:
                # In-memory storage
                active_sessions[session_id]['audio_buffer'] += audio_chunk
                print(f"🔍 Debug: updated in-memory audio buffer: {len(active_sessions[session_id]['audio_buffer'])} bytes")
                sentry_capture_voice_event("audio_buffer_updated", session_id, details={"storage": "memory", "buffer_size": len(active_sessions[session_id]['audio_buffer'])})
        except Exception as e:
            print(f"❌ Error updating audio buffer: {e}")
            sentry_capture_redis_operation("update_audio_buffer", session_id, False, str(e))
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
    
    
    @socketio.on('stop_recording', namespace='/voice')
    def handle_stop_recording(data=None):
        """Stop recording and process audio"""
        session_id = request.sid
        
        print(f"🛑 [SocketIO] stop_recording event received from {session_id}", flush=True)
        
        # Guard: Ignore redundant stop events if already processing
        if processing_guards.get(session_id):
            print(f"⏩ Session {session_id} is BUSY (processing_guard=True), ignoring stop_recording", flush=True)
            return
        
        # Capture stop recording event in Sentry
        sentry_capture_voice_event("stop_recording", session_id)
        
        # Get session data
        session_data = None
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if not session_data:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "stop_recording"})
                emit('error', {'message': 'Session not found'})
                return
        else:
            if session_id not in active_sessions:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "stop_recording", "storage": "memory"})
                emit('error', {'message': 'Session not found'})
                return
            session_data = active_sessions[session_id]
        
        # Check if recording
        is_recording = session_data.get('is_recording') == 'True' if redis_manager.is_available() else session_data.get('is_recording', False)
        if not is_recording:
            sentry_capture_voice_event("stop_recording_not_recording", session_id, details={"is_recording": is_recording})
            emit('error', {'message': 'Not recording'})
            return
        
        print(f"🛑 Recording stopped: {session_id}")
        
        # Update recording state
        try:
            if redis_manager.is_available():
                success = update_session(session_id, {'is_recording': 'False'})
                if success:
                    sentry_capture_redis_operation("update_recording_state", session_id, True)
                else:
                    sentry_capture_redis_operation("update_recording_state", session_id, False, "Redis update_session returned False")
            else:
                session_data['is_recording'] = False
                sentry_capture_voice_event("recording_state_updated", session_id, details={"storage": "memory"})
        except Exception as e:
            print(f"❌ Error updating recording state: {e}")
            sentry_capture_redis_operation("update_recording_state", session_id, False, str(e))

        # If streaming STT is active, stop the stream and return (skip batch transcription)
        if STREAMING_STT_ENABLED and session_id in streaming_sessions:
            try:
                streaming_sessions[session_id].stop()
                del streaming_sessions[session_id]
                print(f"🛑 Streaming STT session stopped for {session_id}", flush=True)
            except Exception as stop_error:
                print(f"⚠️ Error stopping streaming STT: {stop_error}", flush=True)
            emit('recording_stopped', {'success': True, 'streaming': True})
            return
        
        # Get audio buffer - prefer LiveKit input when enabled
        audio_buffer = None
        if _livekit_input_active():
            try:
                # Allow a short grace period for final audio frames to arrive
                try:
                    socketio.sleep(0.35)
                except Exception:
                    time.sleep(0.35)
                audio_buffer = livekit_manager.pop_audio_buffer(session_id)
                if audio_buffer:
                    print(f"🎧 LiveKit audio buffer captured: {len(audio_buffer)} bytes", flush=True)
                    sentry_capture_voice_event("audio_buffer_retrieved", session_id, details={"storage": "livekit", "buffer_size": len(audio_buffer)})
                else:
                    print("⚠️ LiveKit audio buffer empty", flush=True)
            except Exception as livekit_pop_error:
                print(f"⚠️ Failed to pop LiveKit audio buffer: {livekit_pop_error}", flush=True)
                audio_buffer = None
            finally:
                try:
                    livekit_manager.set_recording(session_id, False)
                except Exception as livekit_stop_error:
                    print(f"⚠️ Failed to disable LiveKit recording: {livekit_stop_error}", flush=True)
        
        # Check if audio data is provided directly from client
        if audio_buffer is None and data and 'audio' in data:
            try:
                # Preserve base64 for Redis audio player, and decode for processing
                audio_buffer_b64_from_client = data['audio']
                audio_buffer = base64.b64decode(audio_buffer_b64_from_client)
                print(f"🎵 Received complete WebM blob from client: {len(audio_buffer)} bytes")
                sentry_capture_voice_event("audio_blob_received", session_id, details={"buffer_size": len(audio_buffer), "source": "client"})

                # Store the complete base64 blob into Redis/in-memory for the audio player tool
                try:
                    if redis_manager.is_available():
                        stored = update_session(session_id, {'audio_buffer': audio_buffer_b64_from_client})
                        if stored:
                            print(f"💾 Stored complete audio blob in Redis for session {session_id}: {len(audio_buffer_b64_from_client)} chars")
                            sentry_capture_redis_operation("store_audio_blob_on_stop", session_id, True)
                        else:
                            print("⚠️ Redis update_session returned False while storing audio blob")
                            sentry_capture_redis_operation("store_audio_blob_on_stop", session_id, False, "update_session returned False")
                    else:
                        session_data['audio_buffer'] = audio_buffer_b64_from_client
                        print(f"💾 Stored complete audio blob in memory for session {session_id}: {len(audio_buffer_b64_from_client)} chars")
                        sentry_capture_voice_event("audio_blob_stored_memory", session_id, details={"length": len(audio_buffer_b64_from_client)})
                except Exception as store_err:
                    print(f"⚠️ Failed to store audio blob for audio player: {store_err}")
                    sentry_capture_redis_operation("store_audio_blob_on_stop", session_id, False, str(store_err))
            except Exception as decode_error:
                print(f"❌ Error decoding client audio blob: {decode_error}")
                sentry_capture_voice_event("audio_decode_error", session_id, details={"error": str(decode_error), "source": "client"})
                emit('transcription', {
                    'success': False,
                    'message': 'Error decoding audio data.'
                })
                return
        elif audio_buffer is None:
            # Fallback to session buffer (legacy)
            try:
                if redis_manager.is_available():
                    audio_buffer_b64 = session_data.get('audio_buffer', '')
                    if not audio_buffer_b64:
                        print("❌ No audio data in Redis session")
                        sentry_capture_voice_event("no_audio_data", session_id, details={"storage": "redis"})
                        emit('transcription', {
                            'success': False,
                            'message': 'No audio data received.'
                        })
                        return
                    
                    audio_buffer = base64.b64decode(audio_buffer_b64)
                    print(f"🔍 Debug: decoded session audio_buffer length: {len(audio_buffer)}")
                    sentry_capture_voice_event("audio_buffer_retrieved", session_id, details={"storage": "redis", "buffer_size": len(audio_buffer)})
                else:
                    audio_buffer = session_data['audio_buffer']
                    print(f"🔍 Debug: in-memory audio_buffer length: {len(audio_buffer)}")
                    sentry_capture_voice_event("audio_buffer_retrieved", session_id, details={"storage": "memory", "buffer_size": len(audio_buffer)})
            except Exception as e:
                print(f"❌ Error retrieving session audio buffer: {e}")
                sentry_capture_voice_event("audio_buffer_error", session_id, details={"error": str(e)})
                if SENTRY_AVAILABLE:
                    sentry_sdk.capture_exception(e)
                emit('error', {'message': 'Error retrieving audio data'})
                return
        
        if audio_buffer is None and _livekit_input_active():
            emit('transcription', {
                'success': False,
                'message': 'No LiveKit audio received. Please check microphone permissions and try again.'
            })
            return

        # Check minimum audio length for meaningful speech recognition
        # WebRTC chunks are very small, so we need a much lower threshold
        min_audio_length = 10000  # Much lower threshold for WebRTC
        if len(audio_buffer) < min_audio_length:
            sentry_capture_voice_event("audio_too_short", session_id, details={"buffer_size": len(audio_buffer), "threshold": min_audio_length})
            emit('transcription', {
                'success': False,
                'message': f'Audio too short ({len(audio_buffer)} bytes). Please speak longer. Try saying "Create a todo task to buy groceries" and hold the button much longer.'
            })
            return
        
        # Audio analysis moved to background task to avoid blocking event loop
        # Process audio asynchronously
        sentry_capture_voice_event("audio_processing_started", session_id, details={"buffer_size": len(audio_buffer)})
        socketio.start_background_task(
            process_audio_async,
            session_id,
            audio_buffer,
            use_streaming_tts=(STREAMING_TTS_ENABLED and DEEPGRAM_STREAMING_AVAILABLE)
        )
    
    
    def send_welcome_greeting(session_id, user_name):
        """Send welcome greeting with TTS audio after authentication"""
        def _greeting_task():
            with flask_app.app_context():
                try:
                    print(f"🎤 Generating welcome greeting for {user_name}")
                    
                    # Generate welcome message
                    welcome_text = f"Welcome back, {user_name}! I'm your Convonet productivity assistant. How can I help you today?"
                    
                    # Generate TTS audio using Deepgram
                    deepgram_tts = get_deepgram_tts_service()
                    if _livekit_active():
                        audio_bytes = deepgram_tts.synthesize_speech(
                            welcome_text,
                            voice="aura-asteria-en",
                            encoding="linear16",
                            sample_rate=24000,
                            container="none"
                        )
                    else:
                        audio_bytes = deepgram_tts.synthesize_speech(welcome_text, voice="aura-asteria-en")
                    
                    if not audio_bytes:
                        raise Exception("Deepgram TTS failed to generate audio")
                    
                    if _livekit_active():
                        # Use the correct bridge send_pcm method via livekit_manager
                        session = livekit_manager.get_session(session_id)
                        if session:
                            session.send_pcm(audio_bytes, sample_rate=24000, channels=1)
                        
                        socketio.emit('welcome_greeting', {
                            'text': welcome_text
                        }, namespace='/voice', room=session_id)
                    else:
                        # Convert to base64
                        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                        # Send to client
                        socketio.emit('welcome_greeting', {
                            'text': welcome_text,
                            'audio': audio_base64
                        }, namespace='/voice', room=session_id)
                    
                    print(f"✅ Welcome greeting sent to {user_name}")
                    
                except Exception as e:
                    print(f"❌ Error generating welcome greeting: {e}")
        
        socketio.start_background_task(_greeting_task)
    
    
    def process_audio_async(session_id, audio_buffer, transcribed_text_override: Optional[str] = None, use_streaming_tts: bool = False):
        """Process audio in background task"""
        import sys
        buffer_size = len(audio_buffer) if audio_buffer else 0
        print(f"🚀 process_audio_async STARTED for session: {session_id}, buffer size: {buffer_size}", flush=True)
        
        # Throttling: Ignore tiny buffers
        if buffer_size < 10000 and not transcribed_text_override:
            print(f"⚠️ Audio buffer too small ({buffer_size} bytes), skipping processing", flush=True)
            return
        
        # Set guard
        processing_guards[session_id] = True
        try:
            if audio_buffer and len(audio_buffer) > 0:
                is_webm = len(audio_buffer) >= 4 and audio_buffer[:4] == b"\x1a\x45\xdf\xa3"
                if not is_webm:
                    import numpy as np
                    analysis_buf = audio_buffer
                    if len(analysis_buf) % 2 != 0: analysis_buf = analysis_buf[:-1]
                    audio_data = np.frombuffer(analysis_buf, dtype=np.int16)
                    rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
                    unique_vals = len(np.unique(audio_data))
                    print(f"🔍 Background Audio Analysis: RMS={rms:.2f}, Unique={unique_vals}, Samples={len(audio_data)}")
                    if rms < 100 or unique_vals < 10:
                        print(f"⚠️ Audio quality too low or silent (RMS={rms:.2f}), skipping.", flush=True)
                        return
        except Exception as e:
            print(f"⚠️ Background audio analysis failed: {e}")

        sys.stdout.flush()
        # Use the stored Flask app instance for application context
        print(f"🔧 Entering Flask app context...", flush=True)
        sys.stdout.flush()
        with flask_app.app_context():
            print(f"✅ Flask app context entered", flush=True)
            sys.stdout.flush()
            try:
                # Get session data
                print(f"🔍 Getting session data from Redis/memory...", flush=True)
                sys.stdout.flush()
                session = None
                session_record = None
                if redis_manager.is_available():
                    print(f"📦 Redis is available, getting session from Redis...", flush=True)
                    sys.stdout.flush()
                    session_data = get_session(session_id)
                    if not session_data:
                        sentry_capture_voice_event("session_not_found_processing", session_id, details={"operation": "audio_processing"})
                        return
                    session_record = session_data
                    # Convert Redis session data to expected format
                    session = {
                        'user_id': session_data.get('user_id'),
                        'user_name': session_data.get('user_name')
                    }
                else:
                    session = active_sessions.get(session_id)
                    if not session:
                        sentry_capture_voice_event("session_not_found_processing", session_id, details={"operation": "audio_processing", "storage": "memory"})
                        return
                    session_record = session
                
                print(f"🎧 Processing audio: {len(audio_buffer)} bytes")
                sentry_capture_voice_event("audio_processing_started", session_id, session.get('user_id'), details={"buffer_size": len(audio_buffer)})
                
                # Step 1: Transcribe audio (skip if streaming STT already provided text)
                if transcribed_text_override:
                    transcribed_text = transcribed_text_override
                else:
                    socketio.emit('status', {'message': 'Transcribing with Deepgram...'}, namespace='/voice', room=session_id)
                    sentry_capture_voice_event("transcription_started", session_id, session.get('user_id'), details={"method": "deepgram"})
                    
                    # Use Deepgram for transcription (WebRTC-optimized solution)
                    print(f"🎧 Deepgram: Processing audio buffer: {len(audio_buffer) if audio_buffer else 0} bytes")
                    
                    # Use Deepgram integration with fixed English language (no auto-detection)
                    # STT should be forced to English to avoid mis-detecting other languages
                    import sys
                    print(f"🔧 About to call transcribe_audio_with_deepgram_webrtc with language='en'...", flush=True)
                    sys.stdout.flush()
                    try:
                        # Force English transcription only (disable auto-detection)
                        transcribed_text = transcribe_audio_with_deepgram_webrtc(audio_buffer, language="en")
                        print(f"✅ transcribe_audio_with_deepgram_webrtc returned: {transcribed_text[:50] if transcribed_text else 'None'}...", flush=True)
                        sys.stdout.flush()
                    except Exception as e:
                        print(f"❌ Deepgram integration failed: {e}", flush=True)
                        sys.stdout.flush()
                        import traceback
                        traceback.print_exc()
                        socketio.emit('error', {'message': 'Deepgram service not available. Please check configuration.'}, namespace='/voice', room=session_id)
                        sentry_capture_voice_event("transcription_failed", session_id, session.get('user_id'), details={"method": "deepgram", "error": str(e)})
                        return
                
                print(f"🔍 Checking if transcribed_text is empty...", flush=True)
                sys.stdout.flush()
                if not transcribed_text:
                    print("❌ Deepgram transcription failed")
                    socketio.emit('error', {
                        'message': 'Transcription failed. Please try speaking more clearly or check your microphone.',
                        'details': 'The audio was captured but no speech was detected. Make sure you are speaking clearly into your microphone.'
                    }, namespace='/voice', room=session_id)
                    sentry_capture_voice_event("transcription_failed", session_id, session.get('user_id'), details={"method": "deepgram"})
                    return
                
                print(f"✅ Deepgram transcription successful: {transcribed_text}", flush=True)
                sys.stdout.flush()
                print(f"📝 About to call sentry_capture_voice_event...", flush=True)
                sys.stdout.flush()
                sentry_capture_voice_event("transcription_completed", session_id, session.get('user_id'), details={"text_length": len(transcribed_text), "method": "deepgram"})
                print(f"✅ sentry_capture_voice_event completed", flush=True)
                sys.stdout.flush()
                
                # Send transcription to client
                print(f"📤 Sending transcription to client...", flush=True)
                sys.stdout.flush()
                socketio.emit('transcription', {
                    'success': True,
                    'text': transcribed_text,
                    'method': 'assemblyai'
                }, namespace='/voice', room=session_id)
                print(f"✅ Transcription sent to client", flush=True)
                sys.stdout.flush()
                
                print(f"🔍 Checking for transfer intent...", flush=True)
                sys.stdout.flush()
                transfer_requested = has_transfer_intent(transcribed_text)
                print(f"✅ Transfer intent check complete: {transfer_requested}", flush=True)
                sys.stdout.flush()
                
                def start_transfer_flow(target_extension: str, department: str, reason: str, source: str = "agent"):
                    print(f"🔄 Transfer requested: Extension={target_extension}, Department={department}, Reason={reason}")
                    if is_transfer_in_progress(session_id, session_record):
                        print(f"⚠️ Transfer already in progress for session {session_id}, skipping duplicate request")
                        return
                    set_transfer_flag(session_id, True, session_record)
                    sentry_capture_voice_event("transfer_initiated", session_id, session.get('user_id'), details={
                        "extension": target_extension,
                        "department": department,
                        "reason": reason,
                        "platform": "webrtc",
                        "source": source
                    })
                    
                    # Cache customer profile with call_id=session_id for WebRTC calls
                    cache_call_center_profile(target_extension, session_record, call_id=session_id)
                    
                    transfer_instructions = {
                        'extension': target_extension,
                        'department': department,
                        'reason': reason
                    }
                    
                    transfer_success, transfer_details = initiate_agent_transfer(
                        session_id=session_id,
                        extension=target_extension,
                        department=department,
                        reason=reason,
                        session_data=session_record
                    )
                    if not transfer_success:
                        set_transfer_flag(session_id, False, session_record)

                    transfer_message_text = f"I'm transferring you to {department} (extension {target_extension})."

                    socketio.emit('transfer_initiated', {
                        'success': True,
                        'extension': target_extension,
                        'department': department,
                        'reason': reason,
                        'instructions': transfer_instructions,
                        'message': transfer_message_text,
                        'call_started': transfer_success,
                        'call_details': transfer_details
                    }, namespace='/voice', room=session_id)

                    socketio.emit('transfer_status', {
                        'success': transfer_success,
                        'details': transfer_details
                    }, namespace='/voice', room=session_id)

                    print(f"🔄 Transfer instructions sent to WebRTC client for extension {target_extension}")

                    transfer_message = f"I'm transferring you to {department}. Extension {target_extension}."
                    try:
                        # Generate TTS audio using Deepgram
                        if _livekit_active():
                            audio_bytes = _synthesize_deepgram_linear16(transfer_message)
                        else:
                            deepgram_tts = get_deepgram_tts_service()
                            audio_bytes = deepgram_tts.synthesize_speech(transfer_message, voice="aura-asteria-en")
                        
                        if not audio_bytes:
                            raise Exception("Deepgram TTS failed to generate audio")

                        if _livekit_active():
                            _send_livekit_pcm(session_id, audio_bytes, sample_rate=24000, channels=1)
                            socketio.emit('agent_response', {
                                'success': True,
                                'text': transfer_message,
                                'audio': '',
                                'transfer': True
                            }, namespace='/voice', room=session_id)
                        else:
                            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                            socketio.emit('agent_response', {
                                'success': True,
                                'text': transfer_message,
                                'audio': audio_base64,
                                'transfer': True
                            }, namespace='/voice', room=session_id)
                    except Exception as e:
                        print(f"❌ Error generating TTS for transfer: {e}")
                
                if transfer_requested:
                    print(f"🔄 Transfer requested, starting transfer flow...", flush=True)
                    sys.stdout.flush()
                    start_transfer_flow('2001', 'support', 'User requested transfer to human agent', source="caller_intent")
                    return

                # Step 2: Process with agent
                print(f"🚀 About to emit status message...", flush=True)
                sys.stdout.flush()
                socketio.emit('status', {'message': 'Processing request...'}, namespace='/voice', room=session_id)
                print(f"✅ Status message emitted", flush=True)
                sys.stdout.flush()

                # LATENCY OPTIMIZATION: Send a short acknowledgement audio ASAP (batch TTS only)
                # For streaming TTS, we skip this to avoid overlapping audio.
                if not use_streaming_tts:
                    try:
                        ack_text = "One moment while I check that."
                        if _livekit_active():
                            ack_audio = _synthesize_deepgram_linear16(ack_text)
                            if ack_audio:
                                _send_livekit_pcm(session_id, ack_audio, sample_rate=24000, channels=1)
                                print("📤 Emitted LiveKit acknowledgement audio", flush=True)
                        else:
                            deepgram_tts = get_deepgram_tts_service()
                            if deepgram_tts:
                                ack_audio = deepgram_tts.synthesize_speech(ack_text, voice="aura-asteria-en")
                                if ack_audio:
                                    ack_base64 = base64.b64encode(ack_audio).decode('utf-8')
                                    emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                                    if emit_socketio:
                                        emit_socketio.emit('audio_chunk', {
                                            'success': True,
                                            'chunk_index': 0,
                                            'total_chunks': 1,
                                            'audio': ack_base64,
                                            'is_final': True,
                                            'is_ack': True
                                        }, namespace='/voice', room=session_id)
                                        print("📤 Emitted acknowledgement audio chunk", flush=True)
                    except Exception as ack_error:
                        print(f"⚠️ Ack audio generation failed: {ack_error}", flush=True)
                
                print(f"📝 About to call sentry_capture_voice_event for agent_processing_started...", flush=True)
                sys.stdout.flush()
                sentry_capture_voice_event("agent_processing_started", session_id, session.get('user_id'), details={"transcribed_text": transcribed_text})
                print(f"✅ sentry_capture_voice_event for agent_processing_started completed", flush=True)
                sys.stdout.flush()
                
                # LATENCY OPTIMIZATION: Prepare TTS settings EARLY (before agent processing)
                # This allows us to start TTS generation as soon as first sentence arrives
                user_id = session.get('user_id')
                voice_prefs = get_voice_preferences() if ELEVENLABS_AVAILABLE else None
                tts_provider = "deepgram"  # Default fallback
                use_elevenlabs = False
                voice_id = None
                language = "en"
                emotion_enabled = False
                emotion = None
                
                # Determine TTS provider and settings (early, before agent processing)
                if ELEVENLABS_AVAILABLE and voice_prefs:
                    try:
                        elevenlabs = get_elevenlabs_service()
                        if elevenlabs and elevenlabs.is_available():
                            prefs = voice_prefs.get_user_preferences(user_id) if user_id else voice_prefs._get_default_preferences()
                            if prefs.get("use_elevenlabs", True):
                                use_elevenlabs = True
                                tts_provider = "elevenlabs"
                                voice_id = prefs.get("voice_id")
                                language = prefs.get("language", "en")
                                emotion_enabled = prefs.get("emotion_enabled", True)
                                # Note: Emotion detection will happen after we get first sentence
                    except Exception as e:
                        print(f"⚠️ ElevenLabs setup failed, falling back to Deepgram: {e}", flush=True)
                        use_elevenlabs = False
                        tts_provider = "deepgram"

                if _livekit_active() and use_elevenlabs:
                    print("ℹ️ LiveKit active - disabling ElevenLabs to keep PCM audio", flush=True)
                    use_elevenlabs = False
                    tts_provider = "deepgram"
                
                print(f"🤖 Starting agent processing for: {transcribed_text[:100]}", flush=True)
                sys.stdout.flush()
                print(f"🔧 About to call process_with_agent in separate thread...", flush=True)
                sys.stdout.flush()
                
                # LATENCY OPTIMIZATION: Track accumulated text for early TTS generation (defined in outer scope)
                import threading
                text_accumulator = {
                    'text': '',
                    'lock': threading.Lock(),
                    'first_sentence': None,
                    'first_sentence_ready': threading.Event(),
                    'tts_started': False,
                    'tts_started_event': threading.Event(),
                    'early_audio_chunks': [],
                    'early_chunk_index': 0
                }

                # Streaming TTS (Deepgram) for full-duplex low latency
                streaming_tts = None
                response_cancel_event = threading.Event()
                register_active_response(session_id, response_cancel_event, None)
                if use_streaming_tts and STREAMING_TTS_ENABLED and DEEPGRAM_STREAMING_AVAILABLE:
                    try:
                        streaming_tts = StreamingTTSStream(
                            session_id,
                            socketio,
                            STREAMING_TTS_MODEL,
                            use_livekit_audio=_livekit_active()
                        )
                        streaming_tts.start()
                        register_active_response(session_id, response_cancel_event, streaming_tts)
                    except Exception as stream_tts_error:
                        print(f"⚠️ Failed to start streaming TTS: {stream_tts_error}", flush=True)
                        streaming_tts = None
                
                # Define text chunk callback for early TTS (must be defined before run_async_in_thread)
                def text_chunk_callback(chunk_text: str):
                    """Callback to accumulate text chunks and trigger early/streaming TTS"""
                    if response_cancel_event.is_set():
                        return

                    # Streaming TTS path: pipe text directly to Deepgram
                    if streaming_tts:
                        streaming_tts.send_text(chunk_text)
                        text_accumulator['tts_started'] = True
                        text_accumulator['tts_started_event'].set()
                        return

                    with text_accumulator['lock']:
                        text_accumulator['text'] += chunk_text
                        current_text = text_accumulator['text']
                        
                        # Check if we have a complete sentence (ends with . ! ?)
                        if text_accumulator['first_sentence'] is None:
                            # Try to extract first complete sentence
                            sentences = re.split(r'(?<=[.!?])\s+', current_text)
                            if len(sentences) > 0 and sentences[0].strip():
                                first_sentence = sentences[0].strip()
                                # Minimum sentence length to start TTS (avoid fragments)
                                if len(first_sentence) > 20:
                                    text_accumulator['first_sentence'] = first_sentence
                                    text_accumulator['first_sentence_ready'].set()
                                    print(f"🚀 FIRST SENTENCE DETECTED: {first_sentence[:80]}...", flush=True)
                
                filler_sent = {"sent": False}
                filler_texts = [
                    "One moment while I check that.",
                    "Let me look that up for you.",
                    "Sure, checking that now."
                ]

                def tool_call_callback(tool_name: str):
                    """Send a filler phrase immediately when a tool call is detected."""
                    if response_cancel_event.is_set() or filler_sent["sent"]:
                        return
                    filler_sent["sent"] = True
                    filler_text = filler_texts[int(time.time()) % len(filler_texts)]
                    try:
                        if streaming_tts:
                            streaming_tts.send_text(filler_text + " ")
                            return
                        if _livekit_active():
                            filler_audio = _synthesize_deepgram_linear16(filler_text)
                            if filler_audio:
                                _send_livekit_pcm(session_id, filler_audio, sample_rate=24000, channels=1)
                                return
                        deepgram_tts = get_deepgram_tts_service()
                        if deepgram_tts:
                            filler_audio = deepgram_tts.synthesize_speech(filler_text, voice="aura-asteria-en")
                            if filler_audio:
                                filler_base64 = base64.b64encode(filler_audio).decode('utf-8')
                                emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                                if emit_socketio:
                                    emit_socketio.emit('audio_chunk', {
                                        'success': True,
                                        'chunk_index': 0,
                                        'total_chunks': 1,
                                        'audio': filler_base64,
                                        'is_final': True,
                                        'is_filler': True
                                    }, namespace='/voice', room=session_id)
                    except Exception as filler_error:
                        print(f"⚠️ Filler TTS failed: {filler_error}", flush=True)
                
                try:
                    print(f"🔧 Setting up ThreadPoolExecutor...", flush=True)
                    sys.stdout.flush()
                    # Use eventlet's spawn_n to run async code in completely separate greenlet
                    # This prevents blocking the main eventlet worker
                    import eventlet
                    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
                    
                    result_container = {'response': None, 'transfer': None, 'error': None, 'done': False}
                    
                    print(f"🔧 Defining run_async_in_thread function...", flush=True)
                    sys.stdout.flush()
                    def run_async_in_thread():
                        """Run async function in a new thread with its own event loop"""
                        import sys
                        print(f"🧵 Thread started for async execution", flush=True)
                        sys.stdout.flush()
                        
                        # Create new event loop for this thread
                        print(f"🔧 Creating new event loop in thread...", flush=True)
                        sys.stdout.flush()
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        print(f"✅ Event loop created and set", flush=True)
                        sys.stdout.flush()
                        
                        # Use timeout that matches routes.py execution_timeout (15s for Claude/OpenAI, 12s for Gemini)
                        # Add buffer for tool execution which can be slow (MCP calls, API calls, etc.)
                        # Increased to 60s to allow for database operations and external API calls
                        # Tool execution (calendar event creation, database operations) can take time
                        timeout_seconds = 60.0  # Increased from 20s to allow tool execution time
                        try:
                            print(f"🔄 Running process_with_agent in thread (timeout: {timeout_seconds}s)...", flush=True)
                            sys.stdout.flush()
                            result = new_loop.run_until_complete(
                                asyncio.wait_for(
                                    process_with_agent(
                                        transcribed_text,
                                        session['user_id'],
                                        session['user_name'],
                                        socketio=socketio_instance,
                                        session_id=session_id,
                                        text_chunk_callback=text_chunk_callback,  # Pass callback for early TTS (from outer scope)
                                        tool_call_callback=tool_call_callback     # Pass callback for filler on tool calls
                                    ),
                                    timeout=timeout_seconds
                                )
                            )
                            print(f"✅ process_with_agent completed in thread", flush=True)
                            sys.stdout.flush()
                            result_container['response'] = result[0]
                            result_container['transfer'] = result[1]
                            result_container['done'] = True
                            return result
                        except asyncio.TimeoutError:
                            print(f"⏱️ Async timeout in thread after {timeout_seconds} seconds", flush=True)
                            sys.stdout.flush()
                            result_container['error'] = 'timeout'
                            result_container['done'] = True
                            raise
                        except Exception as e:
                            print(f"❌ Error in thread: {e}", flush=True)
                            sys.stdout.flush()
                            import traceback
                            traceback.print_exc()
                            result_container['error'] = str(e)
                            result_container['done'] = True
                            raise
                        finally:
                            print(f"🧵 Closing event loop...", flush=True)
                            sys.stdout.flush()
                            try:
                                # Cancel any pending tasks
                                pending = asyncio.all_tasks(new_loop)
                                for task in pending:
                                    task.cancel()
                                new_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                            except:
                                pass
                            new_loop.close()
                            print(f"🧵 Thread event loop closed", flush=True)
                            sys.stdout.flush()
                    
                    # Run in thread pool with aggressive timeout for Gemini hackathon
                    print(f"🚀 Submitting to ThreadPoolExecutor...", flush=True)
                    sys.stdout.flush()
                    
                    # Function to generate early TTS for first sentence
                    def generate_early_tts(first_sentence: str):
                        """Generate TTS for first sentence in background thread"""
                        try:
                            print(f"🎵 Starting early TTS generation for first sentence...", flush=True)
                            socketio.emit('status', {'message': 'Generating speech...'}, namespace='/voice', room=session_id)
                            
                            # Use TTS settings prepared earlier
                            chunk_audio = None

                            # Prefer Deepgram for early TTS (typically faster) to reduce time-to-first-audio
                            if _livekit_active():
                                chunk_audio = _synthesize_deepgram_linear16(first_sentence)
                            else:
                                deepgram_tts_service = get_deepgram_tts_service()
                                if deepgram_tts_service:
                                    chunk_audio = deepgram_tts_service.synthesize_speech(first_sentence, voice="aura-asteria-en")

                            if not chunk_audio and use_elevenlabs and not _livekit_active():
                                try:
                                    elevenlabs_service = get_elevenlabs_service()
                                    if elevenlabs_service and elevenlabs_service.is_available():
                                        # For early TTS, use basic emotion (will refine later with full response)
                                        chunk_audio = elevenlabs_service.synthesize(
                                            text=first_sentence,
                                            voice_id=voice_id
                                        )
                                except Exception as e:
                                    print(f"⚠️ Early ElevenLabs TTS failed: {e}", flush=True)
                            
                            if chunk_audio:
                                if _livekit_active():
                                    _send_livekit_pcm(session_id, chunk_audio, sample_rate=24000, channels=1)
                                    print(f"📤 Emitted EARLY LiveKit audio chunk ({len(chunk_audio)} bytes)", flush=True)
                                else:
                                    chunk_base64 = base64.b64encode(chunk_audio).decode('utf-8')
                                    with text_accumulator['lock']:
                                        text_accumulator['early_audio_chunks'].append({
                                            'chunk_index': text_accumulator['early_chunk_index'],
                                            'audio': chunk_base64,
                                            'is_final': False
                                        })
                                        text_accumulator['early_chunk_index'] += 1
                                    
                                    # Emit early audio chunk immediately
                                    emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                                    if emit_socketio:
                                        try:
                                            emit_socketio.emit('audio_chunk', {
                                                'success': True,
                                                'chunk_index': 0,
                                                'total_chunks': -1,  # Unknown at this point
                                                'audio': chunk_base64,
                                                'is_final': False,
                                                'is_early': True  # Mark as early chunk
                                            }, namespace='/voice', room=session_id)
                                            print(f"📤 Emitted EARLY audio chunk ({len(chunk_base64)} chars)", flush=True)
                                        except Exception as emit_error:
                                            print(f"⚠️ Error emitting early chunk: {emit_error}", flush=True)
                            else:
                                print(f"⚠️ Failed to generate early TTS audio", flush=True)
                        except Exception as e:
                            print(f"❌ Error in early TTS generation: {e}", flush=True)
                            import traceback
                            traceback.print_exc()
                    
                    with ThreadPoolExecutor(max_workers=2) as executor:  # Increased to 2 for early TTS thread
                        print(f"✅ ThreadPoolExecutor created, submitting task...", flush=True)
                        sys.stdout.flush()
                        future = executor.submit(run_async_in_thread)
                        print(f"✅ Task submitted to executor, future created", flush=True)
                        sys.stdout.flush()
                        
                        if streaming_tts:
                            print("🔊 Streaming TTS active - skipping early sentence TTS", flush=True)
                        else:
                            # LATENCY OPTIMIZATION: Wait for first sentence and start TTS early
                            # This allows TTS to start while agent is still processing (tool execution, etc.)
                            print(f"⏳ Waiting for first sentence (max 10s)...", flush=True)
                            first_sentence_ready = text_accumulator['first_sentence_ready'].wait(timeout=10.0)
                            
                            if first_sentence_ready and text_accumulator['first_sentence']:
                                first_sentence = text_accumulator['first_sentence']
                                print(f"✅ First sentence ready! Starting early TTS: {first_sentence[:80]}...", flush=True)
                                # Start early TTS generation in background thread
                                early_tts_future = executor.submit(generate_early_tts, first_sentence)
                            else:
                                print(f"⏳ First sentence not ready yet, continuing...", flush=True)
                        
                        # Use 60s timeout to allow for tool execution (database operations, API calls)
                        # Tool execution (MCP calls, API calls, database operations) can take time
                        # Increased from 25s to 60s to handle complex operations like calendar event creation
                        executor_timeout = 90.0  # Increased for MCP tools loading (was 60s)
                        try:
                            print(f"⏳ Waiting for agent result with {executor_timeout}s timeout...", flush=True)
                            sys.stdout.flush()
                            
                            # Get final result (wait for completion)
                            agent_response, transfer_marker = future.result(timeout=executor_timeout)
                            print(f"🤖 Agent response received: {agent_response[:100] if agent_response else 'None'}", flush=True)
                            sys.stdout.flush()
                        except FutureTimeoutError:
                            print(f"⏱️ ThreadPoolExecutor timed out after {executor_timeout} seconds", flush=True)
                            sys.stdout.flush()
                            agent_response = "I'm sorry, I'm taking too long to process that request. Please try a simpler request or try again."
                            transfer_marker = None
                            # Cancel the future if possible
                            try:
                                future.cancel()
                                print(f"✅ Future cancelled", flush=True)
                                sys.stdout.flush()
                            except:
                                pass
                        except asyncio.TimeoutError as e:
                            print(f"⏱️ Async timeout: {e}", flush=True)
                            sys.stdout.flush()
                            agent_response = "I'm sorry, I'm taking too long to process that request. Please try a simpler request or switch to Claude model."
                            transfer_marker = None
                        except Exception as e:
                            print(f"❌ Exception in ThreadPoolExecutor: {e}", flush=True)
                            sys.stdout.flush()
                            import traceback
                            traceback.print_exc()
                            agent_response = "I'm sorry, I encountered an error. Please try again."
                            transfer_marker = None
                except asyncio.TimeoutError:
                    print(f"⏱️ Agent processing timed out after 18 seconds (async timeout)")
                    agent_response = "I'm sorry, I'm taking too long to process that request. Please try a simpler request."
                    transfer_marker = None
                except Exception as e:
                    print(f"❌ Error in agent processing: {e}")
                    import traceback
                    traceback.print_exc()
                    agent_response = "I'm sorry, I encountered an error. Please try again."
                    transfer_marker = None
                sentry_capture_voice_event("agent_processing_completed", session_id, session.get('user_id'), details={"response_length": len(agent_response)})
                if response_cancel_event.is_set():
                    print("🛑 Response cancelled before TTS generation (barge-in)", flush=True)
                    if session_id in active_response_controls:
                        active_response_controls.pop(session_id, None)
                    return
                
                effective_marker = transfer_marker or (agent_response if isinstance(agent_response, str) and agent_response.startswith("TRANSFER_INITIATED:") else None)
                if effective_marker:
                    if transfer_requested:
                        marker_data = effective_marker.replace("TRANSFER_INITIATED:", "")
                        parts = marker_data.split("|")
                        target_extension = parts[0] if len(parts) > 0 else '2001'
                        department = parts[1] if len(parts) > 1 else 'support'
                        reason = parts[2] if len(parts) > 2 else 'User requested transfer'
                        start_transfer_flow(target_extension, department, reason)
                        return
                    else:
                        print("Transfer marker detected but caller did not request a human. Ignoring marker.")
                        agent_response = agent_response if not isinstance(agent_response, str) or not agent_response.startswith("TRANSFER_INITIATED:") else "Let me know how else I can help."
                
                if streaming_tts:
                    streaming_tts.flush_and_close()
                    audio_base64 = ""
                    is_streaming = True
                    sentry_capture_voice_event("tts_generation_completed", session_id, session.get('user_id'), details={"audio_size": 0, "chunks": streaming_tts.chunk_index, "streaming": True})
                else:
                    # Step 3: Convert response to speech using streaming chunks (ElevenLabs with Deepgram fallback)
                    # Note: TTS settings were prepared earlier (before agent processing) for early TTS
                    # Check if early TTS was already started
                    early_tts_started = False
                    with text_accumulator['lock']:
                        early_tts_started = len(text_accumulator['early_audio_chunks']) > 0
                    
                    if not early_tts_started:
                        socketio.emit('status', {'message': 'Generating speech...'}, namespace='/voice', room=session_id)
                    sentry_capture_voice_event("tts_generation_started", session_id, session.get('user_id'))
                    
                    # Detect emotion now that we have full response (for remaining chunks)
                    if emotion_enabled and not emotion:
                        try:
                            emotion_detector = get_emotion_detector()
                            user_input_text = transcribed_text if 'transcribed_text' in locals() else ""
                            emotion = emotion_detector.detect_emotion_from_context(
                                user_input=user_input_text,
                                agent_response=agent_response
                            )
                            print(f"🎭 Using ElevenLabs with emotion: {emotion.value}", flush=True)
                        except Exception as e:
                            print(f"⚠️ Emotion detection failed: {e}", flush=True)
                    
                    # Split text into chunks for streaming
                    text_chunks = chunk_text_by_sentences(agent_response, min_chunk_size=100, max_chunk_size=400)
                    print(f"📝 Split response into {len(text_chunks)} chunks for streaming TTS", flush=True)
                    
                    # Check if first chunk matches early TTS sentence (skip if already generated)
                    skip_first_chunk = False
                    if early_tts_started and text_accumulator['first_sentence']:
                        first_chunk = text_chunks[0] if text_chunks else ""
                        # Check if first chunk starts with the early sentence
                        if first_chunk.startswith(text_accumulator['first_sentence'][:50]):
                            skip_first_chunk = True
                            print(f"⏭️ Skipping first chunk (already generated via early TTS)", flush=True)
                    
                    # Track if we're using streaming mode
                    is_streaming = len(text_chunks) > 1
                    
                    # For very short responses (single chunk), use original non-streaming approach for simplicity
                    # For longer responses, stream chunks
                    if not is_streaming:
                        # Short response - use original approach
                        print(f"🔊 Short response ({len(agent_response)} chars), using non-streaming TTS", flush=True)
                        audio_bytes = None
                        use_livekit_audio = _livekit_active()
                        if response_cancel_event.is_set():
                            print("🛑 Response cancelled before short TTS generation (barge-in)", flush=True)
                            if session_id in active_response_controls:
                                active_response_controls.pop(session_id, None)
                            return
                    
                        if use_livekit_audio:
                            use_elevenlabs = False

                        if use_elevenlabs:
                            try:
                                elevenlabs = get_elevenlabs_service()
                                if emotion_enabled and emotion:
                                    audio_bytes = elevenlabs.synthesize_with_emotion(
                                        text=agent_response,
                                        emotion=emotion,
                                        voice_id=voice_id
                                    )
                                elif language != "en":
                                    audio_bytes = elevenlabs.synthesize_multilingual(
                                        text=agent_response,
                                        language=language,
                                        voice_id=voice_id
                                    )
                                else:
                                    audio_bytes = elevenlabs.synthesize(
                                        text=agent_response,
                                        voice_id=voice_id
                                    )
                            except Exception as e:
                                print(f"⚠️ ElevenLabs TTS failed, falling back to Deepgram: {e}", flush=True)
                                use_elevenlabs = False
                    
                        if not audio_bytes:
                            deepgram_tts = get_deepgram_tts_service()
                            if use_livekit_audio:
                                audio_bytes = deepgram_tts.synthesize_speech(
                                    agent_response,
                                    voice="aura-asteria-en",
                                    encoding="linear16",
                                    sample_rate=24000,
                                    container="none"
                                )
                            else:
                                audio_bytes = deepgram_tts.synthesize_speech(agent_response, voice="aura-asteria-en")
                            tts_provider = "deepgram"
                    
                        if not audio_bytes:
                            raise Exception(f"{tts_provider.capitalize()} TTS failed to generate audio")
                    
                        if use_livekit_audio:
                            _send_livekit_pcm(session_id, audio_bytes, sample_rate=24000, channels=1)
                            audio_base64 = ""
                            print(f"🔊 TTS generated for LiveKit: {len(audio_bytes)} bytes", flush=True)
                            sentry_capture_voice_event("tts_generation_completed", session_id, session.get('user_id'), details={"audio_size": len(audio_bytes), "chunks": 1, "livekit": True})
                        else:
                            audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                            print(f"🔊 TTS generated: {len(audio_bytes)} bytes, base64: {len(audio_base64)} chars", flush=True)
                            sentry_capture_voice_event("tts_generation_completed", session_id, session.get('user_id'), details={"audio_size": len(audio_base64), "chunks": 1})
                    else:
                        # Long response - stream chunks
                        print(f"🔊 Long response ({len(agent_response)} chars, {len(text_chunks)} chunks), using streaming TTS", flush=True)
                    
                        # Send text immediately (for transcript display)
                        # We'll send audio chunks separately
                        audio_base64 = ""  # Will accumulate for fallback/pending storage
                        all_audio_chunks = []
                        chunk_errors = []
                    
                        # Initialize TTS service
                        elevenlabs_service = None
                        deepgram_tts_service = None
                        use_livekit_audio = _livekit_active()
                        if use_livekit_audio:
                            use_elevenlabs = False
                        if use_elevenlabs:
                            try:
                                elevenlabs_service = get_elevenlabs_service()
                                if not elevenlabs_service or not elevenlabs_service.is_available():
                                    use_elevenlabs = False
                                    tts_provider = "deepgram"
                            except:
                                use_elevenlabs = False
                                tts_provider = "deepgram"
                    
                        if not use_elevenlabs:
                            deepgram_tts_service = get_deepgram_tts_service()
                    
                        # Process each chunk (skip first if already generated via early TTS)
                        start_idx = 1 if skip_first_chunk else 0
                        chunk_offset = 1 if skip_first_chunk else 0  # Offset for chunk_index in emit
                    
                        for chunk_idx, text_chunk in enumerate(text_chunks[start_idx:], start=start_idx):
                            if response_cancel_event.is_set():
                                print("🛑 Response cancelled during streaming TTS (barge-in)", flush=True)
                                break
                            try:
                                chunk_audio = None
                    
                                if use_elevenlabs and elevenlabs_service:
                                    try:
                                        if emotion_enabled and emotion:
                                            chunk_audio = elevenlabs_service.synthesize_with_emotion(
                                                text=text_chunk,
                                                emotion=emotion,
                                                voice_id=voice_id
                                            )
                                        elif language != "en":
                                            chunk_audio = elevenlabs_service.synthesize_multilingual(
                                                text=text_chunk,
                                                language=language,
                                                voice_id=voice_id
                                            )
                                        else:
                                            chunk_audio = elevenlabs_service.synthesize(
                                                text=text_chunk,
                                                voice_id=voice_id
                                            )
                                    except Exception as e:
                                        print(f"⚠️ ElevenLabs chunk {chunk_idx+1}/{len(text_chunks)} failed: {e}", flush=True)
                                        chunk_audio = None
                    
                                if not chunk_audio:
                                    if use_livekit_audio:
                                        chunk_audio = _synthesize_deepgram_linear16(text_chunk)
                                    elif deepgram_tts_service:
                                        chunk_audio = deepgram_tts_service.synthesize_speech(text_chunk, voice="aura-asteria-en")
                    
                                if chunk_audio:
                                    if use_livekit_audio:
                                        _send_livekit_pcm(session_id, chunk_audio, sample_rate=24000, channels=1)
                                        chunk_audio = StreamingTTSStream._wrap_linear16_wav(chunk_audio, sample_rate=24000, channels=1, sample_width=2)
                                    chunk_base64 = base64.b64encode(chunk_audio).decode('utf-8')
                                    all_audio_chunks.append(chunk_base64)
                    
                                    # Emit this chunk immediately
                                    if not use_livekit_audio:
                                        emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                                        if emit_socketio:
                                            try:
                                                emit_socketio.emit('audio_chunk', {
                                                    'success': True,
                                                    'chunk_index': chunk_idx - chunk_offset,  # Adjust index if first chunk was skipped
                                                    'total_chunks': len(text_chunks) - chunk_offset,  # Adjust total if first chunk was skipped
                                                    'audio': chunk_base64,
                                                    'is_final': chunk_idx == len(text_chunks) - 1
                                                }, namespace='/voice', room=session_id)
                                                print(f"📤 Emitted audio chunk {chunk_idx+1}/{len(text_chunks)} ({len(chunk_base64)} chars)", flush=True)
                                            except Exception as emit_error:
                                                print(f"⚠️ Error emitting chunk {chunk_idx+1}: {emit_error}", flush=True)
                                else:
                                    chunk_errors.append(chunk_idx)
                                    print(f"⚠️ Failed to generate audio for chunk {chunk_idx+1}", flush=True)
                            except Exception as chunk_error:
                                chunk_errors.append(chunk_idx)
                                print(f"⚠️ Error processing chunk {chunk_idx+1}: {chunk_error}", flush=True)
                                import traceback
                                traceback.print_exc()
                    
                        # Combine all chunks for fallback/pending storage
                        if all_audio_chunks:
                            # Properly combine base64 chunks: decode to binary, concatenate, then re-encode
                            # (Cannot just concatenate base64 strings - that's invalid!)
                            combined_audio_binary = b''
                    
                            # Include early TTS chunks if they exist (first chunk might have been generated via early TTS)
                            with text_accumulator['lock']:
                                early_chunks = text_accumulator.get('early_audio_chunks', [])
                                if early_chunks:
                                    for early_chunk in early_chunks:
                                        try:
                                            combined_audio_binary += base64.b64decode(early_chunk['audio'])
                                        except Exception as e:
                                            print(f"⚠️ Error decoding early TTS chunk: {e}", flush=True)
                    
                            # Add regular streaming chunks
                            for chunk_base64 in all_audio_chunks:
                                try:
                                    combined_audio_binary += base64.b64decode(chunk_base64)
                                except Exception as e:
                                    print(f"⚠️ Error decoding streaming chunk: {e}", flush=True)
                    
                            # Re-encode to base64
                            audio_base64 = base64.b64encode(combined_audio_binary).decode('utf-8')
                            print(f"🔊 Streaming TTS completed: {len(all_audio_chunks)} chunks, {len(audio_base64)} total chars (properly combined)", flush=True)
                            if chunk_errors:
                                print(f"⚠️ {len(chunk_errors)} chunks failed: {chunk_errors}", flush=True)
                    
                            # Emit completion event
                            emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                            if emit_socketio:
                                try:
                                    emit_socketio.emit('audio_stream_complete', {
                                        'success': len(chunk_errors) == 0,
                                        'total_chunks': len(text_chunks),
                                        'successful_chunks': len(all_audio_chunks),
                                        'failed_chunks': len(chunk_errors)
                                    }, namespace='/voice', room=session_id)
                                except:
                                    pass
                    
                            sentry_capture_voice_event("tts_generation_completed", session_id, session.get('user_id'), 
                                                      details={"audio_size": len(audio_base64), "chunks": len(all_audio_chunks), "streaming": True})
                        else:
                            raise Exception(f"{tts_provider.capitalize()} TTS failed to generate audio for all chunks")
                # Send response to client
                print(f"📤 Sending agent_response event to session {session_id}...", flush=True)
                print(f"📤 Response text length: {len(agent_response)}, audio base64 length: {len(audio_base64)}", flush=True)
                
                # Check if session still exists before emitting
                session_still_exists = False
                if redis_manager.is_available():
                    session_data = get_session(session_id)
                    session_still_exists = session_data is not None
                else:
                    session_still_exists = session_id in active_sessions
                
                # Get user_id for pending response storage
                # session variable should be available from earlier in the function (line ~1115)
                user_id = None
                try:
                    # First try from session dict (created from Redis/memory session data)
                    if 'session' in locals() and session:
                        user_id = session.get('user_id')
                        print(f"👤 Got user_id from session dict: {user_id}", flush=True)
                    
                    # If not found, try to get from Redis directly
                    if not user_id:
                        session_data = get_session(session_id)
                        if session_data:
                            user_id = session_data.get('user_id')
                            print(f"👤 Got user_id from Redis session: {user_id}", flush=True)
                except Exception as user_id_error:
                    print(f"⚠️ Error getting user_id for pending response: {user_id_error}", flush=True)
                    import traceback
                    traceback.print_exc()
                
                if user_id:
                    print(f"✅ user_id available for pending response storage: {user_id}", flush=True)
                else:
                    print(f"⚠️ No user_id available for pending response storage (session_id: {session_id})", flush=True)
                    print(f"⚠️ Session dict: {session if 'session' in locals() else 'not in scope'}", flush=True)
                
                if response_cancel_event.is_set():
                    print("🛑 Response cancelled before emit (barge-in)", flush=True)
                    if session_id in active_response_controls:
                        active_response_controls.pop(session_id, None)
                    return
                
                if not session_still_exists:
                    print(f"⚠️ Session {session_id} no longer exists (client may have disconnected)", flush=True)
                    
                    # Store pending response for user_id so it can be sent when client reconnects
                    if user_id:
                        try:
                            pending_response = {
                                'text': agent_response,
                                'audio': audio_base64,
                                'created_at': time.time(),
                                'original_session_id': session_id
                            }
                            if redis_manager.is_available():
                                import json
                                redis_key = f"pending_response:{user_id}"
                                redis_manager.redis_client.setex(redis_key, 300, json.dumps(pending_response))  # 5 min TTL
                                print(f"💾 Stored pending response for user_id {user_id} (will be sent on reconnect)", flush=True)
                                sentry_capture_voice_event("pending_response_stored", session_id, user_id, details={"reason": "session_gone_during_tts"})
                            else:
                                # Fallback to in-memory (not ideal but better than losing response)
                                module_self = sys.modules[__name__]
                                if not hasattr(module_self, 'pending_responses'):
                                    module_self.pending_responses = {}
                                module_self.pending_responses[user_id] = pending_response
                                print(f"💾 Stored pending response in memory for user_id {user_id}", flush=True)
                        except Exception as store_error:
                            print(f"⚠️ Failed to store pending response: {store_error}", flush=True)
                    
                    sentry_capture_voice_event("agent_response_skipped_session_gone", session_id, user_id, details={"reason": "session_no_longer_exists", "pending_stored": user_id is not None})
                else:
                    print(f"✅ Session {session_id} still exists, proceeding with emit", flush=True)
                    try:
                        # Use the global socketio instance
                        emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                        if not emit_socketio:
                            raise Exception("socketio instance not available")
                        
                        # Check if client is actually connected to Socket.IO room (more reliable than callback)
                        # Socket.IO automatically puts each client in a room with their session_id
                        # A "room" in Socket.IO is a channel/group - when you emit to a room, all clients in that room receive the message
                        # Each client is automatically in a room named after their session_id
                        client_actually_connected = False
                        try:
                            # Check if there are any clients in the room for this session
                            # get_participants() returns a generator, so we need to convert to list
                            room_clients = list(emit_socketio.server.manager.get_participants('/voice', session_id))
                            client_actually_connected = len(room_clients) > 0
                            print(f"🔍 Socket.IO room check: {len(room_clients)} client(s) in room {session_id}, connected={client_actually_connected}", flush=True)
                        except Exception as room_check_error:
                            print(f"⚠️ Error checking Socket.IO room: {room_check_error}", flush=True)
                            import traceback
                            traceback.print_exc()
                            # If we can't check, assume connected (fallback to old behavior)
                            client_actually_connected = True
                        
                        if not client_actually_connected:
                            print(f"⚠️ Client not in Socket.IO room (disconnected), storing as pending", flush=True)
                            # Client is not actually connected, store as pending immediately
                            if user_id:
                                try:
                                    import json
                                    pending_response = {
                                        'text': agent_response,
                                        'audio': audio_base64,
                                        'is_streaming': is_streaming if 'is_streaming' in locals() else False,
                                        'created_at': time.time(),
                                        'original_session_id': session_id
                                    }
                                    redis_key = f"pending_response:{user_id}"
                                    if redis_manager.is_available():
                                        redis_manager.redis_client.setex(redis_key, 300, json.dumps(pending_response))
                                        print(f"💾 Stored pending response for user_id {user_id} (client not in room)", flush=True)
                                        sentry_capture_voice_event("pending_response_stored_no_room", session_id, user_id, details={"reason": "client_not_in_room"})
                                    else:
                                        module_self = sys.modules[__name__]
                                        if not hasattr(module_self, 'pending_responses'):
                                            module_self.pending_responses = {}
                                        module_self.pending_responses[user_id] = pending_response
                                        print(f"💾 Stored pending response in memory for user_id {user_id}", flush=True)
                                except Exception as store_error:
                                    print(f"⚠️ Error storing pending response: {store_error}", flush=True)
                        else:
                            # Client is connected, emit the response
                            print(f"📤 Emitting agent_response to session {session_id} in /voice namespace...", flush=True)
                            
                            # CRITICAL: Store as pending BEFORE emitting as a safety net
                            # Even if client is in room, they might disconnect before receiving the message
                            # We'll clear it if callback confirms delivery
                            if user_id:
                                try:
                                    import json
                                    pending_response = {
                                        'text': agent_response,
                                        'audio': audio_base64,
                                        'is_streaming': is_streaming if 'is_streaming' in locals() else False,
                                        'created_at': time.time(),
                                        'original_session_id': session_id
                                    }
                                    redis_key = f"pending_response:{user_id}"
                                    if redis_manager.is_available():
                                        redis_manager.redis_client.setex(redis_key, 300, json.dumps(pending_response))
                                        print(f"💾 Stored pending response as backup for user_id {user_id} (will clear if delivery confirmed)", flush=True)
                                except Exception as store_error:
                                    print(f"⚠️ Error storing pending response backup: {store_error}", flush=True)
                            
                            # Simplified callback - clear pending if delivery confirmed
                            def emit_callback(success):
                                if success:
                                    print(f"✅ agent_response callback: delivered to session {session_id}", flush=True)
                                    # Clear pending response if emit succeeded
                                    if user_id:
                                        try:
                                            import json
                                            redis_key = f"pending_response:{user_id}"
                                            if redis_manager.is_available():
                                                redis_manager.redis_client.delete(redis_key)
                                                print(f"🧹 Cleared pending response for user_id {user_id} (response delivered)", flush=True)
                                        except Exception as clear_error:
                                            print(f"⚠️ Error clearing pending response: {clear_error}", flush=True)
                                else:
                                    print(f"⚠️ agent_response callback: delivery failed to session {session_id}", flush=True)
                                    # Pending response already stored before emit, so we're covered
                            
                            # Check audio size - if too large, use HTTP instead of Socket.IO to avoid WebSocket corruption
                            AUDIO_SIZE_THRESHOLD = 500000  # 500KB base64 (~375KB binary)
                            audio_size = len(audio_base64) if audio_base64 else 0
                            
                            if audio_size > AUDIO_SIZE_THRESHOLD:
                                print(f"⚠️ Audio payload too large ({audio_size} chars), using HTTP delivery instead of Socket.IO", flush=True)
                                # Store in Redis and send notification
                                if user_id:
                                    try:
                                        import json
                                        pending_response = {
                                            'text': agent_response,
                                            'audio': audio_base64,
                                            'created_at': time.time(),
                                            'original_session_id': session_id
                                        }
                                        redis_key = f"pending_response:{user_id}"
                                        if redis_manager.is_available():
                                            redis_manager.redis_client.setex(redis_key, 300, json.dumps(pending_response))
                                            print(f"💾 Stored large response in Redis for HTTP delivery", flush=True)
                                            
                                            # Send small notification via Socket.IO
                                            emit_socketio.emit('pending_response_available', {
                                                'success': True,
                                                'message': 'Response ready - fetching via HTTP...',
                                                'user_id': user_id
                                            }, namespace='/voice', room=session_id)
                                            print(f"✅ Large response notification sent - client will fetch via HTTP", flush=True)
                                    except Exception as store_error:
                                        print(f"⚠️ Error storing large response: {store_error}", flush=True)
                                        # Fallback: try sending anyway (might work, might fail)
                                        emit_socketio.emit('agent_response', {
                                            'success': True,
                                            'text': agent_response,
                                            'audio': audio_base64,
                                            'is_streaming': is_streaming if 'is_streaming' in locals() else False
                                        }, namespace='/voice', room=session_id, callback=emit_callback)
                                else:
                                    # No user_id, try sending anyway
                                    emit_socketio.emit('agent_response', {
                                        'success': True,
                                        'text': agent_response,
                                        'audio': audio_base64,
                                        'is_streaming': is_streaming if 'is_streaming' in locals() else False
                                    }, namespace='/voice', room=session_id, callback=emit_callback)
                            else:
                                # Small enough to send via Socket.IO
                                emit_socketio.emit('agent_response', {
                                    'success': True,
                                    'text': agent_response,
                                    'audio': audio_base64,
                                    'is_streaming': is_streaming if 'is_streaming' in locals() else False
                                }, namespace='/voice', room=session_id, callback=emit_callback)
                                print(f"✅ agent_response event emitted to session {session_id} (Socket.IO will handle delivery)", flush=True)
                    except Exception as emit_error:
                        print(f"❌ Error during emit: {emit_error}", flush=True)
                        import traceback
                        traceback.print_exc()
                        # Try to send error notification
                        try:
                            emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                            if emit_socketio:
                                emit_socketio.emit('error', {
                                    'message': f"Error sending response: {str(emit_error)}"
                                }, namespace='/voice', room=session_id)
                        except:
                            pass
                        import traceback
                        traceback.print_exc()
                        # Try sending error to client
                        try:
                            emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                            if emit_socketio:
                                emit_socketio.emit('error', {
                                    'message': f"Error sending response: {str(emit_error)}"
                                }, namespace='/voice', room=session_id)
                        except:
                            pass
                
                sentry_capture_voice_event("audio_processing_completed", session_id, session.get('user_id'), details={"success": True})
                if session_id in active_response_controls:
                    active_response_controls.pop(session_id, None)
            
            except Exception as e:
                print(f"❌ Error processing audio: {e}")
                import traceback
                traceback.print_exc()
                
                sentry_capture_voice_event("audio_processing_error", session_id, session.get('user_id') if 'session' in locals() else None, details={"error": str(e)})
                if SENTRY_AVAILABLE:
                    sentry_sdk.capture_exception(e)
                if session_id in active_response_controls:
                    active_response_controls.pop(session_id, None)
                
                socketio.emit('error', {
                    'message': f"Error processing audio: {str(e)}"
                }, namespace='/voice', room=session_id)
            finally:
                # Clear guard
                processing_guards.pop(session_id, None)
                print(f"🧹 processing_guard CLEARED for session: {session_id}", flush=True)
                sys.stdout.flush()


async def process_with_agent(
    text: str, 
    user_id: str, 
    user_name: str,
    socketio=None,
    session_id: str | None = None,
    text_chunk_callback: Optional[callable] = None,  # Optional callback for text chunks (for early TTS)
    tool_call_callback: Optional[callable] = None,   # Optional callback when tool calls are detected
) -> str:
    """Process user input with the agent"""
    try:
        # Capture agent processing start in Sentry
        if SENTRY_AVAILABLE:
            with sentry_sdk.configure_scope() as scope:
                scope.set_tag("component", "webrtc_voice_server")
                scope.set_tag("operation", "agent_processing")
                scope.set_context("agent_processing", {
                    "user_id": user_id,
                    "user_name": user_name,
                    "text_length": len(text),
                    "text_preview": text[:100] + "..." if len(text) > 100 else text
                })
                sentry_sdk.add_breadcrumb(
                    message="Agent processing started",
                    category="agent",
                    level="info"
                )
        
        # Use the same agent processing as Twilio for consistency
        from convonet.routes import _run_agent_async
        
        # LATENCY OPTIMIZATION: Use Claude Haiku (faster model) for voice responses
        # Claude Haiku is ~2-3x faster than Sonnet 4, reducing agent processing time from ~5s to ~2-3s
        voice_model = _select_voice_model(user_id)  # Faster model for voice responses
        
        # Use the same agent processing function as Twilio, but with faster model for voice
        result = await _run_agent_async(
            prompt=text,
            user_id=user_id,
            user_name=user_name,
            reset_thread=False,
            include_metadata=True,
            socketio=socketio,
            session_id=session_id,
            model=voice_model,  # Use faster model for voice responses
            text_chunk_callback=text_chunk_callback,  # Pass callback for early TTS
            tool_call_callback=tool_call_callback,    # Pass callback for filler on tool calls
        )
        
        if isinstance(result, dict):
            return result.get("response", ""), result.get("transfer_marker")
        return result, None
    
    except asyncio.TimeoutError:
        # Capture timeout in Sentry
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_message("Agent processing timeout", level="warning")
        return "I'm sorry, I'm taking too long to process that request. Please try again.", None
    except Exception as e:
        print(f"❌ Agent error: {e}")
        # Capture agent error in Sentry
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_exception(e)
        return "I'm sorry, I encountered an error. Please try again.", None
