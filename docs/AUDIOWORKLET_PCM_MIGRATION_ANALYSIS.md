# AudioWorklet PCM Migration Analysis

## Current vs Proposed Architecture

### Current (SocketIO Voice Assistant)

| Tier | Path | Format | Used For |
|------|------|--------|----------|
| **Primary** | MediaRecorder → WebM/Opus chunks | `audio/webm;codecs=opus` | Streaming STT (Deepgram) + batch |
| **Fallback** | PCM → WAV | Raw PCM wrapped in WAV | Batch STT when WebM fails |

**Flow:** `webrtc_voice_assistant_socketio.html` → MediaRecorder (100ms chunks) → base64 → `socket.emit('audio_data')` → backend → Deepgram (WebM) or Cartesia (resampled).

### Proposed

| Tier | Path | Format | Used For |
|------|------|--------|----------|
| **Primary** | AudioWorklet → PCM 16-bit | linear16, 48kHz, mono | Streaming STT (lowest latency) |
| **Secondary** | MediaRecorder → WebM | `audio/webm;codecs=opus` | Fallback when AudioWorklet unavailable |
| **Tertiary** | PCM → WAV | Raw PCM wrapped in WAV | Batch fallback |

---

## Side Effects & Breaking Changes

### 1. **Backend: Deepgram Streaming Config**

**Current:** `StreamingSTTSession` expects WebM/Opus:
```python
encoding="opus",
container="webm",
sample_rate="48000"
```

**With PCM:** Must change to:
```python
encoding="linear16",
channels=1,
sample_rate=48000
# No container
```

**Impact:** You need format detection or a separate session type. Options:
- **A)** Client sends `format: "pcm"` or `format: "webm"` in `start_recording`; backend creates the right Deepgram connection.
- **B)** Two streaming session classes: `DeepgramStreamingPCM` and `DeepgramStreamingWebM`.
- **C)** Single session that accepts both (Deepgram may not support switching mid-stream).

### 2. **Backend: Cartesia Streaming**

**Current:** Cartesia path receives WebM chunks and calls `resample_audio(audio_chunk, 48000, 16000)`.  
**Problem:** `resample_audio()` expects **raw PCM** (`np.frombuffer(..., dtype=np.int16)`). WebM bytes are not PCM—interpreting them as PCM produces garbage. Cartesia streaming with MediaRecorder is effectively broken unless there is WebM→PCM decoding elsewhere (there isn’t in the current flow).

**With PCM:** Cartesia would receive real PCM and `resample_audio` would work correctly. **This is a fix, not a regression.**

### 3. **Buffer Accumulation for Batch**

**Current:** `audio_buffer` is built from WebM chunks. On `stop_recording`, batch STT receives WebM (or PCM from LiveKit).

**With PCM:** If primary path is AudioWorklet, `audio_buffer` becomes raw PCM. Batch transcription already supports PCM via `_create_wav_from_pcm()`. No change needed for Deepgram batch.

### 4. **Socket.IO Transport**

**Current:** `socket.emit('audio_data', { audio: base64 })` — JSON with base64 string.

**With PCM:** Options:
- **A)** Keep base64: `socket.emit('audio_data', { audio: base64, format: 'pcm' })` — ~33% overhead, minimal code change.
- **B)** Binary: `socket.emit('audio_data', arrayBuffer)` — Socket.IO supports binary; backend receives `bytes` directly. More efficient.

### 5. **Visualizer**

**Current:** Uses `createMediaStreamSource(stream)` from the same `getUserMedia` stream as MediaRecorder.

**With AudioWorklet:** Options:
- **A)** Keep MediaRecorder for visualizer only (same stream, no recording) — wasteful.
- **B)** Use `AnalyserNode` in the AudioWorklet’s `AudioContext` — requires sharing the context or routing worklet output to an analyser.
- **C)** Use a separate `getUserMedia` + `createMediaStreamSource` for the visualizer — two mic accesses, possible permission prompts.

### 6. **Browser Compatibility**

| Feature | Chrome | Firefox | Safari | Edge |
|---------|--------|---------|--------|------|
| AudioWorklet | 66+ | 76+ | 14.1+ | 79+ |
| MediaRecorder WebM | Wide | Wide | 14.1+ | Wide |

**Impact:** Older Safari (< 14.1) and some mobile browsers may lack AudioWorklet. MediaRecorder fallback is important.

### 7. **LiveKit vs SocketIO Paths**

The codebase has two distinct flows:

- **LiveKit** (`webrtc_voice_assistant.html`): Mic → LiveKit → server gets PCM. No MediaRecorder.
- **SocketIO** (`webrtc_voice_assistant_socketio.html`): Mic → MediaRecorder → WebM → Socket.IO.

The proposed change applies to the **SocketIO** path. LiveKit already uses PCM and is unaffected.

### 8. **Sample Rate Consistency**

**Proposed:** `new AudioContext({ sampleRate: 48000 })` — matches Deepgram and current WebRTC usage. Ensure the worklet uses this context so output is 48 kHz.

### 9. **Chunk Size & Latency**

**Current:** MediaRecorder `start(100)` → ~100 ms chunks, plus Opus encoding delay.

**With AudioWorklet:** You can send smaller frames (e.g. 20 ms, 1920 samples = 3840 bytes at 48 kHz). Lower latency, more messages. Consider batching (e.g. 40–60 ms) to balance latency and overhead.

### 10. **Separate WebSocket vs Socket.IO**

Your sample uses raw `websockets` and `async for message in websocket`. The app uses Socket.IO. You can:

- **A)** Keep Socket.IO and send PCM over `audio_data` (with format flag or binary).
- **B)** Add a dedicated WebSocket for audio (e.g. `/voice/audio`) — cleaner separation but more moving parts.

---

## Migration Checklist

### Frontend (`webrtc_voice_assistant_socketio.html`)

- [ ] Add AudioWorklet processor (e.g. `pcm-processor.js`) that outputs Int16Array frames.
- [ ] Use `new AudioContext({ sampleRate: 48000 })` for the worklet.
- [ ] Try AudioWorklet first; fall back to MediaRecorder if unsupported.
- [ ] Send PCM via `socket.emit('audio_data', { audio: base64, format: 'pcm' })` or binary.
- [ ] Keep MediaRecorder path for fallback and/or visualizer.

### Backend (`webrtc_voice_server_socketio.py`)

- [ ] In `start_recording`, accept `format: 'pcm' | 'webm'` from client.
- [ ] For Deepgram streaming: create PCM vs WebM connection based on format.
- [ ] In `handle_audio_data`, branch on format:
  - PCM → forward to Deepgram (linear16) or resample for Cartesia.
  - WebM → keep current behavior.
- [ ] Ensure `audio_buffer` accumulation works for both formats (it does for batch).

### AudioWorklet Processor (new file)

```javascript
// pcm-processor.js
class PCMProcessor extends AudioWorkletProcessor {
  process(inputs, outputs, parameters) {
    const input = inputs[0];
    if (input.length > 0 && input[0].length > 0) {
      const float32 = input[0];
      const int16 = new Int16Array(float32.length);
      for (let i = 0; i < float32.length; i++) {
        const s = Math.max(-1, Math.min(1, float32[i]));
        int16[i] = s < 0 ? s * 0x8000 : s * 0x7FFF;
      }
      this.port.postMessage(int16.buffer);
    }
    return true;
  }
}
registerProcessor('pcm-processor', PCMProcessor);
```

---

## Implementation Status (Jan 2026)

The following recommendations have been implemented:

1. **StreamingSTTSession config fix:** Uses `encoding="linear16"`, `sample_rate=48000` when `use_pcm=True` (LiveKit path); keeps `encoding="opus"`, `container="webm"` when `use_pcm=False` (MediaRecorder path).
2. **Partial transcripts:** `on_partial_transcript` callback emits `transcript_partial` to client; frontend shows live interim text via `updatePartialUserTranscript()`.
3. **Batch fallback:** Unchanged; still used when streaming doesn't produce a final transcript.
4. **Both templates:** `webrtc_voice_assistant.html` (LiveKit) and `webrtc_voice_assistant_socketio.html` (MediaRecorder) both handle `transcript_partial`.

---

## Summary

| Area | Risk | Notes |
|------|------|-------|
| Deepgram streaming | Medium | Must support both opus/webm and linear16; config depends on format |
| Cartesia streaming | Low | PCM path fixes current WebM→PCM misuse |
| Batch transcription | Low | Already handles PCM via WAV |
| Visualizer | Medium | Need a strategy for worklet-based or fallback UI |
| Browser support | Low | MediaRecorder fallback covers older browsers |
| LiveKit | None | Separate path, already PCM |

**Recommendation:** Implement as a tiered system: try AudioWorklet first, fall back to MediaRecorder, then batch PCM→WAV. Add a `format` flag so the backend can choose the correct Deepgram and Cartesia configuration.
