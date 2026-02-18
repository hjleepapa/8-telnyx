"""ElevenLabs Speech Services - TTS, Realtime STT, and Multi-Context WebSocket Streaming"""

# REST API Services
from .service import ElevenLabsService, get_elevenlabs_service, EmotionType

# WebSocket Streaming STT (Realtime)
from .streaming_stt import (
    ElevenLabsStreamingSTT,
    CommitStrategy,
    create_streaming_stt_session,
    create_streaming_stt_session_sync,
    get_streaming_stt_session,
    remove_streaming_stt_session,
    get_all_streaming_stt_sessions,
)

# WebSocket Streaming TTS (Multi-Context)
from .streaming_tts import (
    ElevenLabsStreamingTTS,
    TextToSpeechOutputFormat,
    TextNormalization,
    VoiceSettings,
    GenerationConfig,
    AudioContext,
    create_streaming_tts_session,
    get_streaming_tts_session,
    remove_streaming_tts_session,
    get_all_streaming_tts_sessions,
)

__all__ = [
    # REST API
    "ElevenLabsService",
    "get_elevenlabs_service",
    "EmotionType",
    # Streaming STT
    "ElevenLabsStreamingSTT",
    "CommitStrategy",
    "create_streaming_stt_session",
    "create_streaming_stt_session_sync",
    "get_streaming_stt_session",
    "remove_streaming_stt_session",
    "get_all_streaming_stt_sessions",
    # Streaming TTS
    "ElevenLabsStreamingTTS",
    "TextToSpeechOutputFormat",
    "TextNormalization",
    "VoiceSettings",
    "GenerationConfig",
    "AudioContext",
    "create_streaming_tts_session",
    "get_streaming_tts_session",
    "remove_streaming_tts_session",
    "get_all_streaming_tts_sessions",
]
