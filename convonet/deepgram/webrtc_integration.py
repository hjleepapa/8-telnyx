import os
import logging
from typing import Dict, Any, Optional
from .service import get_deepgram_service

logger = logging.getLogger(__name__)

def transcribe_audio_with_deepgram_webrtc(audio_buffer: bytes, language: str = "en") -> Optional[str]:
    """
    Transcribe audio buffer using Deepgram, specifically for WebRTC chunks.
    
    Args:
        audio_buffer: Raw audio data bytes from WebRTC.
        language: Language code (default: "en").
        
    Returns:
        Transcribed text string or None if failed.
    """
    try:
        service = get_deepgram_service()
        
        # Deepgram is designed for real-time streaming and handles WebRTC chunks well
        # It can process smaller audio buffers more effectively than AssemblyAI
        transcribed_text = service.transcribe_audio_buffer(audio_buffer, language)
        
        if transcribed_text:
            logger.info(f"✅ Deepgram WebRTC transcription successful: {transcribed_text}")
            return transcribed_text
        else:
            logger.warning("⚠️ Deepgram WebRTC transcription returned empty text.")
            return None
            
    except Exception as e:
        logger.error(f"❌ Deepgram WebRTC transcription failed: {e}")
        return None

def get_deepgram_webrtc_info() -> Dict[str, Any]:
    """Get information about Deepgram service for WebRTC."""
    return {
        'transcriber': 'deepgram',
        'model': 'nova-2',  # Deepgram's latest model
        'accuracy': '95%+',  # Based on Deepgram's claims and benchmarks
        'cost_per_minute': 0.0043,  # Deepgram's pricing for nova-2 model
        'privacy': 'processed on Deepgram servers',  # Data is sent to Deepgram servers
        'latency': '200-500ms',  # Much faster than AssemblyAI for real-time
        'webrtc_ready': True,  # Deepgram is optimized for WebRTC
        'production_ready': True,
        'api_key_configured': bool(os.getenv('DEEPGRAM_API_KEY')),
        'streaming_optimized': True,  # Deepgram excels at real-time streaming
        'webRTC_native': True  # Native WebRTC support
    }
