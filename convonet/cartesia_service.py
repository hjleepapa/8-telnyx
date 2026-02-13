"""
Cartesia STT and TTS Service
Provides high-quality, low-latency speech-to-text and text-to-speech using Cartesia API.
"""

import os
import logging
import base64
import json
import asyncio
import websockets
from typing import Optional, Generator, AsyncGenerator
import httpx

logger = logging.getLogger(__name__)

# Check for Cartesia SDK
try:
    from cartesia import Cartesia
    CARTESIA_SDK_AVAILABLE = True
except ImportError:
    CARTESIA_SDK_AVAILABLE = False
    logger.warning("Cartesia SDK not available. Install with: pip install cartesia")

class CartesiaService:
    """Service for Cartesia STT and TTS"""
    
    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.getenv('CARTESIA_API_KEY')
        if not self.api_key:
            logger.warning("⚠️ CARTESIA_API_KEY not set. Cartesia services will not work.")
            self.client = None
        elif CARTESIA_SDK_AVAILABLE:
            self.client = Cartesia(api_key=self.api_key)
        else:
            self.client = None
            
        # Default settings
        self.model_id = "sonic-english"  # Default TTS model
        self.voice_id = "228fca29-3a0a-435c-8728-5cb483251068"  # Kiefer (Sonic 3)
        self.stt_model = "ink-whisper" # Default STT model (Cartesia Ink)
        self.stt_version = "2024-02-29"

    def is_available(self) -> bool:
        """Check if Cartesia service is available"""
        return bool(self.api_key and (CARTESIA_SDK_AVAILABLE or self.api_key))

    def transcribe_audio_buffer(self, audio_buffer: bytes, language: str = "en") -> Optional[str]:
        """
        Transcribe audio buffer using Cartesia STT (REST API)
        
        Args:
            audio_buffer: Raw audio bytes
            language: Language code (default: en)
            
        Returns:
            Transcribed text or None
        """
        if not self.is_available():
            return None

        # Cartesia STT currently uses a REST endpoint (based on reference)
        # We'll use httpx directly if SDK doesn't support convenient buffer upload or just to match reference
        
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Cartesia-Version": self.stt_version,
            }
            
            # Prepare multipart form data
            files = {"file": ("audio.wav", audio_buffer, "audio/wav")} 
            data = {
                "model": self.stt_model,
                "language": language,
                "timestamp_granularities[]": "word",
            }
            
            logger.info(f"📤 Cartesia STT: Uploading {len(audio_buffer)} bytes...")
            
            response = httpx.post(
                "https://api.cartesia.ai/stt",
                headers=headers,
                files=files,
                data=data,
                timeout=30.0,
            )
            
            if response.status_code == 200:
                payload = response.json()
                text = payload.get("text", "").strip()
                logger.info(f"✅ Cartesia STT success: '{text}'")
                return text
            else:
                logger.error(f"❌ Cartesia STT failed: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Cartesia STT error: {e}")
            return None

    def synthesize_stream(self, text: str, voice_id: Optional[str] = None) -> Generator[bytes, None, None]:
        """
        Stream TTS audio from Cartesia
        
        Args:
            text: Text to synthesize
            voice_id: Voice ID (optional)
            
        Yields:
            Audio chunks (bytes)
        """
        if not self.is_available() or not self.client:
            logger.error("Cartesia SDK not initialized")
            return

    def synthesize_stream(self, text: str, voice_id: Optional[str] = None) -> Generator[bytes, None, None]:
        """
        Stream TTS audio from Cartesia
        
        Args:
            text: Text to synthesize
            voice_id: Voice ID (optional)
            
        Yields:
            Audio chunks (bytes)
        """
        if not self.is_available() or not self.client:
            logger.error("Cartesia SDK not initialized")
            return

        try:
            voice_id = voice_id or self.voice_id
            print(f"🔊 CartesiaService.synthesize_stream: Starting for text: {text[:50]}... (voice: {voice_id})", flush=True)
            
            # Use the SDK's sse method for streaming audio chunks
            # Returns a generator yielding ChunkEvent objects (Pydantic models)
            response_iter = self.client.tts.sse(
                model_id=self.model_id,
                transcript=text,
                voice={
                    "mode": "id",
                    "id": voice_id
                },
                output_format={
                    "container": "raw",
                    "encoding": "pcm_s16le", # PCM 16-bit little-endian
                    "sample_rate": 44100
                }
            )
            
            chunk_count = 0
            for chunk_event in response_iter:
                # ChunkEvent is a Pydantic model with direct attribute access
                # Audio data is base64-encoded in the 'audio' attribute
                if hasattr(chunk_event, 'audio') and chunk_event.audio:
                    # Decode base64 audio to bytes
                    import base64
                    chunk = base64.b64decode(chunk_event.audio)
                    chunk_count += 1
                    if chunk_count == 1:
                        print(f"✅ CartesiaService: Received first chunk ({len(chunk)} bytes)", flush=True)
                    yield chunk
            print(f"✅ CartesiaService: SSE stream complete, total chunks: {chunk_count}", flush=True)
                
        except Exception as e:
            logger.error(f"❌ Cartesia TTS streaming error: {e}")
            import traceback
            traceback.print_exc()

_cartesia_service = None

def get_cartesia_service() -> CartesiaService:
    global _cartesia_service
    if _cartesia_service is None:
        _cartesia_service = CartesiaService()
    return _cartesia_service
