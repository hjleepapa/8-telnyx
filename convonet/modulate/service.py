"""
Modulate Voice Intelligence Service
Uses Velma-2 Batch API for transcription with emotion detection, speaker diarization, and accent identification.

API Docs: https://modulate-developer-apis.com/web/docs.html
Supports: AAC, AIFF, FLAC, MP3, MP4, MOV, OGG, Opus, WAV, WebM up to 100MB
"""

import os
import logging
import tempfile
import struct
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

MODULATE_API_BASE = "https://modulate-developer-apis.com"


@dataclass
class ModulateUtterance:
    """Single utterance from Modulate response."""
    text: str
    start_ms: int
    duration_ms: int
    speaker: Optional[int] = None
    language: Optional[str] = None
    emotion: Optional[str] = None
    accent: Optional[str] = None
    utterance_uuid: Optional[str] = None


@dataclass
class ModulateTranscriptionResult:
    """Full transcription result from Modulate Velma-2."""
    text: str
    duration_ms: int
    utterances: List[ModulateUtterance] = field(default_factory=list)
    raw_response: Optional[Dict[str, Any]] = None

    @property
    def primary_emotion(self) -> Optional[str]:
        """Get dominant emotion from utterances (first non-Neutral, or first)."""
        for u in self.utterances:
            if u.emotion and u.emotion.lower() != "neutral":
                return u.emotion
        return self.utterances[0].emotion if self.utterances else None


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 48000, channels: int = 1) -> bytes:
    """Wrap raw PCM s16le in WAV header for Modulate API."""
    num_samples = len(pcm_bytes) // 2
    data_size = num_samples * 2
    header_size = 44
    file_size = header_size + data_size
    wav = b"RIFF"
    wav += struct.pack("<I", file_size - 8)
    wav += b"WAVE"
    wav += b"fmt "
    wav += struct.pack("<I", 16)
    wav += struct.pack("<H", 1)  # PCM
    wav += struct.pack("<H", channels)
    wav += struct.pack("<I", sample_rate)
    wav += struct.pack("<I", sample_rate * channels * 2)
    wav += struct.pack("<H", channels * 2)
    wav += struct.pack("<H", 16)
    wav += b"data"
    wav += struct.pack("<I", data_size)
    wav += pcm_bytes
    return wav


class ModulateService:
    """Modulate Velma-2 Voice Intelligence API service."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv("MODULATE_API_KEY")
        if not self.api_key:
            logger.warning("⚠️ MODULATE_API_KEY not set. Modulate services will not work.")
        else:
            logger.info("✅ Modulate Velma-2 service initialized")

    def is_available(self) -> bool:
        """Check if Modulate service is available."""
        return bool(self.api_key)

    def transcribe_audio_buffer(
        self,
        audio_buffer: bytes,
        language: Optional[str] = None,
        speaker_diarization: bool = False,
        emotion_signal: bool = True,
        accent_identification: bool = False,
    ) -> Optional[ModulateTranscriptionResult]:
        """
        Transcribe audio buffer using Modulate Velma-2 Batch API.

        Args:
            audio_buffer: Raw audio bytes (PCM, WebM, WAV, etc.)
            language: Optional language hint (e.g. "en"). None = auto-detect.
            speaker_diarization: Enable speaker diarization.
            emotion_signal: Enable emotion detection.
            accent_identification: Enable accent identification.

        Returns:
            ModulateTranscriptionResult or None if failed.
        """
        if not self.is_available():
            logger.error("❌ Modulate API key not configured")
            return None

        if not audio_buffer or len(audio_buffer) < 500:
            logger.warning(f"⚠️ Audio too short: {len(audio_buffer)} bytes")
            return None

        # Detect format and prepare file
        is_webm = len(audio_buffer) >= 4 and audio_buffer[:4] == b"\x1a\x45\xdf\xa3"
        is_wav = len(audio_buffer) >= 12 and audio_buffer[:4] == b"RIFF" and b"WAVE" in audio_buffer[:12]

        temp_path = None
        try:
            if is_webm:
                suffix = ".webm"
                content_type = "audio/webm"
            elif is_wav:
                suffix = ".wav"
                content_type = "audio/wav"
            else:
                # Treat as raw PCM (LiveKit 48kHz mono)
                suffix = ".wav"
                content_type = "audio/wav"
                audio_buffer = _pcm_to_wav(audio_buffer, sample_rate=48000, channels=1)

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                f.write(audio_buffer)
                temp_path = f.name

            return self._transcribe_file(
                temp_path,
                content_type=content_type,
                language=language,
                speaker_diarization=speaker_diarization,
                emotion_signal=emotion_signal,
                accent_identification=accent_identification,
            )
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except OSError:
                    pass

    def _transcribe_file(
        self,
        file_path: str,
        content_type: str = "audio/wav",
        language: Optional[str] = None,
        speaker_diarization: bool = False,
        emotion_signal: bool = True,
        accent_identification: bool = False,
    ) -> Optional[ModulateTranscriptionResult]:
        """Call Modulate Velma-2 Batch API with file upload."""
        try:
            headers = {"X-API-Key": self.api_key}

            data = {
                "speaker_diarization": str(speaker_diarization).lower(),
                "emotion_signal": str(emotion_signal).lower(),
                "accent_identification": str(accent_identification).lower(),
            }
            if language:
                data["language"] = language

            with open(file_path, "rb") as f:
                files = {"upload_file": (os.path.basename(file_path), f, content_type)}

                logger.info(
                    f"📤 Modulate Velma-2: Uploading {os.path.getsize(file_path)} bytes "
                    f"(emotion={emotion_signal}, diarization={speaker_diarization})"
                )

                response = requests.post(
                    f"{MODULATE_API_BASE}/api/velma-2-stt-batch",
                    headers=headers,
                    files=files,
                    data=data,
                    timeout=30,
                )

            response.raise_for_status()
            result = response.json()

            utterances = []
            for u in result.get("utterances", []):
                utterances.append(
                    ModulateUtterance(
                        text=u.get("text", ""),
                        start_ms=u.get("start_ms", 0),
                        duration_ms=u.get("duration_ms", 0),
                        speaker=u.get("speaker"),
                        language=u.get("language"),
                        emotion=u.get("emotion"),
                        accent=u.get("accent"),
                        utterance_uuid=u.get("utterance_uuid"),
                    )
                )

            transcription = ModulateTranscriptionResult(
                text=result.get("text", "").strip(),
                duration_ms=result.get("duration_ms", 0),
                utterances=utterances,
                raw_response=result,
            )

            logger.info(
                f"✅ Modulate transcription: '{transcription.text[:80]}...' "
                f"(emotion={transcription.primary_emotion})"
            )
            return transcription

        except requests.exceptions.RequestException as e:
            logger.error(f"❌ Modulate API request failed: {e}")
            if hasattr(e, "response") and e.response is not None:
                try:
                    err_body = e.response.json()
                    logger.error(f"   Response: {err_body}")
                except Exception:
                    logger.error(f"   Response text: {e.response.text[:500]}")
            return None
        except Exception as e:
            logger.error(f"❌ Modulate transcription failed: {e}")
            return None


_modulate_service: Optional[ModulateService] = None


def get_modulate_service(api_key: Optional[str] = None) -> ModulateService:
    """Get or create singleton ModulateService instance."""
    global _modulate_service
    if _modulate_service is None:
        _modulate_service = ModulateService(api_key=api_key)
    return _modulate_service
