"""
ElevenLabs Multi-Context Text-to-Speech WebSocket Streaming
Handles real-time speech synthesis with multiple concurrent contexts
"""

import os
import json
import logging
import asyncio
import base64
from typing import Optional, Callable, Dict, Any, List
from enum import Enum
from dataclasses import dataclass, field
import websockets

logger = logging.getLogger(__name__)

# Global sessions manager
_streaming_tts_sessions: Dict[str, 'ElevenLabsStreamingTTS'] = {}


class TextToSpeechOutputFormat(str, Enum):
    """Output format for TTS audio"""
    PCM_8000 = "pcm_8000"
    PCM_16000 = "pcm_16000"
    PCM_22050 = "pcm_22050"
    PCM_24000 = "pcm_24000"
    PCM_44100 = "pcm_44100"
    PCM_48000 = "pcm_48000"  # WebRTC native
    OPUS_48000 = "opus_48000_128"  # Compressed, WebRTC compatible
    MP3_44100 = "mp3_44100_128"
    ULAW_8000 = "ulaw_8000"


class TextNormalization(str, Enum):
    """Text normalization options"""
    AUTO = "auto"
    ON = "on"
    OFF = "off"


@dataclass
class VoiceSettings:
    """Voice settings for TTS"""
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True
    speed: float = 1.0


@dataclass
class GenerationConfig:
    """Generation configuration for TTS"""
    chunk_length_schedule: List[int] = field(default_factory=lambda: [120, 160, 250, 290])
    # Default: First chunk at 120 chars, then 160, 250, 290+ chars per chunk


@dataclass
class AudioContext:
    """Manages a single audio generation context"""
    context_id: str
    voice_id: str
    voice_settings: VoiceSettings
    on_audio_chunk: Optional[Callable[[bytes], None]] = None
    on_final: Optional[Callable[[], None]] = None
    on_error: Optional[Callable[[str], None]] = None
    buffer: List[bytes] = field(default_factory=list)
    is_active: bool = True
    is_flushing: bool = False


class ElevenLabsStreamingTTS:
    """
    ElevenLabs Multi-Context TTS WebSocket client for streaming speech synthesis
    
    Features:
    - Real-time streaming text-to-speech
    - Multiple concurrent audio contexts
    - Character-level alignment/timing info
    - Smart buffering with chunk_length_schedule
    - Output formats: PCM, Opus, MP3, µ-law, A-law
    - ~400-600ms latency for first chunk
    
    Reference: https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-multi-stream-input
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        session_id: str = "default",
        default_voice_id: str = "21m00Tcm4TlvDq8ikWAM",  # Rachel
        model_id: str = "eleven_multilingual_v2",
        output_format: TextToSpeechOutputFormat = TextToSpeechOutputFormat.PCM_48000,
        language_code: str = "en",
        enable_logging: bool = True,
        enable_ssml_parsing: bool = False,
        apply_text_normalization: TextNormalization = TextNormalization.AUTO,
        inactivity_timeout: int = 20,
        sync_alignment: bool = False,
        auto_mode: bool = False,
    ):
        """
        Initialize ElevenLabs Multi-Context TTS session
        
        Args:
            api_key: ElevenLabs API key
            session_id: Unique session identifier
            default_voice_id: Default voice for new contexts
            model_id: Model to use
            output_format: Audio output format
            language_code: Language code
            enable_logging: Enable server-side logging
            enable_ssml_parsing: Enable SSML parsing in text
            apply_text_normalization: Text normalization strategy
            inactivity_timeout: Timeout for inactive contexts (seconds)
            sync_alignment: Sync alignment info with audio
            auto_mode: Auto mode for chunk scheduling
        """
        self.api_key = api_key or os.getenv('ELEVENLABS_API_KEY')
        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY not set")
        
        self.session_id = session_id
        self.default_voice_id = default_voice_id
        self.model_id = model_id
        self.output_format = output_format
        self.language_code = language_code
        self.enable_logging = enable_logging
        self.enable_ssml_parsing = enable_ssml_parsing
        self.apply_text_normalization = apply_text_normalization
        self.inactivity_timeout = inactivity_timeout
        self.sync_alignment = sync_alignment
        self.auto_mode = auto_mode
        
        # Connection state
        self.websocket = None
        self.is_connected = False
        self.is_running = False
        
        # Context management - each context is an independent audio stream
        self.contexts: Dict[str, AudioContext] = {}
        self.default_context_id = "default"
        self.lock = asyncio.Lock()
    
    async def connect(self):
        """Establish WebSocket connection to ElevenLabs Multi-Context TTS"""
        try:
            # Use default voice for the connection
            url = f"wss://api.elevenlabs.io/v1/text-to-speech/{self.default_voice_id}/multi-stream-input"
            
            params = {
                "model_id": self.model_id,
                "language_code": self.language_code,
                "output_format": self.output_format.value,
                "enable_logging": "true" if self.enable_logging else "false",
                "enable_ssml_parsing": "true" if self.enable_ssml_parsing else "false",
                "apply_text_normalization": self.apply_text_normalization.value,
                "inactivity_timeout": str(self.inactivity_timeout),
                "sync_alignment": "true" if self.sync_alignment else "false",
                "auto_mode": "true" if self.auto_mode else "false",
            }
            
            query_string = "&".join([f"{k}={v}" for k, v in params.items()])
            url = f"{url}?{query_string}"
            
            logger.info(f"🔌 Connecting to ElevenLabs Multi-Context TTS: {self.session_id}")
            
            # Connect with authentication header
            async with websockets.connect(
                url,
                additional_headers={"xi-api-key": self.api_key}
            ) as websocket:
                self.websocket = websocket
                self.is_connected = True
                self.is_running = True
                logger.info(f"✅ Connected to ElevenLabs TTS: {self.session_id}")
                
                # Start listening for messages
                await self._listen_loop()
        
        except Exception as e:
            logger.error(f"❌ Connection failed: {e}")
            self.is_connected = False
            raise
    
    async def _listen_loop(self):
        """Listen for incoming audio from WebSocket"""
        try:
            async for message in self.websocket:
                try:
                    data = json.loads(message)
                    await self._handle_message(data)
                except json.JSONDecodeError:
                    # Might be binary audio data
                    await self._handle_binary_message(message)
                except Exception as e:
                    logger.error(f"Error handling message: {e}")
        
        except asyncio.CancelledError:
            logger.info(f"TTS session cancelled: {self.session_id}")
        except Exception as e:
            logger.error(f"Listen loop error: {e}")
        finally:
            self.is_running = False
            self.is_connected = False
            logger.info(f"TTS session closed: {self.session_id}")
    
    async def _handle_message(self, data: Dict[str, Any]):
        """Handle incoming JSON message (metadata)"""
        message_type = data.get("message_type", "")
        
        if "audio" in data:
            # Audio chunk message
            context_id = data.get("contextId", self.default_context_id)
            audio_b64 = data.get("audio", "")
            
            if context_id in self.contexts:
                try:
                    audio_bytes = base64.b64decode(audio_b64)
                    context = self.contexts[context_id]
                    
                    if context.on_audio_chunk:
                        context.on_audio_chunk(audio_bytes)
                    
                    context.buffer.append(audio_bytes)
                    logger.debug(f"🔊 Received {len(audio_bytes)} bytes for context: {context_id}")
                
                except Exception as e:
                    logger.error(f"Error decoding audio: {e}")
        
        elif data.get("isFinal"):
            # Final message for a context
            context_id = data.get("contextId", self.default_context_id)
            logger.info(f"✅ Final audio for context: {context_id}")
            
            if context_id in self.contexts:
                context = self.contexts[context_id]
                if context.on_final:
                    context.on_final()
        
        else:
            logger.debug(f"Message: {data}")
    
    async def _handle_binary_message(self, message: bytes):
        """Handle binary audio messages"""
        # Try to parse as audio for default context
        if self.default_context_id in self.contexts:
            context = self.contexts[self.default_context_id]
            if context.on_audio_chunk:
                context.on_audio_chunk(message)
            context.buffer.append(message)
    
    async def initialize_context(
        self,
        text: str = " ",
        context_id: Optional[str] = None,
        voice_id: Optional[str] = None,
        voice_settings: Optional[VoiceSettings] = None,
        generation_config: Optional[GenerationConfig] = None,
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
        on_final: Optional[Callable[[], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
    ) -> str:
        """
        Create and initialize a new audio context
        
        Args:
            text: Initial text (usually just a space)
            context_id: Unique context ID (auto-generated if not provided)
            voice_id: Voice ID for this context (uses default if not provided)
            voice_settings: Voice settings object
            generation_config: Generation configuration
            on_audio_chunk: Callback for audio chunks
            on_final: Callback when generation finishes
            on_error: Callback for errors
        
        Returns:
            The context_id for this context
        """
        if not self.is_connected:
            raise RuntimeError("Not connected to TTS service")
        
        context_id = context_id or f"ctx_{len(self.contexts)}_{asyncio.get_event_loop().time()}"
        voice_id = voice_id or self.default_voice_id
        voice_settings = voice_settings or VoiceSettings()
        generation_config = generation_config or GenerationConfig()
        
        # Create context object
        async with self.lock:
            if context_id in self.contexts:
                logger.warning(f"Context {context_id} already exists, reusing")
                return context_id
            
            context = AudioContext(
                context_id=context_id,
                voice_id=voice_id,
                voice_settings=voice_settings,
                on_audio_chunk=on_audio_chunk,
                on_final=on_final,
                on_error=on_error,
            )
            self.contexts[context_id] = context
        
        # Send initialization message
        try:
            message = {
                "text": text,
                "context_id": context_id,
                "voice_settings": {
                    "stability": voice_settings.stability,
                    "similarity_boost": voice_settings.similarity_boost,
                    "style": voice_settings.style,
                    "use_speaker_boost": voice_settings.use_speaker_boost,
                    "speed": voice_settings.speed,
                },
                "generation_config": {
                    "chunk_length_schedule": generation_config.chunk_length_schedule,
                },
            }
            
            await self.websocket.send(json.dumps(message))
            logger.info(f"✅ Context initialized: {context_id}")
            return context_id
        
        except Exception as e:
            logger.error(f"Failed to initialize context: {e}")
            async with self.lock:
                del self.contexts[context_id]
            raise
    
    async def send_text(
        self,
        text: str,
        context_id: Optional[str] = None,
        try_trigger_generation: bool = False,
    ):
        """
        Send text to a context for audio generation
        
        Args:
            text: Text to synthesize (should end with space)
            context_id: Target context (uses default if not provided)
            try_trigger_generation: Force generation attempt (advanced)
        """
        context_id = context_id or self.default_context_id
        
        if not self.is_connected:
            raise RuntimeError("Not connected to TTS service")
        
        if context_id not in self.contexts:
            raise ValueError(f"Context {context_id} does not exist")
        
        try:
            # Ensure text ends with space for streaming
            if not text.endswith(" "):
                text += " "
            
            message = {
                "text": text,
                "context_id": context_id,
                "try_trigger_generation": try_trigger_generation,
            }
            
            await self.websocket.send(json.dumps(message))
            logger.debug(f"📝 Sent text to {context_id}: '{text[:50]}...'")
        
        except Exception as e:
            logger.error(f"Error sending text: {e}")
            raise
    
    async def flush_context(
        self,
        context_id: Optional[str] = None,
        text: str = " ",
    ):
        """
        Flush a context to force audio generation
        
        Args:
            context_id: Context to flush (uses default if not provided)
            text: Optional text to send before flushing
        """
        context_id = context_id or self.default_context_id
        
        if not self.is_connected:
            raise RuntimeError("Not connected to TTS service")
        
        if context_id not in self.contexts:
            raise ValueError(f"Context {context_id} does not exist")
        
        try:
            self.contexts[context_id].is_flushing = True
            
            message = {
                "text": text if text else " ",
                "context_id": context_id,
                "flush": True,
            }
            
            await self.websocket.send(json.dumps(message))
            logger.info(f"🔄 Flushed context: {context_id}")
        
        except Exception as e:
            logger.error(f"Error flushing context: {e}")
            raise
    
    async def close_context(self, context_id: Optional[str] = None):
        """
        Close an audio context
        
        Args:
            context_id: Context to close (uses default if not provided)
        """
        context_id = context_id or self.default_context_id
        
        if not self.is_connected:
            raise RuntimeError("Not connected to TTS service")
        
        if context_id not in self.contexts:
            logger.warning(f"Context {context_id} does not exist")
            return
        
        try:
            message = {
                "context_id": context_id,
                "close_context": True,
            }
            
            await self.websocket.send(json.dumps(message))
            logger.info(f"✅ Closed context: {context_id}")
            
            async with self.lock:
                del self.contexts[context_id]
        
        except Exception as e:
            logger.error(f"Error closing context: {e}")
            raise
    
    async def close(self):
        """Close entire WebSocket connection and all contexts"""
        try:
            if self.is_connected and self.websocket:
                message = {"close_socket": True}
                await self.websocket.send(json.dumps(message))
            
            self.is_connected = False
            self.is_running = False
            
            async with self.lock:
                self.contexts.clear()
            
            logger.info(f"✅ TTS session closed: {self.session_id}")
        
        except Exception as e:
            logger.error(f"Error closing connection: {e}")
    
    def get_context_audio_buffer(self, context_id: Optional[str] = None) -> bytes:
        """Get buffered audio for a context"""
        context_id = context_id or self.default_context_id
        
        if context_id not in self.contexts:
            return b''
        
        context = self.contexts[context_id]
        audio_data = b''.join(context.buffer)
        context.buffer.clear()  # Clear after retrieval
        
        return audio_data
    
    def get_active_contexts(self) -> List[str]:
        """Get list of active context IDs"""
        return [cid for cid, ctx in self.contexts.items() if ctx.is_active]


async def create_streaming_tts_session(
    session_id: str = "default",
    default_voice_id: str = "21m00Tcm4TlvDq8ikWAM",
    **kwargs
) -> ElevenLabsStreamingTTS:
    """
    Create and connect a new Multi-Context TTS streaming session
    
    Args:
        session_id: Unique session identifier
        default_voice_id: Default voice ID to use
        **kwargs: Additional configuration options
    
    Returns:
        Connected ElevenLabsStreamingTTS instance
    """
    session = ElevenLabsStreamingTTS(
        session_id=session_id,
        default_voice_id=default_voice_id,
        **kwargs
    )
    
    # Store in global sessions
    _streaming_tts_sessions[session_id] = session
    
    # Connect asynchronously
    try:
        asyncio.create_task(session.connect())
        logger.info(f"✅ TTS session created: {session_id}")
    except Exception as e:
        logger.error(f"Failed to create TTS session: {e}")
        raise
    
    return session


def get_streaming_tts_session(session_id: str) -> Optional[ElevenLabsStreamingTTS]:
    """Get existing streaming TTS session"""
    return _streaming_tts_sessions.get(session_id)


async def remove_streaming_tts_session(session_id: str):
    """Close and remove streaming TTS session"""
    session = _streaming_tts_sessions.pop(session_id, None)
    if session:
        await session.close()
        logger.info(f"✅ TTS session removed: {session_id}")


def get_all_streaming_tts_sessions() -> Dict[str, ElevenLabsStreamingTTS]:
    """Get all active streaming TTS sessions"""
    return _streaming_tts_sessions.copy()
