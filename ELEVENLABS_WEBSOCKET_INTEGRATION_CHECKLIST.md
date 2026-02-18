# ElevenLabs WebSocket Integration Checklist

## Dependencies

```bash
# Install if not already present
pip install websockets
```

Ensure `ELEVENLABS_API_KEY` environment variable is set.

---

## Import Statements to Add

Add these to the top of `webrtc_voice_server_socketio.py`:

```python
from convonet.elevenlabs import (
    # WebSocket Streaming STT
    create_streaming_stt_session,
    get_streaming_stt_session,
    remove_streaming_stt_session,
    CommitStrategy,
    # WebSocket Streaming TTS (Multi-Context)
    create_streaming_tts_session,
    get_streaming_tts_session,
    remove_streaming_tts_session,
    TextToSpeechOutputFormat,
    VoiceSettings,
)
```

---

## Code Snippets to Add

### 1. Event Handler: Initialize STT (WebSocket)

Add to your `@socketio.on()` handlers:

```python
@socketio.on('webrtc:start_recording')
async def handle_start_recording_elevenlabs(data):
    """Start ElevenLabs Realtime STT WebSocket session"""
    try:
        user_id = data.get('user_id')
        session_id = f"{user_id}_elevenlabs_stt"
        
        # Create streaming STT session
        stt_session = await create_streaming_stt_session(
            session_id=session_id,
            language_code=data.get('language', 'en'),
            commit_strategy=CommitStrategy.VAD,  # Auto-commit on silence
            vad_silence_threshold_secs=1.5,
            vad_threshold=0.4,
            on_partial=handle_stt_partial,
            on_commit=handle_stt_committed,
            on_error=handle_stt_error,
            include_timestamps=True,
            include_language_detection=True,
        )
        
        # Store session reference for this user
        redis_manager.set_session_data(
            user_id,
            'elevenlabs_stt_session_id',
            session_id
        )
        
        emit('recording_started', {
            'provider': 'elevenlabs',
            'stt_session': session_id,
            'mode': 'streaming',
            'latency': 'sub-500ms'
        })
        
        logger.info(f"✅ ElevenLabs STT session started: {session_id}")
    
    except Exception as e:
        logger.error(f"Failed to start ElevenLabs STT: {e}")
        emit('recording_error', {'error': str(e)})


def handle_stt_partial(text: str):
    """Handle partial STT transcript"""
    # Emit to client for live preview
    emit('transcript_partial', {'text': text})


def handle_stt_committed(text: str, metadata: dict):
    """Handle committed STT transcript"""
    # Store or process final transcription
    words = metadata.get('words', [])
    logger.info(f"STT Final: {text} ({len(words)} words)")
    emit('transcript_final', {
        'text': text,
        'confidence': 'high',
        'word_count': len(words)
    })


def handle_stt_error(error: str):
    """Handle STT errors"""
    logger.error(f"STT Error: {error}")
    emit('stt_error', {'error': error})
```

---

### 2. Event Handler: Audio Processing (WebSocket STT)

```python
@socketio.on('webrtc:audio_data')
async def handle_audio_data_elevenlabs(data):
    """Process audio chunk through ElevenLabs STT"""
    try:
        user_id = data.get('user_id')
        session_id = redis_manager.get_session_data(
            user_id, 
            'elevenlabs_stt_session_id'
        )
        
        if not session_id:
            logger.warning("No active STT session")
            return
        
        stt_session = get_streaming_stt_session(session_id)
        if not stt_session or not stt_session.is_connected:
            logger.warning("STT not connected")
            return
        
        # Decode audio
        audio_chunk = data.get('audio')
        if isinstance(audio_chunk, str):
            audio_bytes = base64.b64decode(audio_chunk)
        else:
            audio_bytes = audio_chunk
        
        # WebRTC is 48kHz, ElevenLabs STT needs 16kHz
        if data.get('sample_rate', 48000) != 16000:
            audio_bytes = resample_audio(audio_bytes, 48000, 16000)
        
        # Send to STT
        await stt_session.send_audio(
            audio_bytes,
            is_final_chunk=data.get('is_final', False)
        )
    
    except Exception as e:
        logger.error(f"Audio processing error: {e}")


def resample_audio(audio_bytes: bytes, from_sr: int, to_sr: int) -> bytes:
    """Resample PCM audio"""
    try:
        import librosa
        import numpy as np
        
        # Convert to numpy array
        audio_array = np.frombuffer(audio_bytes, dtype=np.int16)
        
        # Resample
        resampled = librosa.resample(
            audio_array.astype(np.float32),
            orig_sr=from_sr,
            target_sr=to_sr
        )
        
        # Convert back
        return (resampled * 32767).astype(np.int16).tobytes()
    
    except ImportError:
        logger.warning("librosa not available, skipping resampling")
        return audio_bytes
```

---

### 3. Event Handler: Stop Recording (STT Cleanup)

```python
@socketio.on('webrtc:stop_recording')
async def handle_stop_recording_elevenlabs(data):
    """Stop ElevenLabs STT session"""
    try:
        user_id = data.get('user_id')
        session_id = redis_manager.get_session_data(
            user_id,
            'elevenlabs_stt_session_id'
        )
        
        if session_id:
            stt_session = get_streaming_stt_session(session_id)
            if stt_session:
                # Final commit
                await stt_session.commit_transcript()
                # Close session
                await remove_streaming_stt_session(session_id)
            
            # Clear from Redis
            redis_manager.delete_session_data(
                user_id,
                'elevenlabs_stt_session_id'
            )
        
        emit('recording_stopped')
        logger.info(f"STT session stopped: {session_id}")
    
    except Exception as e:
        logger.error(f"Error stopping STT: {e}")
```

---

### 4. Event Handler: Initialize TTS (WebSocket)

```python
@socketio.on('webrtc:init_tts')
async def handle_init_tts_elevenlabs(data):
    """Initialize ElevenLabs Multi-Context TTS WebSocket session"""
    try:
        user_id = data.get('user_id')
        session_id = f"{user_id}_elevenlabs_tts"
        voice_id = data.get('voice_id', '21m00Tcm4TlvDq8ikWAM')  # Rachel
        
        # Create TTS session
        tts_session = await create_streaming_tts_session(
            session_id=session_id,
            default_voice_id=voice_id,
            output_format=TextToSpeechOutputFormat.PCM_48000,  # WebRTC native
            model_id='eleven_multilingual_v2',
        )
        
        # Initialize default context for agent response
        context_id = await tts_session.initialize_context(
            text=' ',
            voice_id=voice_id,
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                use_speaker_boost=True,
                speed=1.0,
            ),
            on_audio_chunk=lambda audio: handle_tts_audio_chunk(user_id, audio),
            on_final=lambda: emit('tts_generation_complete'),
        )
        
        # Store session info
        redis_manager.set_session_data(user_id, 'elevenlabs_tts_session_id', session_id)
        redis_manager.set_session_data(user_id, 'elevenlabs_tts_context_id', context_id)
        
        emit('tts_ready', {
            'provider': 'elevenlabs',
            'session': session_id,
            'context': context_id,
            'output_format': 'pcm_48000',
            'latency': '400-600ms'
        })
        
        logger.info(f"✅ ElevenLabs TTS session initialized: {session_id}")
    
    except Exception as e:
        logger.error(f"Failed to init TTS: {e}")
        emit('tts_error', {'error': str(e)})


def handle_tts_audio_chunk(user_id: str, audio_bytes: bytes):
    """Handle audio chunks from TTS"""
    # Send to WebRTC client
    emit('tts_audio_chunk', {
        'audio': base64.b64encode(audio_bytes).decode('utf-8'),
        'size': len(audio_bytes),
    })
```

---

### 5. Stream LLM Response to TTS

```python
async def stream_agent_response_to_tts(user_id: str, response_text: str):
    """
    Called from your agent response handler
    Streams LLM response through TTS as it arrives
    """
    session_id = redis_manager.get_session_data(user_id, 'elevenlabs_tts_session_id')
    context_id = redis_manager.get_session_data(user_id, 'elevenlabs_tts_context_id')
    
    if session_id and context_id:
        tts_session = get_streaming_tts_session(session_id)
        if tts_session and tts_session.is_connected:
            try:
                # Stream text (in production, this would be from LLM stream)
                await tts_session.send_text(
                    response_text + ' ',
                    context_id=context_id
                )
                logger.debug(f"TTS: Sent {len(response_text)} characters")
            except Exception as e:
                logger.error(f"TTS streaming error: {e}")
```

---

### 6. Flush and Finalize TTS

```python
@socketio.on('webrtc:flush_tts')
async def handle_flush_tts(data):
    """Flush TTS context to generate remaining audio"""
    try:
        user_id = data.get('user_id')
        session_id = redis_manager.get_session_data(user_id, 'elevenlabs_tts_session_id')
        context_id = redis_manager.get_session_data(user_id, 'elevenlabs_tts_context_id')
        
        if session_id and context_id:
            tts_session = get_streaming_tts_session(session_id)
            if tts_session:
                await tts_session.flush_context(context_id)
                logger.info(f"TTS flushed: {context_id}")
                emit('tts_flushed')
    
    except Exception as e:
        logger.error(f"Flush error: {e}")
```

---

### 7. Cleanup on Disconnect

```python
@socketio.on('webrtc:close')
async def handle_webrtc_close(data):
    """Clean up all ElevenLabs sessions on disconnect"""
    try:
        user_id = data.get('user_id')
        
        # Clean up STT
        stt_session_id = redis_manager.get_session_data(
            user_id,
            'elevenlabs_stt_session_id'
        )
        if stt_session_id:
            await remove_streaming_stt_session(stt_session_id)
            redis_manager.delete_session_data(user_id, 'elevenlabs_stt_session_id')
        
        # Clean up TTS
        tts_session_id = redis_manager.get_session_data(
            user_id,
            'elevenlabs_tts_session_id'
        )
        if tts_session_id:
            await remove_streaming_tts_session(tts_session_id)
            redis_manager.delete_session_data(user_id, 'elevenlabs_tts_session_id')
            redis_manager.delete_session_data(user_id, 'elevenlabs_tts_context_id')
        
        logger.info(f"ElevenLabs sessions cleaned up: {user_id}")
    
    except Exception as e:
        logger.error(f"Cleanup error: {e}")
```

---

## Configuration

### Environment Variables

```bash
# Required
export ELEVENLABS_API_KEY="sk_live_..."

# Optional
export ELEVENLABS_STT_LANGUAGE="en"  # Default
export ELEVENLABS_TTS_MODEL="eleven_multilingual_v2"  # Default
```

### Performance Tuning

For real-time conversational AI, use these settings:

```python
# STT: Fast+Accurate balance
STT_CONFIG = {
    'vad_silence_threshold_secs': 1.5,   # 1.5s silence = commit
    'vad_threshold': 0.4,                 # 40% confidence
    'min_speech_duration_ms': 100,        # Ignore < 100ms audio
    'min_silence_duration_ms': 100,       # Need 100ms silence
    'commit_strategy': CommitStrategy.VAD,
}

# TTS: Quality-focused
TTS_CONFIG = {
    'chunk_length_schedule': [120, 160, 250, 290],  # Default ElevenLabs
    'auto_mode': False,
}

# For mobile/low-latency, use:
FAST_TTS_CONFIG = {
    'chunk_length_schedule': [50, 100, 150, 200],
    'auto_mode': True,
}
```

---

## Testing Checklist

- [ ] `ELEVENLABS_API_KEY` is set and valid
- [ ] `websockets` package is installed
- [ ] STT session creates successfully
- [ ] TTS session creates successfully
- [ ] Audio chunks are sent to STT
- [ ] Text chunks are sent to TTS
- [ ] Partial transcripts received from STT
- [ ] Audio chunks received from TTS
- [ ] Multi-context TTS works (2+ concurrent contexts)
- [ ] Cleanup on disconnect works
- [ ] Error handling works with fallback
- [ ] Testing with real WebRTC audio
- [ ] Load test with multiple users

---

## Common Issues & Solutions

### Issue: "ELEVENLABS_API_KEY not set"
**Solution:** Set environment variable before running:
```bash
export ELEVENLABS_API_KEY="your_key_here"
```

### Issue: "not available. Install with: pip install websockets"
**Solution:** Install missing dependency:
```bash
pip install websockets
```

### Issue: STT not receiving transcripts
**Solution:** 
1. Check audio is 16kHz PCM format
2. Verify VAD settings (increase `vad_silence_threshold_secs`)
3. Check `on_commit` callback is properly defined

### Issue: TTS audio chunks not received
**Solution:**
1. Ensure context is initialized
2. Verify text is being sent
3. Call `flush_context()` to generate remaining audio
4. Check `on_audio_chunk` callback

### Issue: WebSocket connection timeout
**Solution:**
1. Verify API key is valid
2. Check network connectivity
3. Increase timeout values if needed
4. Implement reconnection logic

---

## Deployment Notes

1. **WebSocket Port**: Ensure WSS (secure WebSocket) is available
2. **Rate Limits**: ElevenLabs has rate limits - implement queue if needed
3. **Audio Format**: Always resample input to 16kHz for STT
4. **Connection Pool**: Consider connection pooling for high concurrency
5. **Monitoring**: Track session count, latency, error rates
6. **Logging**: Enable debug logging during integration phase

---

## References

- [ElevenLabs Realtime STT API](https://elevenlabs.io/docs/api-reference/speech-to-text/v-1-speech-to-text-realtime)
- [ElevenLabs Multi-Context TTS API](https://elevenlabs.io/docs/api-reference/text-to-speech/v-1-text-to-speech-voice-id-multi-stream-input)
- [WebSocket Integration Guide](./convonet/elevenlabs/WEBSOCKET_INTEGRATION_GUIDE.md)
- [Usage Examples](./convonet/elevenlabs/WEBSOCKET_EXAMPLES.py)

---

## Support

For issues or questions:
1. Check the integration guide
2. Review usage examples
3. Enable debug logging
4. Test with WEBSOCKET_EXAMPLES.py
5. Verify API key and network connectivity
