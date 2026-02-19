# STT & TTS Provider Comparison

Comprehensive comparison of Cartesia, AssemblyAI, Deepgram, ElevenLabs, Rime, and Inworld for Speech-to-Text (STT) and Text-to-Speech (TTS) in the Convonet voice assistant.

---

## Provider Capability Matrix

| Provider | STT | TTS | Streaming STT | Streaming TTS |
|----------|-----|-----|---------------|---------------|
| **Deepgram** | ✅ | ✅ | ✅ | ✅ (low-latency) |
| **Cartesia** | ✅ | ✅ | ✅ | ✅ (REST used) |
| **AssemblyAI** | ✅ | ❌ | ✅ (fallback) | — |
| **ElevenLabs** | ✅ | ✅ | ✅ | ⚠️ (MP3, LiveKit fallback) |
| **Rime** | ❌ | ✅ | — | ✅ |
| **Inworld** | ❌ | ✅ | — | ✅ (REST + WebSocket) |

---

## 1. DEEPGRAM

### STT (Speech-to-Text)

**Function:** `transcribe_audio_with_deepgram_webrtc()` / `DeepgramService.transcribe_audio_buffer()`

**How it works:**
- **Batch mode:** Accepts raw PCM or WebM/Opus. Detects WebM (MediaRecorder) and sends directly; otherwise creates WAV from PCM. Tries multiple sample rates (48kHz, 16kHz, 44.1kHz, 8kHz) if first attempt fails.
- **Streaming mode:** WebSocket to `listen.v2` API. Accepts Opus in WebM container at 48kHz. Returns interim + final transcripts with VAD (Voice Activity Detection).

**API:** `POST https://api.deepgram.com/v1/listen` (batch) or `wss://api.deepgram.com/v1/listen` (streaming)

**Audio format:**
- Batch: WebM, WAV, or raw PCM (auto-detected)
- Streaming: Opus in WebM, 48kHz

**Call flow (batch):**
```
User stops recording → Audio buffer (WebM or PCM) → Deepgram HTTP POST
→ Response JSON with transcript → process_audio_async continues
```

**Call flow (streaming):**
```
User starts recording → LiveKit/MediaRecorder sends chunks
→ StreamingSTTSession forwards to Deepgram WebSocket
→ Interim transcripts (optional) + Final on VAD/silence
→ on_final_transcript callback → process_audio_async with transcribed_text_override
```

**Pros:**
- Native WebM/Opus support (no resampling for browser)
- Low latency streaming (~100–300ms)
- 30+ languages, auto-detect
- Strong accuracy (Nova-2 model)
- Single provider for STT + TTS

**Cons:**
- Requires `DEEPGRAM_API_KEY`
- Streaming needs Deepgram SDK v3+

---

### TTS (Text-to-Speech)

**Function:** `get_deepgram_tts_service().synthesize_speech()`

**How it works:**
- REST POST to `https://api.deepgram.com/v1/speak`
- Returns linear16 PCM (or MP3) at 48kHz
- Voices: aura-asteria-en, aura-luna-en, aura-stella-en, etc.

**Pros:**
- Native 48kHz PCM for LiveKit
- Low latency
- Same API key as STT

**Cons:**
- Fewer voice options than specialized TTS providers
- Quality good but not top-tier

---

## 2. CARTESIA

### STT (Speech-to-Text)

**Function:** `CartesiaService.transcribe_audio_buffer()` or `CartesiaStreamingSTT`

**How it works:**
- **Batch mode:** Wraps PCM in WAV header (48kHz), POST to `https://api.cartesia.ai/stt`. Model: `ink-whisper`.
- **Streaming mode:** WebSocket to Cartesia. Resamples 48kHz → 16kHz before sending. Real-time partial/final transcripts.

**API:** `POST https://api.cartesia.ai/stt` (batch) or WebSocket (streaming)

**Audio format:**
- Batch: WAV, 48kHz mono 16-bit
- Streaming: PCM 16kHz (resampled from LiveKit 48kHz)

**Call flow (batch):**
```
User stops recording → PCM 48kHz → _pcm_to_wav() → Cartesia POST
→ JSON { "text": "..." } → process_audio_async continues
```

**Call flow (streaming):**
```
start_recording → CartesiaStreamingSTT created, WebSocket connected
→ LiveKit callback: 48kHz chunks → resample to 16kHz → send_audio_chunk()
→ on_final callback with transcript → process_audio_async(transcribed_text_override)
```

**Pros:**
- Single API for STT + TTS
- Streaming STT with low latency
- Good accuracy (Whisper-based)

**Cons:**
- Batch STT ~5–10s latency
- Requires resampling for streaming (48→16kHz)
- `CARTESIA_API_KEY` required

---

### TTS (Text-to-Speech)

**Function:** `CartesiaService.synthesize_rest_api()`

**How it works:**
- REST POST to `https://api.cartesia.ai/tts/bytes`
- Payload: model_id, transcript, voice (id), output_format (pcm_s16le, 48kHz)
- Returns raw PCM 16-bit LE

**Pros:**
- Native 48kHz PCM for LiveKit
- High-quality voices
- Simple REST (no streaming complexity)

**Cons:**
- Non-streaming: full audio before response (~1–3s for long text)
- SSE streaming had base64 chunk issues (REST used instead)

---

## 3. ASSEMBLYAI

### STT Only (No TTS)

**Function:** `transcribe_with_assemblyai()` → `transcribe_with_assemblyai_batch()` (primary)

**How it works:**
- **Batch (primary):** Upload WAV to `https://api.assemblyai.com/v2/upload` → get `upload_url` → POST to `/v2/transcript` with `audio_url` → poll `/v2/transcript/{id}` until `status: completed`.
- **Streaming (fallback):** WebSocket, send 100ms PCM chunks, ForceEndpoint to commit.

**API:** REST upload + transcript + poll

**Audio format:** PCM 16kHz mono 16-bit (WAV). Input is resampled from 48kHz.

**Call flow:**
```
User stops recording → PCM 48kHz → resample_audio(48k→16k)
→ _pcm_to_wav(16k) → POST upload → POST transcript → Poll every 0.5s
→ status=completed → text → process_audio_async continues
```

**Pros:**
- Strong accuracy
- Speaker diarization, summarization (if configured)
- Reliable batch API for "record then transcribe"

**Cons:**
- No TTS
- Batch latency ~2–5s (upload + processing + poll)
- Requires 16kHz; resampling adds overhead
- `ASSEMBLYAI_API_KEY` required

---

## 4. ELEVENLABS

### STT (Speech-to-Text)

**Function:** `ElevenLabsStreamingSTT` (WebSocket)

**How it works:**
- WebSocket to `wss://api.elevenlabs.io/v1/speech-to-text/realtime`
- Sends PCM 16-bit 16kHz chunks
- VAD-based or manual commit strategy
- Model: `scribev1`

**API:** WebSocket with query params (model_id, language_code, vad_threshold, etc.)

**Audio format:** PCM 16kHz (resampled from 48kHz)

**Call flow:**
```
start_recording → ElevenLabsStreamingSTT created
→ LiveKit 48kHz → resample to 16kHz → send_audio()
→ on_commit callback → process_audio_async(transcribed_text_override)
```

**Pros:**
- Low latency (~100–500ms)
- Word-level timestamps
- Language detection

**Cons:**
- Requires resampling 48→16kHz
- `ELEVENLABS_API_KEY` required
- STT integration less mature than Deepgram in this codebase

---

### TTS (Text-to-Speech)

**Function:** ElevenLabs TTS (typically returns MP3)

**How it works:**
- ElevenLabs usually returns MP3. For LiveKit (PCM), the codebase falls back to Deepgram when ElevenLabs is selected and LiveKit is active.
- Used for acknowledgments, filler, agent response when not using LiveKit.

**Pros:**
- Very natural, expressive voices
- Emotion control

**Cons:**
- MP3 output; LiveKit expects PCM → fallback to Deepgram
- More complex integration for real-time PCM

---

## 5. RIME

### TTS Only (No STT)

**Function:** `RimeTTSService.synthesize()`

**How it works:**
- WebSocket to `wss://users-ws.rime.ai/ws?speaker={speaker}&modelId=arcana&audioFormat=wav&samplingRate=48000`
- Sends text as word tokens + `<EOS>`
- Receives WAV stream; strips 44-byte header, returns raw PCM 48kHz
- Resamples to 48kHz if Rime returns different rate

**API:** WebSocket

**Audio format:** WAV at 48kHz (requested via samplingRate param), converted to PCM

**Call flow:**
```
_synthesize_audio_linear16(provider="rime")
→ Tokenize text (word + space) → WebSocket connect
→ Send tokens + <EOS> → Receive WAV chunks
→ _wav_to_pcm() (strip header, resample if needed)
→ Return PCM → _send_livekit_pcm()
```

**Pros:**
- High-quality Arcana model
- Multiple speakers (astra, ballad, clint, etc.)
- 48kHz native for LiveKit

**Cons:**
- No STT
- WebSocket can be slower than REST for short phrases
- `RIME_API_KEY` required

---

## 6. INWORLD

### TTS Only (No STT)

**Function:** `InworldTTSService.synthesize()` → `synthesize_rest()` (primary) or WebSocket

**How it works:**
- **REST (primary):** POST to `https://api.inworld.ai/tts/v1/voice` with `audioConfig: { audioEncoding: "LINEAR16", sampleRateHertz: 48000 }`. Returns base64 WAV; strip 44-byte header.
- **WebSocket (fallback):** `wss://api.inworld.ai/api/v1/ws/synthesize` — returns 404 on Render/cloud, so REST is used.

**API:** REST (reliable) or WebSocket (local only)

**Audio format:** LINEAR16 PCM 48kHz (explicitly requested)

**Call flow:**
```
_synthesize_audio_linear16(provider="inworld")
→ synthesize_rest(text) → POST /tts/v1/voice
→ Decode base64 audioContent → Strip WAV header
→ Return PCM → _send_livekit_pcm()
```

**Pros:**
- TTS 1.5 model (mini/max)
- REST works on cloud (WebSocket 404 on Render)
- Natural voices (e.g. Ashley)

**Cons:**
- No STT
- WebSocket unreliable on cloud
- `INWORLD_API_KEY` (Basic auth) required

---

## End-to-End Call Flow (Voice Assistant)

### High-level flow (all providers)

```
1. User authenticates (PIN)
2. LiveKit room connects
3. User clicks mic / speaks
4. start_recording:
   - If streaming STT (Deepgram/Cartesia/ElevenLabs): create streaming session, pipe LiveKit audio
5. User stops speaking → stop_recording
   - If streaming STT: get final transcript from callback
   - Else: collect full buffer
6. process_audio_async:
   a. If no transcript override: transcribe with selected STT (batch)
   b. Emit transcription to client
   c. Start processing music (hold music) to LiveKit
   d. Run agent (LLM + tools)
   e. Generate TTS with selected provider (ack, filler, full response)
   f. _send_livekit_pcm() → stops hold music, streams TTS
   g. Emit agent_response to client
7. User hears response via LiveKit
```

### Provider-specific flows

| STT Provider | Recording → Transcript | Latency |
|--------------|------------------------|---------|
| Deepgram streaming | Chunks → WebSocket → Final on VAD | ~200–500ms |
| Cartesia streaming | Chunks → resample → WebSocket → Final | ~300–600ms |
| AssemblyAI batch | Full buffer → resample → Upload → Poll | ~2–5s |
| ElevenLabs streaming | Chunks → resample → WebSocket → Commit | ~200–500ms |
| Deepgram batch (default) | Full buffer → HTTP POST | ~500ms–2s |

| TTS Provider | Text → Audio | Format |
|--------------|--------------|--------|
| Deepgram | REST /speak | PCM 48kHz |
| Cartesia | REST /tts/bytes | PCM 48kHz |
| Rime | WebSocket | WAV→PCM 48kHz |
| Inworld | REST /tts/v1/voice | PCM 48kHz |
| ElevenLabs | (Fallback Deepgram for LiveKit) | MP3→PCM |

---

## Recommendations

| Use case | STT | TTS |
|----------|-----|-----|
| **Lowest latency** | Deepgram streaming | Deepgram / Cartesia |
| **Best accuracy** | AssemblyAI / Deepgram | Cartesia / Rime / Inworld |
| **Single provider** | Deepgram (STT+TTS) | Deepgram |
| **Best voice quality** | — | Rime / Inworld / Cartesia |
| **Cloud deployment** | Deepgram / AssemblyAI / Cartesia | Inworld REST / Rime / Cartesia |
| **Cost-effective** | Deepgram / AssemblyAI | Deepgram |

---

## Environment Variables

| Provider | Key |
|----------|-----|
| Deepgram | `DEEPGRAM_API_KEY` |
| Cartesia | `CARTESIA_API_KEY` |
| AssemblyAI | `ASSEMBLYAI_API_KEY` |
| ElevenLabs | `ELEVENLABS_API_KEY` |
| Rime | `RIME_API_KEY` |
| Inworld | `INWORLD_API_KEY` |
