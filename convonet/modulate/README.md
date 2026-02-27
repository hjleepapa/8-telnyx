# Modulate Voice Intelligence Integration

Uses [Modulate Velma-2 API](https://modulate-developer-apis.com/web/docs.html) for transcription with emotion detection, speaker diarization, and accent identification.

## Models

| Model | Endpoint | Type | Use Case |
|-------|----------|------|----------|
| **Velma-2 Batch** | `/api/velma-2-stt-batch` | REST | Full audio after user stops speaking |
| **Velma-2 Streaming** | `/api/velma-2-stt-streaming` | WebSocket | Real-time, low-latency (5 concurrent, 1000 hrs/mo, $0.025/hr) |

## Setup

1. Add to `.env`:
   ```
   MODULATE_API_KEY=your_api_key_here
   ```

2. Get API key from [Modulate Developer Console](https://modulate-developer-apis.com).

## Usage

### Batch transcription (text only)

```python
from convonet.modulate import transcribe_audio_with_modulate

text = transcribe_audio_with_modulate(audio_buffer, language="en")
```

### Batch transcription (full result + emotion)

```python
from convonet.modulate import transcribe_audio_with_modulate_full

result = transcribe_audio_with_modulate_full(
    audio_buffer,
    language="en",
    emotion_signal=True,
    speaker_diarization=False,
)
if result:
    print(result.text)           # "Hello everyone..."
    print(result.primary_emotion) # "Neutral", "Frustrated", etc.
    for u in result.utterances:
        print(f"  {u.emotion}: {u.text}")
```

### Streaming STT (real-time)

```python
from convonet.modulate import ModulateStreamingSTT

stt = ModulateStreamingSTT(
    session_id="sess-1",
    on_partial=lambda t: print(f"Partial: {t}"),
    on_final=lambda t, m: print(f"Final: {t} (emotion: {m.get('emotion')})"),
    on_emotion=lambda e: print(f"Emotion: {e}"),
)
await stt.connect()
await stt.send_audio(pcm_chunk)
# ... receive via callbacks
await stt.close()
```

### Direct service

```python
from convonet.modulate import get_modulate_service

service = get_modulate_service()
result = service.transcribe_audio_buffer(
    audio_buffer,
    emotion_signal=True,
    speaker_diarization=True,
)
```

## Supported formats

- **Batch**: WAV, WebM, PCM (auto-converted to WAV), up to 100MB
- **Streaming**: PCM 16-bit LE (48kHz or 16kHz)
- 70+ languages

## Integration with voice pipeline

Modulate works **independently** of Deepgram. You can:

1. **Replace Deepgram** – Use Modulate Batch or Streaming for all transcription
2. **Parallel** – Use Modulate for emotion context, Deepgram for low-latency
3. **Fallback** – Try Modulate first; fall back to Deepgram on failure

**Note:** Modulate Streaming WebSocket protocol may need adjustment per Modulate's actual API spec. Check [Modulate docs](https://modulate-developer-apis.com/web/docs.html) for message format.
