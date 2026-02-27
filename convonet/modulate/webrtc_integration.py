"""
Modulate WebRTC integration
Provides transcription and full result (with emotion) for voice assistant pipeline.
"""

import os
import logging
from typing import Dict, Any, Optional

from .service import get_modulate_service, ModulateTranscriptionResult

logger = logging.getLogger(__name__)


def transcribe_audio_with_modulate(
    audio_buffer: bytes,
    language: str = "en",
    emotion_signal: bool = True,
) -> Optional[str]:
    """
    Transcribe audio buffer using Modulate Velma-2 (text only).
    Drop-in replacement for transcribe_audio_with_deepgram_webrtc.

    Args:
        audio_buffer: Raw audio bytes (PCM, WebM, WAV).
        language: Language code (default "en").
        emotion_signal: Enable emotion detection (used internally, not returned here).

    Returns:
        Transcribed text string or None if failed.
    """
    result = transcribe_audio_with_modulate_full(
        audio_buffer,
        language=language,
        emotion_signal=emotion_signal,
    )
    return result.text if result else None


def transcribe_audio_with_modulate_full(
    audio_buffer: bytes,
    language: str = "en",
    speaker_diarization: bool = False,
    emotion_signal: bool = True,
    accent_identification: bool = False,
) -> Optional[ModulateTranscriptionResult]:
    """
    Transcribe audio buffer using Modulate Velma-2 with full result.
    Returns transcription + emotion + utterances for agent context.

    Args:
        audio_buffer: Raw audio bytes (PCM, WebM, WAV).
        language: Language code (default "en").
        speaker_diarization: Enable speaker diarization.
        emotion_signal: Enable emotion detection.
        accent_identification: Enable accent identification.

    Returns:
        ModulateTranscriptionResult or None if failed.
    """
    try:
        service = get_modulate_service()
        if not service.is_available():
            logger.warning("⚠️ Modulate not configured, skipping")
            return None

        result = service.transcribe_audio_buffer(
            audio_buffer,
            language=language or None,
            speaker_diarization=speaker_diarization,
            emotion_signal=emotion_signal,
            accent_identification=accent_identification,
        )

        if result and result.text:
            logger.info(
                f"✅ Modulate WebRTC transcription: '{result.text[:60]}...' "
                f"(emotion={result.primary_emotion})"
            )
            return result
        return None

    except Exception as e:
        logger.error(f"❌ Modulate WebRTC transcription failed: {e}")
        return None


def get_modulate_webrtc_info() -> Dict[str, Any]:
    """Get information about Modulate service for WebRTC/voice pipeline."""
    return {
        "transcriber": "modulate",
        "model": "velma-2",
        "batch_endpoint": "velma-2-stt-batch",
        "streaming_endpoint": "velma-2-stt-streaming",
        "features": ["transcription", "emotion", "speaker_diarization", "accent", "pii_phi_tagging"],
        "formats": ["WAV", "WebM", "PCM"],
        "languages": "70+",
        "streaming": True,
        "streaming_quota": "5 concurrent, 1000 hrs/month",
        "streaming_cost": "$0.025/hour",
        "api_key_configured": bool(os.getenv("MODULATE_API_KEY")),
        "webrtc_ready": True,
        "emotion_detection": True,
    }
