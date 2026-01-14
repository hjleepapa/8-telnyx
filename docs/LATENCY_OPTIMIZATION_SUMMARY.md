# Latency Optimization Summary: Reducing Response Time to <5 seconds

## Problem
Current system waits for **complete agent response** before starting TTS, causing 10+ second delays.

**Current Timeline:**
- Transcription: ~2 seconds ✅
- Agent processing (waiting for full response): ~5 seconds ⚠️
- First TTS chunk: ~3 seconds ⚠️
- **Total: ~10 seconds** ❌

## Solution Options

### Option 1: Use Faster LLM Model (Simplest)
**Change:** Use Claude Haiku instead of Claude Sonnet 4 for voice responses
- **Expected improvement:** ~2-3 seconds faster agent processing
- **Implementation:** Add voice-specific model override in `process_with_agent`
- **Pros:** Simple, no architectural changes
- **Cons:** Slightly lower quality responses

### Option 2: Text Streaming with Early TTS (Recommended)
**Change:** Start TTS as soon as first sentence arrives (don't wait for full response)
- **Expected improvement:** ~3-5 seconds faster (start TTS while agent is still generating)
- **Implementation:** 
  1. Add `text_chunk_callback` parameter to `_run_agent_async` in `routes.py`
  2. Accumulate text chunks in `webrtc_voice_server.py`
  3. Start TTS on first complete sentence (sentence boundary detection)
  4. Continue streaming TTS for subsequent sentences
- **Pros:** Maintains quality, significant latency reduction
- **Cons:** More complex implementation

### Option 3: Hybrid Approach
**Change:** Use faster model + text streaming
- **Expected improvement:** ~5-7 seconds faster
- **Implementation:** Combine Option 1 + Option 2
- **Pros:** Maximum latency reduction
- **Cons:** Most complex

## Recommended: Start with Option 1

**Quick Win:** Modify `process_with_agent` to use Claude Haiku for voice responses:
```python
# In webrtc_voice_server.py, modify process_with_agent call
# Add voice_model parameter to use faster model
```

**Expected Result:** 
- Agent processing: ~5s → ~2-3s
- Total latency: ~10s → ~7-8s (still above 5s target)

**Then implement Option 2** for full <5s target.

## Next Steps

1. **Immediate:** Add Claude Haiku model override for voice responses
2. **Short-term:** Implement text chunk callback mechanism
3. **Long-term:** Full streaming TTS pipeline

## Files to Modify

1. `convonet/webrtc_voice_server.py` - Add voice model override
2. `convonet/routes.py` - Add text_chunk_callback parameter
3. `convonet/llm_provider_manager.py` - Support voice-specific model selection

