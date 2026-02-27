"""
Cartesia STT and TTS Service
Provides high-quality, low-latency speech-to-text and text-to-speech using Cartesia API.

Features:
- TTS: Uses CartesiaSDK with streaming SSE for low-latency audio generation
- STT: Streaming WebSocket API for real-time transcription (via cartesia_streaming_stt.py)
        Falls back to Batch API for non-streaming use cases
- Redis integration: Buffers audio chunks in Redis for distributed processing
"""

import os
import logging
import base64
import json
import asyncio
import websockets
from typing import Optional, Generator, AsyncGenerator, Callable, Dict, Any
import httpx
import tempfile

logger = logging.getLogger(__name__)

# wraps raw PCM audio bytes into a valid WAV container.
def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = 48000, channels: int = 1) -> bytes:
    """Wrap raw PCM s16le in WAV header. Required for Cartesia STT API."""
    import struct
    num_samples = len(pcm_bytes) // 2
    data_size = num_samples * 2
    header_size = 44
    file_size = header_size + data_size
    wav = b'RIFF'
    wav += struct.pack('<I', file_size - 8)
    wav += b'WAVE'
    wav += b'fmt '
    wav += struct.pack('<I', 16)
    wav += struct.pack('<H', 1)  # PCM
    wav += struct.pack('<H', channels)
    wav += struct.pack('<I', sample_rate)
    wav += struct.pack('<I', sample_rate * channels * 2)
    wav += struct.pack('<H', channels * 2)
    wav += struct.pack('<H', 16)
    wav += b'data'
    wav += struct.pack('<I', data_size)
    wav += pcm_bytes
    return wav


# Check for Cartesia SDK
try:
    from cartesia import Cartesia
    CARTESIA_SDK_AVAILABLE = True
except ImportError:
    CARTESIA_SDK_AVAILABLE = False
    logger.warning("Cartesia SDK not available. Install with: pip install cartesia")

# Redis support (optional)
try:
    from convonet.redis_manager import redis_manager
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.debug("Redis not available - will use in-memory buffering")

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
        Transcribe audio buffer using Cartesia STT (Batch API - DEPRECATED FOR REAL-TIME)
        
        ⚠️ WARNING: This uses the slow Batch API. For real-time transcription, use:
           - cartesia_streaming_stt.py: WebSocket-based streaming STT
           - Or use Deepgram: get_deepgram_service().transcribe_audio_buffer()
        
        Args:
            audio_buffer: Raw audio bytes (PCM 16-bit LE, 16kHz or 48kHz)
            language: Language code (en, es, fr, etc.) - ISO 639-1 format
            
        Returns:
            Transcribed text or None if failed
        """
        if not self.is_available():
            logger.error("❌ Cartesia service not available (API key missing)")
            return None

        logger.warning("⚠️ Using Cartesia Batch STT (slow). Consider using streaming for real-time.")
        
        # Validate audio
        if not audio_buffer or len(audio_buffer) < 100:
            logger.warning(f"⚠️ Audio too short: {len(audio_buffer)} bytes")
            return None
        
        try:
            # Store in Redis if available (for logging/debugging)
            if REDIS_AVAILABLE and redis_manager.is_available():
                try:
                    session_key = f"cartesia:audio:{id(audio_buffer)}"
                    redis_manager.redis_client.setex(
                        session_key,
                        3600,
                        base64.b64encode(audio_buffer[:10000]).decode('utf-8')  # Store first 10KB
                    )
                except Exception as e:
                    logger.debug(f"Could not store audio preview in Redis: {e}")
            
            # LiveKit sends 48kHz mono PCM; wrap in proper WAV header (raw PCM is NOT valid WAV)
            wav_bytes = _pcm_to_wav(audio_buffer, sample_rate=48000, channels=1)
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                wav_path = temp_file.name
                temp_file.write(wav_bytes)
            
            try:
                # Use Cartesia Batch API endpoint
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Cartesia-Version": self.stt_version,
                }
                
                files = {"file": ("audio.wav", open(wav_path, 'rb'), "audio/wav")}
                data = {
                    "model": self.stt_model,  # "ink-whisper"
                    "language": language,
                    "timestamp_granularities[]": "word",
                }
                
                logger.info(f"📤 Cartesia Batch STT: Uploading {len(wav_bytes)} bytes WAV (48kHz)...")
                
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
                    logger.info(f"✅ Cartesia STT success: '{text[:50]}...'")
                    return text
                elif response.status_code == 401:
                    logger.error("❌ Cartesia: Invalid API key")
                    return None
                elif response.status_code == 400:
                    logger.error(f"❌ Cartesia: Bad request - {response.text}")
                    return None
                else:
                    logger.error(f"❌ Cartesia STT failed: {response.status_code} - {response.text}")
                    return None
                    
            finally:
                if os.path.exists(wav_path):
                    os.unlink(wav_path)
                
        except FileNotFoundError:
            logger.error("❌ Could not create temp WAV file")
            return None
        except httpx.ConnectError:
            logger.error("❌ Cartesia API connection failed - check network")
            return None
        except Exception as e:
            logger.error(f"❌ Cartesia STT error: {e}")
            import traceback
            traceback.print_exc()
            return None

    def synthesize_stream(self, text: str, voice_id: Optional[str] = None, sample_rate: int = 48000) -> Generator[bytes, None, None]:
        """
        Stream TTS audio from Cartesia using SSE (Server-Sent Events)
        
        🎯 **WebRTC-Optimized**: Uses 48kHz sample rate to match WebRTC audio format.
           This eliminates resampling overhead and ensures perfect synchronization.
        
        **Approach Used**: SSE Streaming via Cartesia SDK
        - ✅ Low latency (chunks stream in real-time)
        - ✅ Efficient for long texts
        - ✅ Perfect for real-time voice conversations
        - ✅ No resampling needed (native 48kHz)
        
        Args:
            text: Text to synthesize
            voice_id: Voice ID (optional, defaults to Kiefer)
            sample_rate: Output sample rate (default 48000 for WebRTC)
            
        Yields:
            Audio chunks (bytes) in PCM 16-bit LE format @ sample_rate Hz
        """
        if not self.is_available() or not self.client:
            logger.error("❌ Cartesia SDK not initialized")
            return

        try:
            voice_id = voice_id or self.voice_id
            logger.info(f"🔊 Cartesia TTS (SSE @ {sample_rate}Hz): {text[:50]}... (voice: {voice_id})")
            
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
                    "encoding": "pcm_s16le",  # PCM 16-bit little-endian
                    "sample_rate": sample_rate  # 48kHz for WebRTC compatibility
                }
            )
            
            chunk_count = 0
            for chunk_event in response_iter:
                # ChunkEvent is a Pydantic model with direct attribute access
                # Audio data is base64-encoded in the 'audio' attribute
                if hasattr(chunk_event, 'audio') and chunk_event.audio:
                    try:
                        # Decode base64 audio to bytes (streaming chunks may lack padding or have invalid chars)
                        audio_b64 = chunk_event.audio
                        if isinstance(audio_b64, str):
                            audio_b64 = audio_b64.encode('ascii')
                        # Strip whitespace/newlines that can cause "data characters" validation errors
                        audio_b64 = audio_b64.replace(b'\n', b'').replace(b'\r', b'').replace(b' ', b'')
                        # Add padding so length is multiple of 4 (required for b64decode)
                        pad_len = (-len(audio_b64) % 4)
                        if pad_len:
                            audio_b64 = audio_b64 + b'=' * pad_len
                        # Use validate=False for lenient decode (handles edge cases in streaming)
                        chunk = base64.b64decode(audio_b64, validate=False)
                        chunk_count += 1
                        if chunk_count == 1:
                            logger.info(f"🎵 Received first TTS chunk ({len(chunk)} bytes)")
                        yield chunk
                    except Exception as decode_err:
                        # Skip corrupted chunks (e.g. 169 chars = 4k+1 invalid) to keep stream alive
                        logger.warning("⚠️ Cartesia TTS: Skipping chunk (decode error: %s)", decode_err)
            
            logger.info(f"✅ Cartesia TTS stream complete: {chunk_count} chunks")
                
        except Exception as e:
            logger.error(f"❌ Cartesia TTS streaming error: {e}")
            import traceback
            traceback.print_exc()

    def synthesize_rest_api(self, text: str, voice_id: Optional[str] = None, sample_rate: int = 48000) -> Optional[bytes]:
        """
        Generate TTS audio using REST API (non-streaming)
        
        **Approach**: Direct REST POST to `/tts/bytes` endpoint
        - ❌ Higher latency (full audio generation before response)
        - ✅ Simpler implementation
        - ✅ Good for offline/batch generation  - ❌ Not suitable for real-time WebRTC
        - ❌ Memory intensive for long texts
        
        Args:
            text: Text to synthesize
            voice_id: Voice ID (optional)
            sample_rate: Output sample rate (default 48000)
            
        Returns:
            Complete audio bytes (PCM 16-bit LE) or None if failed
        """
        if not self.api_key:
            logger.error("❌ Cartesia API key not set")
            return None
        
        try:
            import httpx
            
            # Prepare request payload
            payload = {
                "model_id": self.model_id,
                "transcript": text,
                "voice": {
                    "mode": "id",
                    "id": voice_id or self.voice_id
                },
                "output_format": {
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": sample_rate
                }
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Cartesia-Version": "2025-04-16",
                "Content-Type": "application/json"
            }
            
            logger.info(f"📄 Cartesia TTS (REST API @ {sample_rate}Hz): {text[:50]}...")
            
            # Single POST request, returns complete audio
            response = httpx.post(
                "https://api.cartesia.ai/tts/bytes",
                json=payload,
                headers=headers,
                timeout=30.0
            )
            
            if response.status_code == 200:
                audio_bytes = response.content
                logger.info(f"✅ Cartesia REST TTS success: {len(audio_bytes)} bytes")
                return audio_bytes
            elif response.status_code == 401:
                logger.error("❌ Cartesia: Invalid API key")
                return None
            elif response.status_code == 400:
                logger.error(f"❌ Cartesia: Bad request - {response.text}")
                return None
            else:
                logger.error(f"❌ Cartesia REST API failed: {response.status_code} - {response.text}")
                return None
                
        except ImportError:
            logger.error("❌ httpx not available for REST API")
            return None
        except Exception as e:
            logger.error(f"❌ Cartesia REST TTS error: {e}")
            return None
    
    @staticmethod
    def get_approach_comparison() -> Dict[str, Dict[str, str]]:
        """
        Return comparison of available Cartesia TTS approaches
        
        Returns:
            Dictionary with approach details and recommendations
        """
        return {
            "sse_streaming": {
                "name": "SSE Streaming (RECOMMENDED)",
                "method": "self.client.tts.sse()",
                "latency": "Low (~100-200ms first chunk)",
                "use_case": "Real-time WebRTC conversations ⭐",
                "webrtc_friendly": "✅ YES (48kHz native)",
                "resampling_needed": "❌ NO",
                "pros": "Lowest latency, real-time chunks, perfect for WebRTC",
                "cons": "More complex streaming handling",
                "implementation": "synthesize_stream(text, voice_id, sample_rate=48000)"
            },
            "rest_api": {
                "name": "REST API (/tts/bytes)",
                "method": "POST /tts/bytes direct HTTP",
                "latency": "High (~1-3s full response)",
                "use_case": "Batch/offline generation",
                "webrtc_friendly": "❌ Slow for real-time",
                "resampling_needed": "❌ NO",
                "pros": "Simple, direct control, good for one-shot",
                "cons": "Full latency before audio starts",
                "implementation": "synthesize_rest_api(text, voice_id, sample_rate=48000)"
            },
            "websocket": {
                "name": "WebSocket Streaming",
                "method": "Cartesia WebSocket API",
                "latency": "N/A - Not provided by Cartesia",
                "use_case": "Not available",
                "webrtc_friendly": "N/A",
                "resampling_needed": "N/A",
                "pros": "Would be ideal for WebRTC",
                "cons": "Not offered in public Cartesia API",
                "implementation": "Use SSE as next-best option"
            }
        }
    
    @staticmethod
    def print_approach_guide():
        """Print a helpful guide for choosing the right approach"""
        guide = """
╔════════════════════════════════════════════════════════════════════════════╗
║                  CARTESIA TTS IMPLEMENTATION GUIDE                        ║
╠════════════════════════════════════════════════════════════════════════════╣
║                                                                            ║
║  RECOMMENDATION FOR WEBRTC: SSE Streaming @ 48kHz                        ║
║  ─────────────────────────────────────────────────                       ║
║                                                                            ║
║  Why SSE Streaming?                                                      ║
║  • Real-time audio chunks (perfect for conversational AI)                ║
║  • 48kHz native output (matches WebRTC exactly)                          ║
║  • No resampling overhead                                                ║
║  • Low latency (~100-200ms to first chunk)                               ║
║                                                                            ║
║  ✅ CURRENT: SSE Streaming at 48kHz                                      ║
║  ✅ STATUS: Optimized for WebRTC                                         ║
║                                                                            ║
║  Alternative: REST API (/tts/bytes)                                      ║
║  • Use only for: Batch/offline audio generation                          ║
║  • NOT suitable for: Real-time WebRTC (1-3s latency)                    ║
║                                                                            ║
║  Usage:                                                                  ║
║  ────                                                                    ║
║  cartesia = get_cartesia_service()                                       ║
║                                                                            ║
║  # For real-time WebRTC (RECOMMENDED):                                   ║
║  audio_gen = cartesia.synthesize_stream(                                 ║
║      text="Hello world",                                                 ║
║      voice_id="228fca29-3a0a-435c-8728-5cb483251068",                  ║
║      sample_rate=48000  # Matches WebRTC                                ║
║  )                                                                       ║
║  for chunk in audio_gen:                                                 ║
║      send_to_webrtc(chunk)  # Send directly, no resampling needed       ║
║                                                                            ║
║  # For batch generation (if needed):                                     ║
║  audio_bytes = cartesia.synthesize_rest_api(                             ║
║      text="Generate offline",                                            ║
║      sample_rate=48000                                                   ║
║  )                                                                       ║
║                                                                            ║
╚════════════════════════════════════════════════════════════════════════════╝
        """
        print(guide)
    
    def get_streaming_stt_session(self, session_id: str, on_final: Callable[[str], None]):
        """
        Get a streaming STT session for real-time transcription
        
        This is the RECOMMENDED approach for real-time voice calls.
        Uses cartesia_streaming_stt.CartesiaStreamingSTT with WebSocket API.
        
        Args:
            session_id: Unique session identifier
            on_final: Callback function when final transcription arrives
            
        Returns:
            CartesiaStreamingSTT session or None if not available
            
        Usage:
            >>> session = cartesia_service.get_streaming_stt_session(
            ...     session_id="user_123",
            ...     on_final=lambda text: print(f"Transcribed: {text}")
            ... )
            >>> session.start()
            >>> session.send_audio_chunk(audio_bytes)
            >>> session.stop()
        """
        try:
            from convonet.cartesia import get_cartesia_streaming_session
            
            logger.info(f"📡 Creating Cartesia streaming STT session: {session_id}")
            
            return get_cartesia_streaming_session(
                session_id=session_id,
                on_final=on_final,
                language=self.language_code if hasattr(self, 'language_code') else "en"
            )
        except ImportError:
            logger.error("❌ cartesia_streaming_stt module not found")
            return None
        except Exception as e:
            logger.error(f"❌ Failed to create streaming session: {e}")
            return None
    
    def list_voices(self) -> Optional[Dict[str, Any]]:
        """
        List available Cartesia voices
        
        Returns:
            Dictionary with voice information or None
        """
        if not self.client:
            logger.warning("⚠️ Cartesia SDK not available")
            return None
        
        try:
            # Check if SDK has voices endpoint
            if hasattr(self.client, 'voices'):
                voices = self.client.voices.list()
                return {"voices": voices}
            else:
                logger.warning("⚠️ Cartesia SDK doesn't expose voices endpoint")
                # Return known good defaults
                return {
                    "default_voice": self.voice_id,
                    "model": self.model_id,
                    "note": "Visit https://cartesia.ai to explore voices"
                }
        except Exception as e:
            logger.error(f"❌ Failed to list voices: {e}")
            return None

_cartesia_service = None

def get_cartesia_service() -> CartesiaService:
    global _cartesia_service
    if _cartesia_service is None:
        _cartesia_service = CartesiaService()
    return _cartesia_service


"""
═══════════════════════════════════════════════════════════════════════════════
INTEGRATION GUIDE: Using Cartesia with WebRTC Voice Server
═══════════════════════════════════════════════════════════════════════════════

## Quick Start: Real-Time STT with Streaming

### 1. In webrtc_voice_server_socketio.py:

```python
from cartesia_service import get_cartesia_service
from cartesia_streaming_stt import get_cartesia_streaming_session

# In handle_start_recording():
if stt_provider == "cartesia":
    cartesia_service = get_cartesia_service()
    
    # Create callbacks for transcription events
    def on_final_transcript(text: str):
        socketio.emit('transcription', {
            'success': True,
            'text': text,
            'provider': 'cartesia'
        }, to=session_id)
    
    # Get streaming STT session
    streaming_session = cartesia_service.get_streaming_stt_session(
        session_id=session_id,
        on_final=on_final_transcript
    )
    
    if streaming_session:
        streaming_session.start()
        streaming_sessions[session_id] = streaming_session

# When audio chunk arrives:
def handle_audio_chunk(data):
    if session_id in streaming_sessions:
        streaming_session = streaming_sessions[session_id]
        streaming_session.send_audio_chunk(data['audio'])
```

### 2. Redis Integration (Optional but Recommended):

The streaming STT will:
- Buffer audio chunks in Redis for fault tolerance
- Store transcription history
- Enable multi-instance horizontal scaling

Redis keys used:
- `cartesia:audio:{session_id}` - Audio buffer
- `cartesia:transcript:{session_id}` - Latest transcription

### 3. Audio Format Requirements:

Cartesia STT expects:
- Sample rate: 16kHz or 48kHz
- Encoding: PCM 16-bit little-endian
- Mono channel preferred
- No WebM wrapper (unlike Deepgram)

Browser WebRTC audio is typically 48kHz, so conversion may be needed.

### 4. Fallback to Batch API:

For non-streaming use cases, use `transcribe_audio_buffer()`:

```python
cartesia_service = get_cartesia_service()
transcription = cartesia_service.transcribe_audio_buffer(audio_bytes)
```

⚠️ Note: Batch API is slow (~5-10s) - only use when real-time not needed.

### 5. TTS Integration:

```python
# Streaming TTS
cartesia_service = get_cartesia_service()

for audio_chunk in cartesia_service.synthesize_stream("Hello world"):
    # Send chunk to client
    socketio.emit('audio_chunk', {'audio': base64.b64encode(audio_chunk).decode()})
```

═══════════════════════════════════════════════════════════════════════════════
"""

