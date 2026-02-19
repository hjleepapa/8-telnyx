# Service Functions Reference

Complete reference of every function in all `service.py` files across the Convonet voice stack.

---

## 1. convonet/deepgram/service.py

### Class: `DeepgramService`

| Function | Description |
|----------|-------------|
| **`__init__(api_key)`** | Initialize Deepgram client. Reads `DEEPGRAM_API_KEY` from env if not provided. Raises if key missing. |
| **`transcribe_audio_buffer(audio_buffer, language)`** | Transcribe raw audio. Detects WebM (MediaRecorder) and sends as-is; else creates WAV from PCM. Tries multiple sample rates (48k, 16k, 44.1k, 8k). Returns transcript or None. |
| **`_create_wav_from_pcm(pcm_data)`** | Create temp WAV file from raw PCM. Tries 4 configs (48k/16k/44.1k/8k mono 16-bit). Returns file path or None. |
| **`_transcribe_file(file_path, language)`** | POST file to `https://api.deepgram.com/v1/listen`. Params: nova-2, smart_format, punctuate, detect_language. Returns transcript from `results.channels[0].alternatives[0].transcript`. |
| **`_analyze_audio_quality(audio_buffer)`** | Analyze PCM: RMS, clipping %, silence detection, speech-like frequency ratio. Returns dict with `is_silence`, `rms`, `clipping_percentage`, etc. |
| **`_strip_markdown_for_tts(text)`** | Remove markdown (bold, italic, headers, code, links, blockquotes) so TTS doesn't read "star star". |
| **`synthesize_speech(text, voice, model, encoding, sample_rate, container)`** | POST to `https://api.deepgram.com/v1/speak`. Returns audio bytes (linear16 PCM when encoding/container specified). Default voice: aura-asteria-en. |

### Module-level

| Function | Description |
|----------|-------------|
| **`get_deepgram_service()`** | Singleton getter. Creates `DeepgramService` on first call. |

---

## 2. convonet/cartesia/service.py

### Module-level (before class)

| Function | Description |
|----------|-------------|
| **`_pcm_to_wav(pcm_bytes, sample_rate, channels)`** | Wrap raw PCM s16le in 44-byte WAV header. Used for Cartesia STT API. |

### Class: `CartesiaService`

| Function | Description |
|----------|-------------|
| **`__init__(api_key)`** | Initialize Cartesia. Uses `CARTESIA_API_KEY`. Sets model_id (sonic-english), voice_id (Kiefer), stt_model (ink-whisper). |
| **`is_available()`** | Return True if API key and SDK/client available. |
| **`transcribe_audio_buffer(audio_buffer, language)`** | Batch STT: wrap PCM in WAV (48kHz), POST to `https://api.cartesia.ai/stt`. Model ink-whisper. Returns transcript. Slow (~5–10s). |
| **`synthesize_stream(text, voice_id, sample_rate)`** | SSE streaming TTS via SDK `client.tts.sse()`. Yields PCM 16-bit LE chunks. Handles base64 decode, padding. sample_rate default 48000. |
| **`synthesize_rest_api(text, voice_id, sample_rate)`** | REST POST to `https://api.cartesia.ai/tts/bytes`. Returns full PCM audio. Used when streaming has issues. |
| **`get_approach_comparison()`** | Static. Returns dict comparing SSE streaming vs REST vs WebSocket. |
| **`print_approach_guide()`** | Static. Prints ASCII guide for choosing TTS approach. |
| **`get_streaming_stt_session(session_id, on_final)`** | Create `CartesiaStreamingSTT` via `get_cartesia_streaming_session()`. For real-time WebSocket STT. |
| **`list_voices()`** | List Cartesia voices via SDK. Returns dict or default voice info. |

### Module-level

| Function | Description |
|----------|-------------|
| **`get_cartesia_service()`** | Singleton getter for `CartesiaService`. |

---

## 3. convonet/assemblyai/service.py

### Class: `AssemblyAIStreamingSTT`

| Function | Description |
|----------|-------------|
| **`__init__(api_key, sample_rate, encoding, language, ...)`** | Configure streaming STT. sample_rate 16000, encoding pcm_s16le. Builds WebSocket URL with query params. |
| **`connect_async()`** | Connect to `wss://streaming.assemblyai.com/v3/ws`. Wait for SessionBegins/Begin. Return True on success. |
| **`send_audio_async(audio_bytes)`** | Send raw audio bytes over WebSocket. |
| **`receive_transcript_async(timeout)`** | Receive message. On Turn: return transcript/utterance. On Termination: return None. |
| **`close_async()`** | Send Terminate, close WebSocket. |
| **`connect()`** | Sync wrapper for connect_async. Uses thread if event loop running. |
| **`send_audio(audio_bytes)`** | Sync wrapper for send_audio_async. |
| **`receive_transcript(timeout)`** | Sync wrapper for receive_transcript_async. |
| **`close()`** | Sync wrapper for close_async. |
| **`_connect_threaded()`** | Run connect_async in new thread/loop. |
| **`_send_audio_threaded(audio_bytes)`** | Run send_audio_async in new thread. |
| **`_receive_transcript_threaded(timeout)`** | Run receive_transcript_async in new thread. |
| **`_close_threaded()`** | Run close_async in new thread. |
| **`transcribe_audio_buffer(audio_bytes, timeout)`** | Connect, send 100ms chunks, ForceEndpoint, receive all Turn results, close. Returns joined transcript. |
| **`get_session_info()`** | Return dict with session_id, sample_rate, current_turn, transcript_buffer. |

### Module-level

| Function | Description |
|----------|-------------|
| **`get_assemblyai_streaming_session(session_id, create_if_missing)`** | Get/create session from `_streaming_sessions` registry. |
| **`remove_assemblyai_streaming_session(session_id)`** | Close and remove session from registry. |
| **`_pcm_to_wav(pcm_bytes, sample_rate, channels)`** | Wrap PCM in WAV header. 44-byte header, RIFF/WAVE/fmt/data. |
| **`transcribe_with_assemblyai_batch(audio_bytes, sample_rate, timeout)`** | Upload WAV to AssemblyAI, create transcript, poll until completed. Returns text. Primary path for "record then transcribe". |
| **`transcribe_with_assemblyai(audio_bytes)`** | Try batch first; fallback to streaming `AssemblyAIStreamingSTT.transcribe_audio_buffer()`. |

---

## 4. convonet/elevenlabs/service.py

### Enum: `EmotionType`

| Value | Description |
|-------|-------------|
| HAPPY, SAD, EXCITED, CALM, STRESSED, EMPATHETIC, PROFESSIONAL, CASUAL, NEUTRAL | Emotion presets for voice synthesis. |

### Class: `ElevenLabsService`

| Function | Description |
|----------|-------------|
| **`__init__(api_key)`** | Initialize ElevenLabs client. Uses `ELEVENLABS_API_KEY`. Default voice Rachel, model eleven_multilingual_v2. Sets emotion_settings dict. |
| **`is_available()`** | Return True if SDK and client available. |
| **`_strip_markdown_for_tts(text)`** | Same as Deepgram: remove markdown before TTS. |
| **`synthesize(text, voice_id, model, stability, similarity_boost, style, use_speaker_boost)`** | TTS via SDK (text_to_speech.convert or convert or generate). Returns MP3 bytes. |
| **`synthesize_with_emotion(text, emotion, voice_id, model)`** | Use emotion_settings for emotion, call synthesize. |
| **`synthesize_multilingual(text, language, voice_id, model)`** | TTS in specified language. Uses multilingual model. |
| **`clone_voice(audio_samples, voice_name, description)`** | Clone voice from audio samples. Saves to temp files, calls voices.clone. Returns voice_id. |
| **`get_voice(voice_id)`** | Get voice info by ID. Returns dict. |
| **`list_voices()`** | List all voices. Returns list of dicts. |
| **`synthesize_with_style(text, style, voice_id, model)`** | Map style (conversational/professional/casual/formal) to EmotionType, call synthesize_with_emotion. |
| **`transcribe_audio_buffer(audio_buffer, language)`** | Batch STT via speech_to_text.convert. Uses scribev1 model. Wraps bytes in BytesIO. Returns transcript. |

### Module-level

| Function | Description |
|----------|-------------|
| **`get_elevenlabs_service()`** | Singleton getter for `ElevenLabsService`. |

---

## 5. convonet/rime/service.py

### Class: `RimeTTSService`

| Function | Description |
|----------|-------------|
| **`__init__(speaker, model_id, audio_format)`** | Init Rime TTS. speaker (astra), model arcana, format wav. sampling_rate 48000. Builds WebSocket URL. |
| **`_synthesize_async(text_tokens)`** | Connect to `wss://users-ws.rime.ai/ws`, send tokens one by one, receive WAV chunks. Call _wav_to_pcm, return raw PCM 48kHz. |
| **`synthesize(text, speaker)`** | Tokenize text (words + spaces), append &lt;EOS&gt;. Run _synthesize_async. Use _synthesize_threaded if event loop running. |
| **`_synthesize_threaded(tokens)`** | Run _synthesize_async in new thread/event loop. 60s timeout. |
| **`_wav_to_pcm(wav_bytes, target_sr)`** | Static. Strip 44-byte WAV header. Read sample rate from header. Resample to 48kHz if needed (scipy). |
| **`_tokenize_text(text)`** | Static. Split by spaces, return list of words with spaces between. |
| **`get_speaker_list()`** | Return list of speaker names: astra, ballad, clint, helen, marcus, scott. |

### Module-level

| Function | Description |
|----------|-------------|
| **`get_rime_service(speaker)`** | Singleton getter. Updates URL if speaker changes. |
| **`rime_tts_synthesize(text, speaker)`** | Convenience: get service, call synthesize. |

---

## 6. convonet/inworld/service.py

### Class: `InworldTTSService`

| Function | Description |
|----------|-------------|
| **`__init__(api_key, workspace, character_id, max_buffer_delay_ms, buffer_char_threshold, auto_mode)`** | Init Inworld TTS. Reads INWORLD_API_KEY (stripped). WebSocket URL for synthesize. |
| **`_synthesize_async(text, context_id)`** | Connect to `wss://api.inworld.ai/api/v1/ws/synthesize`. Send create_context, synthesize (JSON). Receive audio (binary or JSON base64). Convert Float32→PCM16 if needed. Close context. |
| **`synthesize_rest(text)`** | POST to `https://api.inworld.ai/tts/v1/voice`. JSON: text, voiceId Ashley, modelId inworld-tts-1.5-mini, audioConfig LINEAR16 48kHz. Decode base64 audioContent, strip WAV header. |
| **`synthesize(text, context_id)`** | Try synthesize_rest first. Fallback to _synthesize_async. Use _synthesize_threaded if loop running. |
| **`_synthesize_threaded(text, context_id)`** | Run _synthesize_async in new thread. 60s timeout. |
| **`create_context(context_id)`** | Add context to self.contexts dict. |
| **`close_context(context_id)`** | Remove context from dict. |
| **`get_contexts()`** | Return list of active context IDs. |

### Module-level

| Function | Description |
|----------|-------------|
| **`get_inworld_service(context_id)`** | Singleton getter. Creates context if missing. |
| **`inworld_tts_synthesize(text, context_id)`** | Convenience: get service, call synthesize. |

---

## Quick Reference by Capability

| Capability | Deepgram | Cartesia | AssemblyAI | ElevenLabs | Rime | Inworld |
|------------|----------|----------|------------|------------|------|---------|
| **STT batch** | transcribe_audio_buffer | transcribe_audio_buffer | transcribe_with_assemblyai_batch | transcribe_audio_buffer | — | — |
| **STT streaming** | (in webrtc_voice_server) | get_streaming_stt_session | AssemblyAIStreamingSTT | (streaming_stt.py) | — | — |
| **TTS** | synthesize_speech | synthesize_rest_api | — | synthesize | synthesize | synthesize |
| **TTS streaming** | — | synthesize_stream | — | — | _synthesize_async | _synthesize_async |
| **Singleton** | get_deepgram_service | get_cartesia_service | — | get_elevenlabs_service | get_rime_service | get_inworld_service |
