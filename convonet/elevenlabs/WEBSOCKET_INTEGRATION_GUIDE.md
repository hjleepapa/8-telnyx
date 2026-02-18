# ElevenLabs WebSocket Streaming Integration Guide

## Overview

This guide covers integrating ElevenLabs **WebSocket Streaming** for both **STT (Speech-to-Text)** and **TTS (Text-to-Speech)** into your WebRTC voice server for real-time conversational AI.

## Key Improvements Over REST API

| Feature | REST API (Current) | WebSocket Streaming |
|---------|---|---|
| **Latency** | 1-3 seconds | 200-500ms (STT), 400-600ms (TTS) |
| **Real-time** | ❌ Batch processing | ✅ True streaming |
| **Partial Results** | ❌ Full transcripts only | ✅ Partial + committed |
| **Audio Chunks** | Full audio response | ✅ Streamed chunks |
| **Multiple Streams** | Separate connections | ✅ Multi-context on 1 WebSocket |
| **WebRTC Ready** | ⚠️ Requires conversion | ✅ Native 48kHz support |

---

## Part 1: STT WebSocket Implementation

### Basic Usage

```python
import asyncio
from convonet.elevenlabs import (
    create_streaming_stt_session,
    get_streaming_stt_session,
    remove_streaming_stt_session,
    CommitStrategy
)

# Create a session
async def setup_stt_session():
    session = await create_streaming_stt_session(
        session_id="user_123_stt",
        language_code="en",
        commit_strategy=CommitStrategy.VAD,  # Auto-commit on silence
        vad_silence_threshold_secs=1.5,  # 1.5s silence = commit
        on_partial=lambda text: print(f"📝 Partial: {text}"),
        on_commit=lambda text, meta: print(f"✅ Committed: {text}"),
        on_error=lambda err: print(f"❌ Error: {err}"),
    )
    return session

# Send audio chunks (16kHz PCM 16-bit)
async def send_audio_chunk():
    session = get_streaming_stt_session("user_123_stt")
    if session:
        # audio_bytes should be 16kHz PCM 16-bit
        await session.send_audio(audio_bytes, is_final_chunk=False)

# Close session
async def cleanup_stt():
    await remove_streaming_stt_session("user_123_stt")
```

### WebRTC Integration Points

Add to `webrtc_voice_server_socketio.py`:

```python
from convonet.elevenlabs import (
    create_streaming_stt_session,
    get_streaming_stt_session,
    remove_streaming_stt_session,
)

@socketio.on('webrtc:start_recording')
async def handle_start_recording(data):
    user_id = data.get('user_id')
    session_id = f"{user_id}_elevenlabs_stt"
    
    try:
        # Create ElevenLabs STT session
        stt_session = await create_streaming_stt_session(
            session_id=session_id,
            language_code="en",
            on_partial=lambda text: emit_transcript_update(text, is_partial=True),
            on_commit=lambda text, meta: emit_transcript_final(text, meta),
        )
        
        # Store in Redis for this session
        redis_manager.set_session_data(
            user_id, 
            'elevenlabs_stt_session', 
            session_id
        )
        
        emit('recording_started', {'provider': 'elevenlabs', 'session': session_id})
    
    except Exception as e:
        logger.error(f"Failed to start STT: {e}")
        emit('recording_error', {'error': str(e)})

@socketio.on('webrtc:audio_data')
async def handle_audio_data(data):
    user_id = data.get('user_id')
    audio_chunk = data.get('audio')  # Base64 or bytes
    
    # Get the appropriate STT session
    stt_session = get_streaming_stt_session(f"{user_id}_elevenlabs_stt")
    
    if stt_session and stt_session.is_connected:
        try:
            # Decode if base64
            if isinstance(audio_chunk, str):
                audio_bytes = base64.b64decode(audio_chunk)
            else:
                audio_bytes = audio_chunk
            
            # Resample to 16kHz if needed
            audio_16k = resample_audio(audio_bytes, 48000, 16000)
            
            # Send to STT
            await stt_session.send_audio(audio_16k)
        
        except Exception as e:
            logger.error(f"Error processing audio: {e}")

@socketio.on('webrtc:stop_recording')
async def handle_stop_recording(data):
    user_id = data.get('user_id')
    session_id = f"{user_id}_elevenlabs_stt"
    
    try:
        # Final commit
        stt_session = get_streaming_stt_session(session_id)
        if stt_session:
            await stt_session.commit_transcript()
            await remove_streaming_stt_session(session_id)
        
        emit('recording_stopped')
    
    except Exception as e:
        logger.error(f"Error stopping recording: {e}")
```

---

## Part 2: TTS WebSocket Implementation (Multi-Context)

### Basic Usage

```python
import asyncio
from convonet.elevenlabs import (
    create_streaming_tts_session,
    get_streaming_tts_session,
    remove_streaming_tts_session,
    TextToSpeechOutputFormat,
    VoiceSettings,
)

# Create a session
async def setup_tts_session():
    session = await create_streaming_tts_session(
        session_id="user_123_tts",
        default_voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel
        output_format=TextToSpeechOutputFormat.PCM_48000,  # WebRTC native
        on_audio_chunk=lambda audio: handle_audio_chunk(audio),
    )
    
    # Initialize default context
    context_id = await session.initialize_context(
        text=" ",  # Start with space
        voice_id="21m00Tcm4TlvDq8ikWAM",
        voice_settings=VoiceSettings(
            stability=0.5,
            similarity_boost=0.75,
            use_speaker_boost=True,
            speed=1.0,
        ),
        on_audio_chunk=lambda audio: emit_audio(audio),
        on_final=lambda: emit_final(),
    )
    
    return session, context_id

# Send text as LLM streams response
async def stream_tts_response(text: str):
    session = get_streaming_tts_session("user_123_tts")
    if session:
        # Send text chunks as they arrive from LLM
        # The WebSocket will buffer and optimize
        await session.send_text(text)

# Flush to get all remaining audio
async def flush_tts():
    session = get_streaming_tts_session("user_123_tts")
    if session:
        await session.flush_context()

# Close session
async def cleanup_tts():
    await remove_streaming_tts_session("user_123_tts")
```

### Multi-Context Usage (Concurrent Streams)

Perfect for agent talking + user listening:

```python
async def setup_multicontext_tts():
    session = await create_streaming_tts_session(
        session_id="user_123_tts",
        default_voice_id="21m00Tcm4TlvDq8ikWAM",
    )
    
    # Context 1: Agent response (main voice)
    agent_context = await session.initialize_context(
        context_id="agent_response",
        voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel
        on_audio_chunk=lambda audio: emit('agent_audio', {'audio': audio}),
        on_final=lambda: logger.info("Agent audio complete"),
    )
    
    # Context 2: System notifications (different voice)
    system_context = await session.initialize_context(
        context_id="system_notification",
        voice_id="nPczCjzI2devNBz1zQrb",  # Different voice
        voice_settings=VoiceSettings(
            stability=0.7,
            similarity_boost=0.8,
        ),
        on_audio_chunk=lambda audio: emit('system_audio', {'audio': audio}),
    )
    
    # Send text to specific contexts
    await session.send_text("Agent is speaking here... ", context_id="agent_response")
    await session.send_text("System notification. ", context_id="system_notification")
    
    # Flush both
    await session.flush_context("agent_response")
    await session.flush_context("system_notification")
```

### WebRTC Integration Points

Add to `webrtc_voice_server_socketio.py`:

```python
from convonet.elevenlabs import (
    create_streaming_tts_session,
    get_streaming_tts_session,
    remove_streaming_tts_session,
    TextToSpeechOutputFormat,
    VoiceSettings,
)

# TTS session setup
@socketio.on('webrtc:init_tts')
async def handle_init_tts(data):
    user_id = data.get('user_id')
    session_id = f"{user_id}_elevenlabs_tts"
    voice_id = data.get('voice_id', "21m00Tcm4TlvDq8ikWAM")
    
    try:
        # Create streaming TTS session
        tts_session = await create_streaming_tts_session(
            session_id=session_id,
            default_voice_id=voice_id,
            output_format=TextToSpeechOutputFormat.PCM_48000,  # WebRTC native
        )
        
        # Initialize default context
        context_id = await tts_session.initialize_context(
            text=" ",
            voice_id=voice_id,
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                use_speaker_boost=True,
            ),
            on_audio_chunk=lambda audio: emit_audio_to_webrtc(audio),
            on_final=lambda: emit_tts_complete(),
        )
        
        # Store session info
        redis_manager.set_session_data(
            user_id,
            'elevenlabs_tts_session',
            {'session_id': session_id, 'context_id': context_id}
        )
        
        emit('tts_ready', {'session': session_id, 'context': context_id})
    
    except Exception as e:
        logger.error(f"Failed to init TTS: {e}")
        emit('tts_error', {'error': str(e)})

# Stream LLM response to TTS
def stream_agent_response_to_tts(user_id: str, response_text: str):
    """Call this as LLM streams response"""
    session = get_streaming_tts_session(f"{user_id}_elevenlabs_tts")
    context_data = redis_manager.get_session_data(user_id, 'elevenlabs_tts_session')
    
    if session and context_data:
        context_id = context_data.get('context_id', 'default')
        
        # Stream text in chunks (e.g., sentence by sentence from LLM)
        try:
            asyncio.create_task(session.send_text(response_text + " ", context_id=context_id))
        except Exception as e:
            logger.error(f"TTS streaming error: {e}")

# Complete and flush TTS
@socketio.on('webrtc:flush_tts')
async def handle_flush_tts(data):
    user_id = data.get('user_id')
    session_id = f"{user_id}_elevenlabs_tts"
    context_data = redis_manager.get_session_data(user_id, 'elevenlabs_tts_session')
    
    tts_session = get_streaming_tts_session(session_id)
    if tts_session and context_data:
        try:
            context_id = context_data.get('context_id', 'default')
            await tts_session.flush_context(context_id)
            emit('tts_flushed')
        except Exception as e:
            logger.error(f"Flush error: {e}")

# Cleanup
@socketio.on('webrtc:close')
async def handle_close(data):
    user_id = data.get('user_id')
    
    try:
        await remove_streaming_stt_session(f"{user_id}_elevenlabs_stt")
        await remove_streaming_tts_session(f"{user_id}_elevenlabs_tts")
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
```

---

## Part 3: Audio Format Handling

### PCM Resampling (48kHz WebRTC → 16kHz STT)

```python
import librosa
import numpy as np

def resample_audio(audio_bytes: bytes, from_sr: int, to_sr: int) -> bytes:
    """Resample PCM audio"""
    # Convert bytes to numpy array
    audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
    
    # Resample
    resampled = librosa.resample(
        audio_array.astype(np.float32),
        orig_sr=from_sr,
        target_sr=to_sr
    )
    
    # Convert back to int16
    resampled_bytes = (resampled * 32767).astype(np.int16).tobytes()
    return resampled_bytes
```

### Audio Format Support

**STT (Realtime):**
- Input: PCM 16-bit, 16kHz (fixed)
- Auto-detects: Multiple languages

**TTS (Multi-Context):**
- PCM: 8K, 16K, 22.05K, 24K, 44.1K, 48K ⭐ (WebRTC native)
- Opus: 48K (compressed, WebRTC-compatible)
- MP3: 44.1K
- µ-law / A-law: 8K

---

## Part 4: Error Handling & Reconnection

```python
import asyncio
from typing import Optional

class ElevenLabsSTTManager:
    def __init__(self, user_id: str):
        self.user_id = user_id
        self.session = None
        self.retry_count = 0
        self.max_retries = 3
    
    async def connect_with_retry(self):
        """Connect with exponential backoff"""
        while self.retry_count < self.max_retries:
            try:
                self.session = await create_streaming_stt_session(
                    session_id=f"{self.user_id}_stt",
                    on_error=self.handle_error,
                )
                self.retry_count = 0  # Reset on success
                return self.session
            
            except Exception as e:
                self.retry_count += 1
                wait_time = 2 ** self.retry_count  # Exponential backoff
                logger.warning(f"Retry {self.retry_count}/{self.max_retries} in {wait_time}s")
                await asyncio.sleep(wait_time)
        
        raise RuntimeError(f"Failed to connect after {self.max_retries} retries")
    
    def handle_error(self, error: str):
        """Handle errors with user notification"""
        logger.error(f"STT Error: {error}")
        # Notify user via WebSocket
        emit('stt_error', {'error': error, 'retry_allowed': self.retry_count < self.max_retries})
```

---

## Part 5: Performance Tuning

### STT Configuration

```python
# For fast response (aggressive VAD)
fast_config = {
    'vad_silence_threshold_secs': 0.8,      # Fast silence detect
    'vad_threshold': 0.6,                    # Aggressive voice detect
    'min_silence_duration_ms': 50,           # Quick silence
    'commit_strategy': CommitStrategy.VAD,
}

# For accuracy (conservative VAD)
accurate_config = {
    'vad_silence_threshold_secs': 2.0,      # Wait for real silence
    'vad_threshold': 0.3,                    # More generous voice detect
    'min_silence_duration_ms': 200,          # Clear silence break
    'commit_strategy': CommitStrategy.VAD,
}
```

### TTS Configuration

```python
# For speed (lower quality, faster latency)
speed_config = {
    'chunk_length_schedule': [50, 100, 150, 200],  # Generate frequently
    'auto_mode': True,
}

# For quality (higher latency, better audio)
quality_config = {
    'chunk_length_schedule': [120, 160, 250, 290],  # Default ElevenLabs
    'auto_mode': False,
}
```

---

## Part 6: Fallback Strategy

```python
async def get_tts_audio_with_fallback(text: str, user_id: str) -> bytes:
    """Try streaming TTS, fallback to REST API"""
    
    # Try WebSocket first
    try:
        session = get_streaming_tts_session(f"{user_id}_tts")
        if session and session.is_connected:
            await session.send_text(text)
            return await asyncio.wait_for(get_audio_chunk(), timeout=5.0)
    except asyncio.TimeoutError:
        logger.warning("TTS WebSocket timeout, trying REST API")
    except Exception as e:
        logger.error(f"WebSocket TTS failed: {e}")
    
    # Fallback to REST API
    try:
        elevenlabs = get_elevenlabs_service()
        audio_bytes = elevenlabs.synthesize(text)
        return audio_bytes
    except Exception as e:
        logger.error(f"REST API TTS failed: {e}")
        return None
```

---

## Part 7: Deployment Checklist

- [ ] Install `websockets` dependency: `pip install websockets`
- [ ] Set `ELEVENLABS_API_KEY` environment variable
- [ ] Test STT session creation and audio streaming
- [ ] Test TTS session creation and text streaming
- [ ] Test audio format conversion (resampling)
- [ ] Test multi-context concurrent operations
- [ ] Test error handling and reconnection
- [ ] Load test with multiple concurrent sessions
- [ ] Monitor WebSocket connection stability
- [ ] Set up fallback to REST API on errors
- [ ] Test with actual WebRTC voice server

---

## Performance Benchmarks

**Expected Latency:**
- STT: 200-500ms from audio end to transcription ✅
- TTS First Chunk: 400-600ms ✅
- Full Response: +100-200ms per chunk

**Concurrent Capacity:**
- STT Sessions: 100+ concurrent per application
- TTS Sessions: 50+ concurrent per voice
- Multi-context: 5-10 contexts per TTS session

---

## References

- ElevenLabs Realtime STT: https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime
- ElevenLabs Multi-Context TTS: https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-multi-stream-input
- WebSocket API Guide: https://elevenlabs.io/docs/developers/guides/websockets
