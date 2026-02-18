"""Deepgram Speech Services - STT and TTS"""

from .service import DeepgramService, get_deepgram_service
from .webrtc_integration import (
    transcribe_audio_with_deepgram_webrtc,
    get_deepgram_webrtc_info,
)

__all__ = [
    "DeepgramService",
    "get_deepgram_service",
    "transcribe_audio_with_deepgram_webrtc",
    "get_deepgram_webrtc_info",
]
