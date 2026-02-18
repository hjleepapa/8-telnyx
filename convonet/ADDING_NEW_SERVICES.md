# Adding New STT/TTS Services - Architecture Guide

## Overview

This guide explains how to add new speech services (Google, Azure, AssemblyAI, etc.) to your Convonet platform. The architecture is designed for easy extensibility.

---

## Architecture: Service Pattern

All speech services follow this unified pattern:

```python
class SpeechService:
    """Base pattern for all speech services"""
    
    def __init__(self, api_key: Optional[str] = None):
        """Initialize with API credentials"""
        self.api_key = api_key or os.getenv('PROVIDER_API_KEY')
    
    def is_available(self) -> bool:
        """Check if service initialized and ready"""
        return bool(self.api_key)
    
    # STT Methods
    def transcribe_audio_buffer(self, audio_buffer: bytes, language: str = "en") -> Optional[str]:
        """Batch STT - for non-streaming use"""
        pass
    
    def get_streaming_stt_session(self, session_id: str, on_final: Callable) -> StreamingSession:
        """Streaming STT - for real-time transcription (preferred)"""
        pass
    
    # TTS Methods  
    def synthesize(self, text: str, **kwargs) -> Optional[bytes]:
        """Generate audio from text"""
        pass
    
    def synthesize_stream(self, text: str) -> Generator[bytes, None, None]:
        """Stream audio chunks (preferred for real-time)"""
        pass
```

---

## Step 1: Create Service Module

### Directory Structure

```
convonet/
├── speech_services/          # New folder for organized services
│   ├── __init__.py
│   ├── base.py              # Abstract base class
│   ├── cartesia_service.py  # Existing
│   ├── deepgram_service.py  # Existing
│   ├── elevenlabs_service.py # Existing
│   ├── google_speech.py      # New service
│   ├── azure_speech.py       # New service
│   └── assemblyai_service.py # New service
├── streaming_stt/
│   ├── cartesia_streaming_stt.py
│   ├── deepgram_streaming.py
│   └── google_streaming_stt.py  # New
```

---

## Step 2: Define Base Class

Create `convonet/speech_services/base.py`:

```python
"""Base classes for all speech services"""

from abc import ABC, abstractmethod
from typing import Optional, Callable, Generator, Dict, Any
import logging

logger = logging.getLogger(__name__)

class STTSession(ABC):
    """Abstract base for streaming STT sessions"""
    
    @abstractmethod
    def start(self) -> bool:
        """Start the streaming session"""
        pass
    
    @abstractmethod
    def send_audio_chunk(self, audio_chunk: bytes):
        """Send audio chunk for transcription"""
        pass
    
    @abstractmethod
    def stop(self):
        """Stop the streaming session"""
        pass


class SpeechService(ABC):
    """Abstract base class for all speech services"""
    
    def __init__(self, api_key: Optional[str] = None, provider_name: str = "unknown"):
        """Initialize speech service
        
        Args:
            api_key: API key (uses env var if None)
            provider_name: Name of provider (for logging)
        """
        self.api_key = api_key
        self.provider_name = provider_name
    
    @abstractmethod
    def is_available(self) -> bool:
        """Check if service is available and initialized"""
        pass
    
    # ============= STT =============
    
    @abstractmethod
    def transcribe_audio_buffer(self, audio_buffer: bytes, language: str = "en") -> Optional[str]:
        """Batch STT - convert audio buffer to text
        
        Args:
            audio_buffer: Raw audio bytes
            language: Language code (ISO 639-1, e.g., "en", "es", "fr")
            
        Returns:
            Transcribed text or None if failed
        """
        pass
    
    @abstractmethod
    def get_streaming_stt_session(
        self,
        session_id: str,
        on_final: Callable[[str], None],
        on_partial: Optional[Callable[[str], None]] = None,
        language: str = "en"
    ) -> Optional[STTSession]:
        """Get a streaming STT session for real-time transcription
        
        Args:
            session_id: Unique session ID
            on_final: Callback when final transcription arrives
            on_partial: Callback for interim results (optional)
            language: Language code
            
        Returns:
            Streaming session or None
        """
        pass
    
    # ============= TTS =============
    
    @abstractmethod
    def synthesize(self, text: str, **kwargs) -> Optional[bytes]:
        """Generate speech from text (non-streaming)
        
        Args:
            text: Text to synthesize
            **kwargs: Service-specific options (voice_id, emotion, etc.)
            
        Returns:
            Audio bytes or None
        """
        pass
    
    @abstractmethod
    def synthesize_stream(self, text: str, **kwargs) -> Generator[bytes, None, None]:
        """Stream speech from text (preferred for real-time)
        
        Args:
            text: Text to synthesize
            **kwargs: Service-specific options
            
        Yields:
            Audio chunks (bytes)
        """
        pass
    
    # ============= Service Info =============
    
    def get_info(self) -> Dict[str, Any]:
        """Get service information and capabilities
        
        Override in subclass for provider-specific details
        """
        return {
            "provider": self.provider_name,
            "available": self.is_available(),
            "capabilities": {
                "stt_batch": True,
                "stt_streaming": True,
                "tts_batch": True,
                "tts_streaming": True
            }
        }
```

---

## Step 3: Implement New Service (Google Example)

Create `convonet/speech_services/google_speech.py`:

```python
"""Google Cloud Speech-to-Text and Text-to-Speech Integration"""

import os
import logging
from typing import Optional, Callable, Generator, Dict, Any
from google.cloud import speech_v1
from google.cloud import texttospeech_v1
from speech_services.base import SpeechService, STTSession

logger = logging.getLogger(__name__)

class GoogleSTTSession(STTSession):
    """Streaming STT using Google Cloud Speech"""
    
    def __init__(self, session_id: str, api_config: Dict, callbacks: Dict):
        self.session_id = session_id
        self.api_config = api_config
        self.on_final = callbacks.get('on_final')
        self.on_partial = callbacks.get('on_partial')
        self.running = False
    
    def start(self) -> bool:
        """Start streaming"""
        try:
            client = speech_v1.SpeechClient()
            self.client = client
            self.running = True
            logger.info(f"✅ Google STT streaming started: {self.session_id}")
            return True
        except Exception as e:
            logger.error(f"❌ Google STT init failed: {e}")
            return False
    
    def send_audio_chunk(self, audio_chunk: bytes):
        """Send audio to Google"""
        if not self.running:
            return
        
        try:
            # Implement streaming logic
            # Use Google's streaming_recognize API
            pass
        except Exception as e:
            logger.error(f"❌ Send audio error: {e}")
    
    def stop(self):
        """Stop streaming"""
        self.running = False
        logger.info(f"🛑 Google STT stopped: {self.session_id}")


class GoogleSpeechService(SpeechService):
    """Google Cloud Speech-to-Text and Text-to-Speech Service"""
    
    def __init__(self):
        super().__init__(provider_name="google_cloud")
        
        # Google uses service account JSON, not direct API key
        credentials_path = os.getenv('GOOGLE_APPLICATION_CREDENTIALS')
        self.credentials_path = credentials_path
        
        # Initialize clients
        try:
            self.stt_client = speech_v1.SpeechClient()
            self.tts_client = texttospeech_v1.TextToSpeechClient()
            self.api_key = "configured"  # Google uses service account
        except Exception as e:
            logger.error(f"Google initialization failed: {e}")
            self.api_key = None
    
    def is_available(self) -> bool:
        """Check if Google credentials available"""
        return bool(self.credentials_path and os.path.exists(self.credentials_path))
    
    def transcribe_audio_buffer(self, audio_buffer: bytes, language: str = "en") -> Optional[str]:
        """Batch STT using Google Cloud Speech"""
        try:
            config = speech_v1.RecognitionConfig(
                encoding=speech_v1.RecognitionConfig.AudioEncoding.LINEAR16,
                sample_rate_hertz=16000,
                language_code=language,
            )
            
            audio = speech_v1.RecognitionAudio(content=audio_buffer)
            response = self.stt_client.recognize(config=config, audio=audio)
            
            # Extract text from results
            transcription = ""
            for result in response.results:
                if result.alternatives:
                    transcription = result.alternatives[0].transcript
                    break
            
            return transcription if transcription else None
            
        except Exception as e:
            logger.error(f"❌ Google STT failed: {e}")
            return None
    
    def get_streaming_stt_session(
        self,
        session_id: str,
        on_final: Callable[[str], None],
        on_partial: Optional[Callable[[str], None]] = None,
        language: str = "en"
    ) -> Optional[STTSession]:
        """Get streaming STT session"""
        try:
            callbacks = {'on_final': on_final, 'on_partial': on_partial}
            api_config = {'language': language}
            
            session = GoogleSTTSession(session_id, api_config, callbacks)
            
            if session.start():
                return session
            return None
            
        except Exception as e:
            logger.error(f"❌ Failed to create Google streaming session: {e}")
            return None
    
    def synthesize(self, text: str, voice_name: str = "en-US-Neural2-C", **kwargs) -> Optional[bytes]:
        """Generate speech from text"""
        try:
            input_text = texttospeech_v1.SynthesisInput(text=text)
            voice = texttospeech_v1.VoiceSelectionParams(
                language_code="en-US",
                name=voice_name,
            )
            audio_config = texttospeech_v1.AudioConfig(
                audio_encoding=texttospeech_v1.AudioEncoding.MP3
            )
            
            response = self.tts_client.synthesize_speech(
                input=input_text, voice=voice, audio_config=audio_config
            )
            
            return response.audio_content
            
        except Exception as e:
            logger.error(f"❌ Google TTS failed: {e}")
            return None
    
    def synthesize_stream(self, text: str, **kwargs) -> Generator[bytes, None, None]:
        """Stream TTS - Google doesn't support streaming, so we buffer"""
        audio = self.synthesize(text, **kwargs)
        if audio:
            # Yield in chunks for streaming compatibility
            chunk_size = 4096
            for i in range(0, len(audio), chunk_size):
                yield audio[i:i+chunk_size]
    
    def get_info(self) -> Dict[str, Any]:
        """Get Google service info"""
        return {
            **super().get_info(),
            "languages_supported": 100,
            "voices_available": 200,
            "pricing": "Per 1M characters",
            "documentation": "https://cloud.google.com/speech-to-text"
        }


def get_google_speech_service() -> GoogleSpeechService:
    """Get or create Google speech service instance"""
    global _service
    if '_service' not in globals() or _service is None:
        _service = GoogleSpeechService()
    return _service
```

---

## Step 4: Create Service Registry

Create `convonet/speech_services_manager.py`:

```python
"""Service registry and manager for speech services"""

from typing import Dict, Optional, Literal
from speech_services.base import SpeechService
from speech_services.cartesia_service import get_cartesia_service
from speech_services.deepgram_service import get_deepgram_service
from speech_services.elevenlabs_service import get_elevenlabs_service
from speech_services.google_speech import get_google_speech_service

class SpeechServiceProvider:
    """Registry for all available speech services"""
    
    STT_PROVIDERS = {
        "cartesia": ("cartesia_service", get_cartesia_service),
        "deepgram": ("deepgram_service", get_deepgram_service),
        "google": ("google_speech", get_google_speech_service),
        "azure": ("azure_speech", lambda: None),  # TODO: Implement
        "assemblyai": ("assemblyai_service", lambda: None),  # TODO: Implement
    }
    
    TTS_PROVIDERS = {
        "cartesia": ("cartesia_service", get_cartesia_service),
        "elevenlabs": ("elevenlabs_service", get_elevenlabs_service),
        "google": ("google_speech", get_google_speech_service),
        "azure": ("azure_speech", lambda: None),  # TODO: Implement
    }
    
    @classmethod
    def get_stt_service(cls, provider: str) -> Optional[SpeechService]:
        """Get STT service by name"""
        if provider not in cls.STT_PROVIDERS:
            return None
        
        module_name, getter = cls.STT_PROVIDERS[provider]
        service = getter()
        
        if service and service.is_available():
            return service
        return None
    
    @classmethod
    def get_tts_service(cls, provider: str) -> Optional[SpeechService]:
        """Get TTS service by name"""
        if provider not in cls.TTS_PROVIDERS:
            return None
        
        module_name, getter = cls.TTS_PROVIDERS[provider]
        service = getter()
        
        if service and service.is_available():
            return service
        return None
    
    @classmethod
    def list_available_stt(cls) -> Dict[str, Dict]:
        """List all available STT services"""
        available = {}
        for provider in cls.STT_PROVIDERS.keys():
            service = cls.get_stt_service(provider)
            if service:
                available[provider] = service.get_info()
        return available
    
    @classmethod
    def list_available_tts(cls) -> Dict[str, Dict]:
        """List all available TTS services"""
        available = {}
        for provider in cls.TTS_PROVIDERS.keys():
            service = cls.get_tts_service(provider)
            if service:
                available[provider] = service.get_info()
        return available


# Example usage in routes
from flask import Blueprint, jsonify
speech_bp = Blueprint('speech', __name__)

@speech_bp.route('/api/speech-services/stt')
def get_stt_services():
    """List available STT services"""
    return jsonify(SpeechServiceProvider.list_available_stt())

@speech_bp.route('/api/speech-services/tts')
def get_tts_services():
    """List available TTS services"""
    return jsonify(SpeechServiceProvider.list_available_tts())
```

---

## Step 5: Integration with WebRTC Server

Update `webrtc_voice_server_socketio.py`:

```python
from speech_services_manager import SpeechServiceProvider

@socketio.on('start_recording', namespace='/voice')
def handle_start_recording(data=None):
    session_id = request.sid
    stt_provider = data.get('stt_provider', 'deepgram')  # User can choose
    
    # Get available STT provider
    stt_service = SpeechServiceProvider.get_stt_service(stt_provider)
    
    if not stt_service:
        emit('error', {'msg': f'STT provider {stt_provider} not available'})
        return
    
    # Get streaming session
    if hasattr(stt_service, 'get_streaming_stt_session'):
        def on_final(text):
            emit('transcription', {'text': text})
        
        session = stt_service.get_streaming_stt_session(
            session_id=session_id,
            on_final=on_final
        )
        
        if session and session.start():
            streaming_sessions[session_id] = (session, stt_provider)
```

---

## Step 6: Quick Checklist for New Service

- [ ] **Create** `speech_services/{provider}_service.py`
- [ ] **Implement** SpeechService abstract class
- [ ] **Handle** API authentication (env vars, credentials)
- [ ] **Implement** STT batch method (`transcribe_audio_buffer`)
- [ ] **Implement** STT streaming class (`get_streaming_stt_session`)
- [ ] **Implement** TTS batch method (`synthesize`)
- [ ] **Implement** TTS streaming method (`synthesize_stream`)
- [ ] **Add tests** - create `test_{provider}_service.py`
- [ ] **Update** SpeechServiceProvider registry
- [ ] **Update** documentation with audio format specs
- [ ] **Update** environment variable docs
- [ ] **Add error handling** for API failures
- [ ] **Add logging** with consistent format
- [ ] **Test** with real data end-to-end

---

## Validation Checklist

```python
# Test checklist for new service
def test_new_service():
    service = get_new_service()
    
    # 1. Availability
    assert service.is_available(), "Service not available"
    
    # 2. STT Batch
    text = service.transcribe_audio_buffer(test_audio)
    assert text is not None, "STT batch failed"
    
    # 3. STT Streaming
    session = service.get_streaming_stt_session("test", on_final=lambda x: None)
    assert session.start(), "STT streaming failed"
    session.send_audio_chunk(test_audio[:1024])
    session.stop()
    
    # 4. TTS Batch
    audio = service.synthesize("Hello world")
    assert audio is not None, "TTS batch failed"
    
    # 5. TTS Streaming
    chunks = list(service.synthesize_stream("Hello world"))
    assert len(chunks) > 0, "TTS streaming failed"
    
    print("✅ All tests passed")
```

---

## Audio Format Standards

| Format | Sample Rate | Encoding | Channels |
|--------|-------------|----------|----------|
| **WebRTC (Browser)** | 48kHz | PCM 16-bit LE | Mono |
| **Cartesia** | 16kHz or 48kHz | PCM 16-bit LE | Mono |
| **Deepgram** | Any | Auto-detect | Mono |
| **Google** | 16kHz | LINEAR16 | Mono |
| **Azure** | 16kHz | PCM 16-bit LE | Mono |

**Recommendation:** Implement resampling to 16kHz for compatibility across services.

---

## References

- Base Service Class: `speech_services/base.py`
- Existing Implementation: `speech_services/deepgram_service.py`
- Registry: `speech_services_manager.py`
- Integration: `webrtc_voice_server_socketio.py`
