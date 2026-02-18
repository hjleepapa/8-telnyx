"""
ElevenLabs Realtime Speech-to-Text WebSocket Streaming
Handles real-time audio transcription with VAD (Voice Activity Detection)
"""

import os
import json
import logging
import asyncio
import base64
from typing import Optional, Callable, Dict, Any
from enum import Enum
import websockets
from collections import deque

logger = logging.getLogger(__name__)

# Global sessions manager
_streaming_stt_sessions: Dict[str, 'ElevenLabsStreamingSTT'] = {}


class CommitStrategy(str, Enum):
    """Commit strategy for transcription"""
    MANUAL = "manual"          # Manually commit transcriptions
    VAD = "vad"                # Use Voice Activity Detection


class ElevenLabsStreamingSTT:
    """
    ElevenLabs Realtime STT WebSocket client for streaming audio transcription
    
    Features:
    - Real-time streaming audio input
    - Partial and committed transcripts
    - Voice Activity Detection (VAD) for auto-commit
    - Word-level timestamps
    - Language detection
    - ~100-500ms latency
    
    Reference: https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        session_id: str = "default",
        on_partial: Optional[Callable[[str], None]] = None,
        on_commit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        language_code: str = "en",
        model_id: str = "scribev1",
        commit_strategy: CommitStrategy = CommitStrategy.VAD,
        vad_threshold: float = 0.4,
        vad_silence_threshold_secs: float = 1.5,
        min_speech_duration_ms: int = 100,
        min_silence_duration_ms: int = 100,
        include_timestamps: bool = True,
        include_language_detection: bool = True,
    ):
        """
        Initialize ElevenLabs Streaming STT session
        
        Args:
            api_key: ElevenLabs API key (defaults to env var)
            session_id: Unique session identifier
            on_partial: Callback for partial transcripts
            on_commit: Callback for committed transcripts with word/timestamp data
            on_error: Callback for error messages
            language_code: Language code (e.g., 'en', 'es', 'fr')
            model_id: Model to use (default: scribev1 - latest Scribe model)
            commit_strategy: Manual or VAD-based commit
            vad_threshold: VAD confidence threshold (0.0-1.0)
            vad_silence_threshold_secs: Seconds of silence to trigger commit (VAD mode)
            min_speech_duration_ms: Minimum speech duration to consider as input
            min_silence_duration_ms: Minimum silence duration between speech segments
            include_timestamps: Include word-level timestamps in transcripts
            include_language_detection: Auto-detect language
        """
        self.api_key = api_key or os.getenv('ELEVENLABS_API_KEY')
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY not set")
        
        self.session_id = session_id
        self.on_partial = on_partial
        self.on_commit = on_commit
        self.on_error = on_error
        
        # Configuration
        self.language_code = language_code
        self.model_id = model_id
        self.commit_strategy = commit_strategy
        self.vad_threshold = vad_threshold
        self.vad_silence_threshold_secs = vad_silence_threshold_secs
        self.min_speech_duration_ms = min_speech_duration_ms
        self.min_silence_duration_ms = min_silence_duration_ms
        self.include_timestamps = include_timestamps
        self.include_language_detection = include_language_detection
        
        # Connection state
        self.websocket = None
        self.is_connected = False
        self.is_running = False
        self.audio_queue = deque(maxlen=1000)  # Audio chunk buffer
        
        # Session state
        self.session_started = False
        self.partial_transcript = ""
        self.committed_transcript = ""
        self.word_timestamps = []
        
        # Audio format: PCM 16-bit, 16kHz (ElevenLabs standard for STT)
        self.sample_rate = 16000
        self.audio_format = "pcm_16000"
    
    async def connect(self):
        """Establish WebSocket connection to ElevenLabs Realtime STT"""
        try:
            # Build WebSocket URL with query parameters
            url = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
            
            params = {
                "model_id": self.model_id,
                "language_code": self.language_code,
                "commit_strategy": self.commit_strategy.value,
                "vad_threshold": str(self.vad_threshold),
                "vad_silence_threshold_secs": str(self.vad_silence_threshold_secs),
                "min_speech_duration_ms": str(self.min_speech_duration_ms),
                "min_silence_duration_ms": str(self.min_silence_duration_ms),
                "include_timestamps": "true" if self.include_timestamps else "false",
                "include_language_detection": "true" if self.include_language_detection else "false",
                "audio_format": self.audio_format,
                "enable_logging": "true",
            }
            
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
            
            logger.info(f"🔌 Connecting to ElevenLabs Realtime STT: {self.session_id}")
            
            # Connect with authentication header
            async with websockets.connect(
                url,
                additional_headers={"xi-api-key": self.api_key}
            ) as websocket:
                self.websocket = websocket
                self.is_connected = True
                self.is_running = True
                logger.info(f"✅ Connected to ElevenLabs STT: {self.session_id}")
                
                # Start listening for messages
                await self._listen_loop()
        
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            self.is_connected = False
            if self.on_error:
                self.on_error(f"Connection error: {str(e)}")
            raise
    
    async def _listen_loop(self):
        """Listen for incoming messages from WebSocket"""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    logger.error(f"Invalid JSON: {message}")
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
                    if self.on_error:
                        self.on_error(f"Message handling error: {str(e)}")
        
        except asyncio.CancelledError:
            logger.info(f"STT session cancelled: {self.session_id}")
        except Exception as e:
            logger.error(f"Listen loop error: {e}")
            if self.on_error:
                self.on_error(f"Listen loop error: {str(e)}")
        finally:
            self.is_running = False
            self.is_connected = False
            logger.info(f"STT session closed: {self.session_id}")
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming WebSocket message"""
        message_type = data.get("message_type")
        
        if message_type == "session_started":
            logger.info(f"📡 STT Session started: {data.get('session_id')}")
            self.session_started = True
            config = data.get("config", {})
            logger.debug(f"Session config: {config}")
        
        elif message_type == "partial_transcript":
            # Partial transcripts are received as user speaks
            self.partial_transcript = data.get("text", "")
            logger.debug(f"📝 Partial: {self.partial_transcript}")
            if self.on_partial:
                self.on_partial(self.partial_transcript)
        
        elif message_type == "committed_transcript":
            # Committed transcripts are final for a segment
            text = data.get("text", "")
            self.committed_transcript = text
            logger.info(f"✅ Committed: {text}")
            if self.on_commit:
                self.on_commit(text, {"text": text})
        
        elif message_type == "committed_transcript_with_timestamps":
            # Full transcript with word-level timing information
            text = data.get("text", "")
            language = data.get("language_code", self.language_code)
            words = data.get("words", [])
            
            self.committed_transcript = text
            self.word_timestamps = words
            
            logger.info(f"✅ Committed (timestamps): {text}")
            logger.debug(f"   Words: {words}")
            
            if self.on_commit:
                self.on_commit(text, {
                    "text": text,
                    "language_code": language,
                    "words": words
                })
        
        elif message_type in ["error", "auth_error", "quota_exceeded", 
                              "commit_throttled", "rate_limited"]:
            error_msg = data.get("error", "Unknown error")
            logger.error(f"❌ {message_type}: {error_msg}")
            if self.on_error:
                self.on_error(f"{message_type}: {error_msg}")
        
        else:
            logger.warning(f"Unknown message type: {message_type}")
    
    async def send_audio(self, audio_bytes: bytes, is_final_chunk: bool = False, commit: bool = False):
        """
        Send audio chunk to WebSocket for transcription
        
        Args:
            audio_bytes: Raw PCM audio bytes (16-bit, 16kHz)
            is_final_chunk: Whether this is the last chunk
            commit: Force commit after this chunk (manual mode only)
        """
        if not self.is_connected:
            logger.warning("⚠️ Not connected, queueing audio")
            self.audio_queue.append((audio_bytes, commit))
            return
        
        try:
            # Encode audio as base64
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
            
            message = {
                "message_type": "input_audio_chunk",
                "audio_base_64": audio_b64,
                "commit": commit or is_final_chunk,
                "sample_rate": self.sample_rate,
            }
            
            await self.websocket.send(json.dumps(message))
            logger.debug(f"📤 Sent {len(audio_bytes)} bytes ({len(audio_b64)} b64)")
        
        except Exception as e:
            logger.error(f"❌ Error sending audio: {e}")
            if self.on_error:
                self.on_error(f"Send error: {str(e)}")
    
    def send_audio_sync(self, audio_bytes: bytes, commit: bool = False):
        """
        Synchronous wrapper for sending audio (for non-async contexts)
        
        Args:
            audio_bytes: Raw PCM audio bytes
            commit: Force commit after this chunk
        """
        if not self.is_running:
            logger.warning("Session not running")
            return
        
        # Queue for async sending
        try:
            asyncio.create_task(self.send_audio(audio_bytes, commit=commit))
        except RuntimeError:
            self.audio_queue.append((audio_bytes, commit))
    
    async def commit_transcript(self):
        """Manually commit current transcript (for manual commit strategy)"""
        if self.commit_strategy == CommitStrategy.MANUAL:
            await self.send_audio(b'', commit=True)
            logger.info("📤 Commit triggered manually")
    
    async def close(self):
        """Close WebSocket connection"""
        try:
            if self.websocket:
                await self.websocket.close()
            self.is_connected = False
            self.is_running = False
            logger.info(f"✅ STT session closed: {self.session_id}")
        except Exception as e:
            logger.error(f"Error closing connection: {e}")
    
    def get_transcript(self) -> str:
        """Get current committed transcript"""
        return self.committed_transcript
    
    def reset_transcript(self):
        """Reset transcript state"""
        self.partial_transcript = ""
        self.committed_transcript = ""
        self.word_timestamps = []


async def create_streaming_stt_session(
    session_id: str = "default",
    on_partial: Optional[Callable[[str], None]] = None,
    on_commit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    **kwargs
) -> ElevenLabsStreamingSTT:
    """
    Create and connect a new Realtime STT streaming session
    
    Args:
        session_id: Unique session identifier
        on_partial: Callback for partial transcripts
        on_commit: Callback for committed transcripts
        on_error: Callback for error messages
        **kwargs: Additional configuration options
    
    Returns:
        Connected ElevenLabsStreamingSTT instance
    """
    session = ElevenLabsStreamingSTT(
        session_id=session_id,
        on_partial=on_partial,
        on_commit=on_commit,
        on_error=on_error,
        **kwargs
    )
    
    # Store in global sessions
    _streaming_stt_sessions[session_id] = session
    
    # Connect asynchronously
    try:
        asyncio.create_task(session.connect())
        logger.info(f"✅ STT session created: {session_id}")
    except Exception as e:
        logger.error(f"Failed to create STT session: {e}")
        raise
    
    return session


def get_streaming_stt_session(session_id: str) -> Optional[ElevenLabsStreamingSTT]:
    """Get existing streaming STT session"""
    return _streaming_stt_sessions.get(session_id)


async def remove_streaming_stt_session(session_id: str):
    """Close and remove streaming STT session"""
    session = _streaming_stt_sessions.pop(session_id, None)
    if session:
        await session.close()
        logger.info(f"✅ STT session removed: {session_id}")


def get_all_streaming_stt_sessions() -> Dict[str, ElevenLabsStreamingSTT]:
    """Get all active streaming STT sessions"""
    return _streaming_stt_sessions.copy()


def create_streaming_stt_session_sync(
    session_id: str = "default",
    on_partial: Optional[Callable[[str], None]] = None,
    on_commit: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    on_error: Optional[Callable[[str], None]] = None,
    **kwargs
) -> ElevenLabsStreamingSTT:
    """
    Create streaming STT session synchronously (connection deferred to async context)
    
    This creates the session object without connecting. 
    Call session.connect() asynchronously when ready.
    
    Args:
        session_id: Unique session identifier
        on_partial: Callback for partial transcripts
        on_commit: Callback for committed transcripts
        on_error: Callback for error messages
        **kwargs: Additional configuration options
    
    Returns:
        ElevenLabsStreamingSTT instance (not yet connected)
    """
    session = ElevenLabsStreamingSTT(
        session_id=session_id,
        on_partial=on_partial,
        on_commit=on_commit,
        on_error=on_error,
        **kwargs
    )
    
    # Store in global sessions
    _streaming_stt_sessions[session_id] = session
    logger.info(f"✅ STT session created (not connected): {session_id}")
    
    return session
