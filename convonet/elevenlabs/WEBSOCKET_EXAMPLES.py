"""
ElevenLabs WebSocket Streaming Example
Demonstrates real-time STT and TTS usage
"""

import asyncio
import logging
from convonet.elevenlabs import (
    create_streaming_stt_session,
    get_streaming_stt_session,
    remove_streaming_stt_session,
    create_streaming_tts_session,
    get_streaming_tts_session,
    remove_streaming_tts_session,
    CommitStrategy,
    TextToSpeechOutputFormat,
    VoiceSettings,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ============================================================================
# Example 1: Basic Realtime STT Usage
# ============================================================================

async def example_streaming_stt():
    """Example of real-time speech-to-text transcription"""
    
    logger.info("\n🎤 ElevenLabs Realtime STT Example")
    logger.info("=" * 60)
    
    # Track transcription
    transcription_log = {
        'partial': [],
        'committed': [],
    }
    
    def on_partial(text: str):
        """Called when partial transcript is received"""
        logger.info(f"📝 Partial: {text}")
        transcription_log['partial'].append(text)
    
    def on_commit(text: str, metadata: dict):
        """Called when transcript is committed (final for segment)"""
        logger.info(f"✅ Committed: {text}")
        if metadata.get('words'):
            logger.info(f"   Words: {metadata['words'][:3]}...")
        transcription_log['committed'].append(text)
    
    def on_error(error: str):
        """Called when error occurs"""
        logger.error(f"❌ STT Error: {error}")
    
    try:
        # Create STT session
        stt_session = await create_streaming_stt_session(
            session_id="example_stt",
            language_code="en",
            commit_strategy=CommitStrategy.VAD,  # Auto-commit on silence
            vad_silence_threshold_secs=1.5,
            vad_threshold=0.4,
            on_partial=on_partial,
            on_commit=on_commit,
            on_error=on_error,
            include_timestamps=True,
            include_language_detection=True,
        )
        
        logger.info("✅ STT session created and connected")
        
        # Simulate sending audio chunks (normally from WebRTC)
        # In real usage, these would be actual audio data from the user
        logger.info("(In production, audio chunks would be sent here)")
        
        # Wait a bit to receive any connection confirmations
        await asyncio.sleep(2)
        
        # Retrieve session for verification
        session = get_streaming_stt_session("example_stt")
        logger.info(f"Session active: {session.is_connected if session else False}")
        
        # Cleanup
        await remove_streaming_stt_session("example_stt")
        logger.info("✅ STT session closed")
        
        return transcription_log
    
    except Exception as e:
        logger.error(f"STT example failed: {e}")
        raise


# ============================================================================
# Example 2: Basic Multi-Context TTS Usage
# ============================================================================

async def example_streaming_tts():
    """Example of multi-context real-time text-to-speech"""
    
    logger.info("\n🔊 ElevenLabs Multi-Context TTS Example")
    logger.info("=" * 60)
    
    audio_chunks = {
        'agent': [],
        'system': [],
    }
    
    def on_agent_audio(audio_bytes: bytes):
        """Callback for agent audio chunks"""
        logger.info(f"📤 Agent audio chunk: {len(audio_bytes)} bytes")
        audio_chunks['agent'].append(audio_bytes)
    
    def on_system_audio(audio_bytes: bytes):
        """Callback for system audio chunks"""
        logger.info(f"📤 System audio chunk: {len(audio_bytes)} bytes")
        audio_chunks['system'].append(audio_bytes)
    
    def on_agent_final():
        """Called when agent audio is complete"""
        logger.info("✅ Agent audio generation complete")
    
    def on_system_final():
        """Called when system audio is complete"""
        logger.info("✅ System audio generation complete")
    
    try:
        # Create TTS session
        tts_session = await create_streaming_tts_session(
            session_id="example_tts",
            default_voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel
            output_format=TextToSpeechOutputFormat.PCM_48000,  # WebRTC native
            model_id="eleven_multilingual_v2",
        )
        
        logger.info("✅ TTS session created and connected")
        
        # Initialize context for agent response
        agent_context_id = await tts_session.initialize_context(
            text=" ",
            context_id="agent_response",
            voice_id="21m00Tcm4TlvDq8ikWAM",  # Rachel
            voice_settings=VoiceSettings(
                stability=0.5,
                similarity_boost=0.75,
                use_speaker_boost=True,
                speed=1.0,
            ),
            on_audio_chunk=on_agent_audio,
            on_final=on_agent_final,
        )
        logger.info(f"✅ Agent context initialized: {agent_context_id}")
        
        # Initialize context for system notification (different voice)
        system_context_id = await tts_session.initialize_context(
            text=" ",
            context_id="system_notification",
            voice_id="nPczCjzI2devNBz1zQrb",  # Different voice
            voice_settings=VoiceSettings(
                stability=0.7,
                similarity_boost=0.8,
                use_speaker_boost=True,
            ),
            on_audio_chunk=on_system_audio,
            on_final=on_system_final,
        )
        logger.info(f"✅ System context initialized: {system_context_id}")
        
        # Send text to different contexts
        logger.info("📝 Sending text to contexts...")
        await tts_session.send_text(
            "Hello, this is the agent speaking. ",
            context_id="agent_response"
        )
        await tts_session.send_text(
            "System notification: call connected. ",
            context_id="system_notification"
        )
        
        # Wait for audio generation
        await asyncio.sleep(2)
        
        # Flush contexts to get remaining audio
        logger.info("🔄 Flushing contexts...")
        await tts_session.flush_context("agent_response")
        await tts_session.flush_context("system_notification")
        
        # Wait for final chunks
        await asyncio.sleep(2)
        
        # Get buffered audio
        agent_audio = tts_session.get_context_audio_buffer("agent_response")
        system_audio = tts_session.get_context_audio_buffer("system_notification")
        
        logger.info(f"📊 Agent audio total: {len(agent_audio)} bytes")
        logger.info(f"📊 System audio total: {len(system_audio)} bytes")
        
        # Get active contexts
        active = tts_session.get_active_contexts()
        logger.info(f"📊 Active contexts: {active}")
        
        # Cleanup
        await remove_streaming_tts_session("example_tts")
        logger.info("✅ TTS session closed")
        
        return audio_chunks
    
    except Exception as e:
        logger.error(f"TTS example failed: {e}")
        raise


# ============================================================================
# Example 3: Combined STT + TTS Flow (Conversational AI)
# ============================================================================

async def example_conversational_flow():
    """Example of complete conversational flow"""
    
    logger.info("\n🎯 ElevenLabs Conversational AI Example")
    logger.info("=" * 60)
    
    conversation_state = {
        'user_input': '',
        'agent_response': '',
        'user_audio': b'',
        'agent_audio': b'',
    }
    
    # Step 1: Create STT session to receive user input
    logger.info("Step 1️⃣: Setting up speech recognition")
    
    stt_session = await create_streaming_stt_session(
        session_id="conv_stt",
        on_commit=lambda text, meta: logger.info(f"👤 User said: {text}"),
        language_code="en",
        commit_strategy=CommitStrategy.VAD,
    )
    logger.info("✅ STT ready for user input")
    
    # Step 2: Create TTS session to send agent response
    logger.info("\nStep 2️⃣: Setting up speech synthesis")
    
    tts_session = await create_streaming_tts_session(
        session_id="conv_tts",
        default_voice_id="21m00Tcm4TlvDq8ikWAM",
    )
    
    context_id = await tts_session.initialize_context(
        text=" ",
        voice_id="21m00Tcm4TlvDq8ikWAM",
    )
    logger.info("✅ TTS ready for agent response")
    
    # Step 3: Simulate conversation
    logger.info("\nStep 3️⃣: Running simulated conversation")
    
    # Simulate LLM response streaming
    agent_response = "Thank you for calling. How can I assist you today?"
    logger.info(f"🤖 Agent: {agent_response}")
    
    # Stream response through TTS
    await tts_session.send_text(agent_response + " ", context_id=context_id)
    
    # Optional: Simulate user interruption
    logger.info("👤 User interrupts...")
    
    # Flush to get audio
    await tts_session.flush_context(context_id)
    await asyncio.sleep(1)
    
    # Get audio buffer
    audio_data = tts_session.get_context_audio_buffer(context_id)
    logger.info(f"✅ Generated {len(audio_data)} bytes of agent audio")
    
    # Cleanup
    await remove_streaming_stt_session("conv_stt")
    await remove_streaming_tts_session("conv_tts")
    logger.info("\n✅ Conversation ended, sessions cleaned up")
    
    return conversation_state


# ============================================================================
# Example 4: Error Handling
# ============================================================================

async def example_error_handling():
    """Example of error handling and recovery"""
    
    logger.info("\n⚠️ Error Handling Example")
    logger.info("=" * 60)
    
    errors_caught = []
    
    def on_error(error: str):
        logger.error(f"Session error: {error}")
        errors_caught.append(error)
    
    try:
        # Create session with error callback
        session = await create_streaming_stt_session(
            session_id="error_test",
            on_error=on_error,
        )
        
        # Simulate various scenarios
        logger.info("Testing... (errors would appear above)")
        
        await asyncio.sleep(2)
        
        await remove_streaming_stt_session("error_test")
        logger.info(f"✅ Handled {len(errors_caught)} errors gracefully")
        
    except Exception as e:
        logger.error(f"Fatal error: {e}")


# ============================================================================
# Main Runner
# ============================================================================

async def main():
    """Run all examples"""
    
    logger.info("\n" + "=" * 60)
    logger.info("ElevenLabs WebSocket Streaming Examples")
    logger.info("=" * 60)
    
    try:
        # Run STT example
        await example_streaming_stt()
        
        # Run TTS example
        await example_streaming_tts()
        
        # Run conversational example
        await example_conversational_flow()
        
        # Run error handling example
        await example_error_handling()
        
        logger.info("\n" + "=" * 60)
        logger.info("✅ All examples completed successfully!")
        logger.info("=" * 60)
    
    except Exception as e:
        logger.error(f"\n❌ Example failed: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
