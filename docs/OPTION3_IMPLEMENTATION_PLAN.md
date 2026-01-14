# Option 3 (Hybrid) Implementation Plan

## Overview
Combining faster LLM model (Claude Haiku) with text streaming for early TTS generation to reduce latency from ~10s to <5s.

## Implementation Steps

### Step 1: Add Model Override Support
1. Add `model` parameter to `_get_agent_graph()` in `routes.py`
2. Use model if provided, otherwise use env var/default
3. Update cache key to include model override

### Step 2: Add Text Chunk Callback
1. Add `text_chunk_callback` parameter to `_run_agent_async()` in `routes.py`
2. Call callback when text chunks arrive in stream processing
3. Pass callback through to `_get_agent_graph()` and agent execution

### Step 3: Voice-Specific Model Override
1. Modify `process_with_agent()` in `webrtc_voice_server.py` to:
   - Pass `model="claude-3-5-haiku-20241022"` for voice responses
   - Pass `text_chunk_callback` to accumulate text and start early TTS

### Step 4: Early TTS Implementation
1. In `webrtc_voice_server.py`, implement text accumulator
2. Detect sentence boundaries (first complete sentence)
3. Start TTS generation as soon as first sentence is available
4. Continue streaming TTS for subsequent sentences

## Expected Results
- Agent processing: ~5s → ~2-3s (faster model)
- First TTS chunk: Start ~2-3s earlier (text streaming)
- **Total latency: ~10s → ~3-4s** ✅

## Files to Modify
1. `convonet/routes.py` - Add model and text_chunk_callback parameters
2. `convonet/webrtc_voice_server.py` - Use faster model and implement early TTS

