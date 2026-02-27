"""Modulate Voice Intelligence - Velma-2 STT (Batch + Streaming) with emotion and speaker diarization"""

from .service import (
    ModulateService,
    get_modulate_service,
    ModulateTranscriptionResult,
)
from .webrtc_integration import (
    transcribe_audio_with_modulate,
    transcribe_audio_with_modulate_full,
    get_modulate_webrtc_info,
)
from .streaming_stt import (
    ModulateStreamingSTT,
    ModulateStreamingSTTSession,
    get_modulate_streaming_session,
    remove_modulate_streaming_session,
    MODULATE_STREAMING_ENABLED,
)

__all__ = [
    "ModulateService",
    "get_modulate_service",
    "ModulateTranscriptionResult",
    "transcribe_audio_with_modulate",
    "transcribe_audio_with_modulate_full",
    "get_modulate_webrtc_info",
    "ModulateStreamingSTT",
    "ModulateStreamingSTTSession",
    "get_modulate_streaming_session",
    "remove_modulate_streaming_session",
    "MODULATE_STREAMING_ENABLED",
]
