# Cartesia STT + Redis Streaming Integration Guide

## Overview

This guide explains how to refactor Cartesia STT to use real-time streaming with Redis buffering, replacing the slow Batch API approach.

## Architecture Comparison

### ❌ Old Approach (Batch API - SLOW)
```
WebRTC Audio Chunks → Buffer in Memory → Cartesia Batch API (5-10s latency)
```

**Problems:**
- High latency (5-10 seconds)
- No real-time transcription
- Not suitable for live conversations
- Single point of buffering failure

---

### ✅ New Approach (Streaming WebSocket + Redis)
```
WebRTC Chunks → Redis Queue → Cartesia WebSocket (STT) → Stream Results
```

**Benefits:**
- Low latency (<500ms)
- Real-time partial/final transcriptions
- Distributed architecture (Redis)
- Handles backpressure gracefully
- Horizontal scaling support

---

## Files Created/Modified

| File | Purpose |
|------|---------|
| `cartesia_streaming_stt.py` | New WebSocket streaming STT class (like Deepgram's StreamingSTTSession) |
| `cartesia_service.py` | Updated with streaming integration + batch fallback |

---

## Implementation Steps

### 1. Prepare Audio Format

Cartesia requires **PCM 16-bit LE** audio at **16kHz or 48kHz**.

WebRTC typically sends 48kHz mono. You need resampling:

```python
# In webrtc_voice_server_socketio.py or audio handler
import librosa

def resample_audio_if_needed(audio_bytes: bytes, original_rate: int = 48000, target_rate: int = 16000) -> bytes:
    """Resample audio if needed for Cartesia"""
    if original_rate == target_rate:
        return audio_bytes
    
    import numpy as np
    
    # Convert bytes to numpy array
    audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
    
    # Resample using librosa
    audio_float = audio_int16.astype(np.float32) / 32768.0
    resampled = librosa.resample(audio_float, orig_sr=original_rate, target_sr=target_rate)
    
    # Convert back to int16
    resampled_int16 = (resampled * 32767).astype(np.int16)
    
    return resampled_int16.tobytes()
```

Or install resampler:
```bash
pip install librosa soundfile
```

---

### 2. Update WebRTC Voice Server

In `webrtc_voice_server_socketio.py`:

```python
# At top
from cartesia_service import get_cartesia_service
from cartesia_streaming_stt import get_cartesia_streaming_session

# Global storage for streaming sessions
streaming_sessions = {}

# In handle_start_recording():
@socketio.on('start_recording', namespace='/voice')
def handle_start_recording(data=None):
    session_id = request.sid
    stt_provider = get_stt_provider()  # Your existing logic
    
    # If using Cartesia, start streaming STT
    if stt_provider == "cartesia" and STREAMING_STT_ENABLED:
        try:
            cartesia_service = get_cartesia_service()
            
            def on_final_transcript(final_text: str):
                """Called when Cartesia sends final transcription"""
                logger.info(f"✅ Cartesia final: {final_text}")
                
                socketio.emit('transcription', {
                    'success': True,
                    'text': final_text,
                    'provider': 'cartesia',
                    'is_final': True
                }, to=session_id, namespace='/voice')
            
            def on_partial_transcript(partial_text: str):
                """Optional: Called for interim results"""
                socketio.emit('transcription', {
                    'text': partial_text,
                    'is_interim': True
                }, to=session_id, namespace='/voice')
            
            # Get streaming session
            streaming_session = cartesia_service.get_streaming_stt_session(
                session_id=session_id,
                on_final=on_final_transcript
            )
            
            if streaming_session and streaming_session.start():
                streaming_sessions[session_id] = streaming_session
                logger.info(f"🎤 Cartesia Streaming STT started: {session_id}")
            else:
                logger.error("Failed to start Cartesia Streaming STT")
                emit('error', {'msg': 'Microphone setup failed'})
                
        except Exception as e:
            logger.error(f"Cartesia streaming setup error: {e}")
            emit('error', {'msg': str(e)})

# In handle_audio_chunk() or WebRTC audio handler:
@socketio.on('audio_chunk', namespace='/voice')
def handle_audio_chunk(data):
    session_id = request.sid
    
    if session_id not in streaming_sessions:
        return
    
    try:
        # Decode audio from client
        audio_bytes = base64.b64decode(data['audio'])
        
        # Resample if needed (WebRTC→Cartesia format)
        audio_bytes = resample_audio_if_needed(audio_bytes, 48000, 16000)
        
        # Send to streaming STT
        streaming_session = streaming_sessions[session_id]
        streaming_session.send_audio_chunk(audio_bytes)
        
    except Exception as e:
        logger.error(f"Audio chunk error: {e}")

# In handle_stop_recording():
@socketio.on('stop_recording', namespace='/voice')
def handle_stop_recording(data=None):
    session_id = request.sid
    
    if session_id in streaming_sessions:
        streaming_session = streaming_sessions[session_id]
        streaming_session.stop()
        del streaming_sessions[session_id]
        logger.info(f"🛑 Cartesia STT stopped: {session_id}")
```

---

### 3. Redis Buffering (Optional)

Redis helps with distributed audio buffering. If Redis is available, Cartesia streaming STT will automatically store audio chunks:

```bash
# Check Redis status
redis-cli ping

# Monitor Cartesia keys
redis-cli KEYS "cartesia:*"
```

---

### 4. Configuration

Set environment variables:

```bash
# Required
export CARTESIA_API_KEY="your_api_key_here"

# For Redis (optional, defaults to localhost:6379)
export REDIS_HOST="localhost"
export REDIS_PORT=6379
export REDIS_PASSWORD=""  # If needed
```

---

### 5. Feature Comparison: Cartesia vs Deepgram

| Feature | Cartesia | Deepgram |
|---------|----------|----------|
| **WebSocket Streaming** | ✅ SDK available | ✅ SDK available |
| **Batch API** | ✅ Slow but works | ✅ Slow but works |
| **Real-time Speed** | <500ms | <500ms |
| **Redis Integration** | ✅ Optional | ✅ Optional |
| **Language Support** | 100+ | 30+ |
| **Pricing** | 1 credit/2s | $0.0043/min |
| **Production Ready** | ✅ Yes | ✅ Yes (more popular) |

---

## Troubleshooting

### WebSocket Connection Fails

**Error:** `Connection refused: wss://api.cartesia.ai/stt/websocket`

**Solution:**
- Verify CARTESIA_API_KEY is set
- Check network connectivity
- Use Batch API fallback temporarily

### Audio Quality Issues

**Problem:** Transcriptions are garbled/gibberish

**Solutions:**
1. Verify audio format: PCM 16-bit LE
2. Verify sample rate: 16kHz (check resampling works)
3. Check RMS volume (not too quiet)
4. Test with `test_cartesia_audio.py` first

### Latency Issues

**Problem:** Transcriptions come multiple seconds late

**This may be normal:**
- Cartesia does some buffering for context
- Network latency adds 100-200ms
- If >2s, check network bandwidth

---

## Testing

### 1. Test Cartesia Connection

```python
from convonet.cartesia_service import get_cartesia_service

service = get_cartesia_service()
print(f"Available: {service.is_available()}")

# Test TTS
for chunk in service.synthesize_stream("Hello world"):
    print(f"TTS chunk: {len(chunk)} bytes")

# Test Batch STT (slow)
import base64
audio_bytes = open('test_audio.wav', 'rb').read()
text = service.transcribe_audio_buffer(audio_bytes)
print(f"Transcribed: {text}")
```

### 2. Test Streaming STT

```python
import asyncio
from convonet.cartesia_service import get_cartesia_service

async def test_streaming():
    service = get_cartesia_service()
    
    results = []
    
    def on_final(text):
        results.append(text)
        print(f"Final: {text}")
    
    session = service.get_streaming_stt_session(
        session_id="test_session",
        on_final=on_final
    )
    
    if session.start():
        # Read audio file in chunks
        with open('test_audio.wav', 'rb') as f:
            while True:
                chunk = f.read(1024)
                if not chunk:
                    break
                session.send_audio_chunk(chunk)
                await asyncio.sleep(0.05)
        
        session.stop()
        print(f"Results: {results}")

asyncio.run(test_streaming())
```

---

## Migration Checklist

- [ ] Install dependencies: `pip install websockets librosa`
- [ ] Set `CARTESIA_API_KEY` environment variable
- [ ] Update `webrtc_voice_server_socketio.py` with streaming code
- [ ] Add audio resampling logic (48kHz → 16kHz)
- [ ] Test with `handle_start_recording()` first
- [ ] Test with actual WebRTC audio chunks
- [ ] Monitor Redis keys (optional): `redis-cli KEYS "cartesia:*"`
- [ ] Enable metrics/logging for latency tracking
- [ ] Deploy to production with monitoring

---

## Next Steps

1. **Choose between Cartesia and Deepgram** - Deepgram is more mature/battle-tested
2. **Implement audio resampling** - Critical for Cartesia to work well
3. **Test thoroughly** with real microphone audio
4. **Monitor transcription quality** and latency in production
5. **Set up fallback** - Batch API for when streaming fails
6. **Consider caching** - Store transcriptions in Redis for audit trails

---

## References

- [Cartesia API Docs](https://docs.cartesia.ai)
- [Deepgram Integration Reference](./cartesia_service.py#L220)
- [Redis Manager](./redis_manager.py)
- [WebRTC Voice Server](./webrtc_voice_server_socketio.py#L2410)
