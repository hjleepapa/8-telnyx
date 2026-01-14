# Latency Optimization Plan: Reduce Response Time to <5 seconds

## Current Problem

**Current Timeline:**
- Transcription: ~2 seconds ✅
- Agent processing (waiting for full response): ~5 seconds ⚠️
- First TTS chunk: ~3 seconds ⚠️
- **Total: ~10 seconds** ❌

**Target: <5 seconds**

## Root Cause

The system waits for the **complete agent response** before starting TTS generation. Even though we have streaming TTS chunks, we're still waiting for all text first.

## Solution: Text-to-Speech Streaming Pipeline

### Architecture Change

Instead of:
```
User Input → Agent (wait for full response) → TTS (wait for full audio) → Play
```

We need:
```
User Input → Agent (stream text chunks) → TTS (start on first sentence) → Play (as chunks arrive)
```

### Implementation Strategy

1. **Listen for agent text chunks** via `agent_stream_chunk` events
2. **Start TTS on first complete sentence** (don't wait for full response)
3. **Continue TTS for subsequent sentences** as they arrive
4. **Play audio chunks sequentially** as they're generated

### Key Changes Needed

1. **Add text chunk accumulator** in `webrtc_voice_server.py`
2. **Start TTS on first sentence** (not full response)
3. **Coordinate agent streaming with TTS streaming**
4. **Handle partial sentences** (wait for sentence completion before TTS)

### Expected Timeline After Optimization

- Transcription: ~2 seconds ✅
- First sentence from agent: ~1-2 seconds ✅
- First TTS chunk: ~2 seconds ✅
- **Total: ~4-5 seconds** ✅

### Implementation Options

#### Option 1: Event-Driven (Recommended)
- Listen for `agent_stream_chunk` events
- Accumulate text until sentence boundary
- Start TTS immediately on first sentence
- Continue for subsequent sentences

#### Option 2: Modify Agent Processing
- Change `process_with_agent` to yield chunks
- Process chunks as they arrive
- Start TTS immediately

#### Option 3: Use Faster LLM
- Switch to faster model (e.g., Claude Haiku)
- Reduce agent processing time
- Keep current architecture

### Recommended Approach

**Option 1 (Event-Driven)** is best because:
- ✅ Minimal changes to existing code
- ✅ Works with current streaming infrastructure
- ✅ Can start TTS as soon as first sentence arrives
- ✅ Maintains compatibility with all LLM providers

### Code Changes Required

1. Add `agent_stream_chunk` event handler in `webrtc_voice_server.py`
2. Implement sentence boundary detection
3. Start TTS on first complete sentence
4. Queue TTS requests for subsequent sentences
5. Coordinate with existing audio chunk streaming

### Testing Strategy

1. Test with short responses (<1 sentence)
2. Test with medium responses (2-3 sentences)
3. Test with long responses (5+ sentences)
4. Verify audio plays smoothly without gaps
5. Measure actual latency improvements

### Success Metrics

- **Target**: First audio chunk starts playing within 5 seconds
- **Current**: ~10 seconds
- **Improvement**: 50% reduction in perceived latency

