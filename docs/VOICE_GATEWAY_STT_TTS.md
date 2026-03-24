# Voice gateway: STT / TTS providers

All of this runs in **voice-gateway-service** only. The browser sends audio to the gateway; the gateway runs STT → calls **agent-llm** (text only) → runs TTS → sends audio back. **agent-llm** does not need `DEEPGRAM_VOICE_ID`, `ELEVENLABS_VOICE_ID`, `CARTESIA_VOICE_ID`, or `SPEECHMATICS_API_KEY` unless you use those providers elsewhere in that service.

## Environment variables

| Variable | Service | Description |
|----------|---------|-------------|
| `VOICE_STT_PROVIDER` | **voice-gateway** | `deepgram` (default), `elevenlabs`, `cartesia`, `speechmatics` |
| `VOICE_TTS_PROVIDER` | **voice-gateway** | `deepgram` (default), `elevenlabs`, `cartesia`, `speechmatics` |
| `DEEPGRAM_API_KEY` | voice-gateway | Required for Deepgram STT/TTS |
| `DEEPGRAM_VOICE_ID` | **voice-gateway** | Deepgram Aura TTS voice (default `aura-asteria-en` if unset) |
| `ELEVENLABS_API_KEY` | voice-gateway | For ElevenLabs STT or TTS |
| `ELEVENLABS_VOICE_ID` | **voice-gateway** | ElevenLabs TTS voice ID when `VOICE_TTS_PROVIDER=elevenlabs` |
| `CARTESIA_API_KEY` | voice-gateway | For Cartesia STT or TTS |
| `CARTESIA_VOICE_ID` | **voice-gateway** | Cartesia TTS voice when `VOICE_TTS_PROVIDER=cartesia` |
| `SPEECHMATICS_API_KEY` | **voice-gateway** | Required when `VOICE_STT_PROVIDER=speechmatics` or `VOICE_TTS_PROVIDER=speechmatics` (preview TTS API) |
| `SPEECHMATICS_TTS_VOICE` | **voice-gateway** | Optional for Speechmatics TTS: `sarah`, `theo`, `megan`, `jack` (default `sarah`) |

## Behaviour

- **Deepgram STT** accepts WebM from the browser directly.
- **ElevenLabs / Cartesia / Speechmatics STT** use **ffmpeg** (installed in the voice-gateway Docker image) to convert WebM → WAV/PCM before calling the vendor API. If conversion fails, the gateway falls back to **Deepgram** STT when possible.
- **Greeting TTS** after PIN login uses the same `VOICE_TTS_PROVIDER` and voice env vars as the main reply.
- **Speechmatics TTS** returns **WAV 16 kHz** (preview endpoint). The voice WebSocket sends `mime_type: audio/wav` for that provider. Optional package `speechmatics-tts` is listed in `requirements-render.txt`; if import fails, the gateway uses HTTP to `preview.tts.speechmatics.com`.

## Examples

```bash
# Default: Deepgram STT + Deepgram TTS
VOICE_STT_PROVIDER=deepgram
VOICE_TTS_PROVIDER=deepgram
DEEPGRAM_VOICE_ID=aura-asteria-en

# Deepgram STT, ElevenLabs TTS
VOICE_STT_PROVIDER=deepgram
VOICE_TTS_PROVIDER=elevenlabs
ELEVENLABS_VOICE_ID=...

# Speechmatics STT, Cartesia TTS
VOICE_STT_PROVIDER=speechmatics
VOICE_TTS_PROVIDER=cartesia
SPEECHMATICS_API_KEY=...
CARTESIA_VOICE_ID=...

# Speechmatics STT + Speechmatics TTS
VOICE_STT_PROVIDER=speechmatics
VOICE_TTS_PROVIDER=speechmatics
SPEECHMATICS_API_KEY=...
SPEECHMATICS_TTS_VOICE=sarah
```
