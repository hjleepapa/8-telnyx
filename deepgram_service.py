try:
    # Deepgram SDK v3+
    from deepgram import DeepgramClient
except ImportError:  # pragma: no cover - fallback for older SDKs
    from deepgram import Deepgram as DeepgramClient
import logging
import os
import asyncio
from typing import Optional, Dict, Any, Callable
import base64
from dotenv import load_dotenv
import tempfile
import wave
import struct
import requests
import numpy as np

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

class DeepgramService:
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Deepgram service for real-time WebRTC streaming
        
        Args:
            api_key: Deepgram API key (if None, will use DEEPGRAM_API_KEY env var)
        """
        self.api_key = api_key or os.getenv('DEEPGRAM_API_KEY')
        
        if not self.api_key:
            raise ValueError("Deepgram API key required. Set DEEPGRAM_API_KEY environment variable or pass api_key parameter.")
        
        # Initialize Deepgram client
        self.client = DeepgramClient(api_key=self.api_key)
        
        logger.info("✅ Deepgram service initialized for real-time streaming")
    
    def transcribe_audio_buffer(self, audio_buffer: bytes, language: Optional[str] = None) -> Optional[str]:
        """
        Transcribe audio buffer using Deepgram's streaming API
        
        Args:
            audio_buffer: Raw audio data bytes from WebRTC
            language: Language code. Use None or "auto" for automatic detection (default: None = auto-detect).
                      Supports 30+ languages including: en, ko, ja, es, fr, de, zh, etc.
            
        Returns:
            Transcribed text string or None if failed
        """
        # Use "auto" for automatic language detection if None is passed
        if language is None:
            language = "auto"
        try:
                   logger.info(f"🎧 Deepgram: Transcribing audio buffer: {len(audio_buffer)} bytes")
                   
                   # Check if audio is too short for meaningful speech
                   min_audio_length = 1000  # Much lower threshold for Deepgram streaming
                   if len(audio_buffer) < min_audio_length:
                       logger.warning(f"⚠️ Audio too short for transcription: {len(audio_buffer)} bytes (need {min_audio_length}+)")
                       return None
                   
                   # Detect WebM/EBML header (Opus in WebM from MediaRecorder)
                   is_webm = len(audio_buffer) >= 4 and audio_buffer[:4] == b"\x1a\x45\xdf\xa3"
                   if is_webm:
                       logger.info("🧭 Detected WebM/EBML header - sending as audio/webm to Deepgram")
                       with tempfile.NamedTemporaryFile(suffix='.webm', delete=False) as temp_file:
                           webm_path = temp_file.name
                           temp_file.write(audio_buffer)
                       try:
                           result = self._transcribe_file(webm_path, language)
                           return result
                       finally:
                           if os.path.exists(webm_path):
                               os.unlink(webm_path)
                   
                   # Analyze audio quality before PCM processing
                   audio_quality = self._analyze_audio_quality(audio_buffer)
                   logger.info(f"🔍 Audio quality analysis: {audio_quality}")
                   
                   if audio_quality.get('is_silence', False):
                       logger.warning("⚠️ Audio appears to be silence, skipping transcription")
                       return None
                   
                   if audio_quality.get('clipping_percentage', 0) > 10:
                       logger.warning(f"⚠️ Audio has severe clipping ({audio_quality.get('clipping_percentage', 0):.1f}%), may affect transcription quality")
                   
                   # Fallback: treat as raw PCM and create WAV
                   wav_file_path = self._create_wav_from_pcm(audio_buffer)
                   if not wav_file_path:
                       logger.error("❌ Failed to create WAV file from PCM data")
                       return None
                   try:
                       result = self._transcribe_file(wav_file_path, language)
                       return result
                   finally:
                       if os.path.exists(wav_file_path):
                           os.unlink(wav_file_path)
        except Exception as e:
            logger.error(f"❌ Deepgram transcription failed: {e}")
            return None
    
    def _create_wav_from_pcm(self, pcm_data: bytes) -> Optional[str]:
        """Create a WAV file from raw PCM data optimized for Deepgram"""
        try:
            # Create temporary WAV file
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
                wav_path = temp_file.name
            
            # Try different PCM configurations optimized for Deepgram
            # Start with WebRTC standard format first, then Deepgram's preferred format
            pcm_configs = [
                {"sample_rate": 48000, "channels": 1, "sample_width": 2, "desc": "48kHz, mono, 16-bit (WebRTC standard)"},
                {"sample_rate": 16000, "channels": 1, "sample_width": 2, "desc": "16kHz, mono, 16-bit (Deepgram optimized)"},
                {"sample_rate": 44100, "channels": 1, "sample_width": 2, "desc": "44.1kHz, mono, 16-bit (CD quality)"},
                {"sample_rate": 8000, "channels": 1, "sample_width": 2, "desc": "8kHz, mono, 16-bit (telephone quality)"},
            ]
            
            for config in pcm_configs:
                try:
                    logger.info(f"🔍 Trying PCM config: {config['desc']}")
                    
                    # Write WAV file with proper headers
                    with wave.open(wav_path, 'wb') as wav_file:
                        wav_file.setnchannels(config['channels'])
                        wav_file.setsampwidth(config['sample_width'])
                        wav_file.setframerate(config['sample_rate'])
                        wav_file.writeframes(pcm_data)
                    
                    # Test if the file is valid
                    with wave.open(wav_path, 'rb') as test_file:
                        frames = test_file.getnframes()
                        if frames > 0:
                            logger.info(f"✅ Created valid WAV file: {config['desc']}, {frames} frames")
                            return wav_path
                    
                except Exception as e:
                    logger.warning(f"⚠️ PCM config {config['desc']} failed: {e}")
                    continue
            
            logger.error("❌ All PCM configurations failed")
            return None
            
        except Exception as e:
            logger.error(f"❌ Failed to create WAV file: {e}")
            return None
    
    def _transcribe_file(self, file_path: str, language: Optional[str] = None) -> Optional[str]:
        """Transcribe a file using Deepgram's HTTP API"""
        try:
            logger.info(f"📤 Uploading file to Deepgram: {file_path}")
            logger.info(f"📊 File size: {os.path.getsize(file_path)} bytes")
            
            # Use Deepgram's HTTP API directly
            url = "https://api.deepgram.com/v1/listen"
            
            # Configure parameters for WebRTC audio - try without specifying encoding
            # Note: endpointing is NOT supported for batch/upload requests, only for streaming
            # Enable automatic language detection if language is "auto" or None
            use_auto_detect = language == "auto" or language is None
            
            if use_auto_detect:
                logger.info(f"🌍 Using automatic language detection (supports 30+ languages)")
            else:
                logger.info(f"🔤 Using specified language: {language}")
            
            params = {
                "model": "nova-2",  # Use Deepgram's latest model
                "smart_format": "true",  # Enable smart formatting
                "punctuate": "true",     # Add punctuation
                "alternatives": "1",     # Single alternative
                "detect_language": "true" if use_auto_detect else "false",  # Auto-detect or use specified language
                # "endpointing": "300",  # REMOVED: Not supported for batch requests (only streaming)
                "vad_events": "true",       # Voice activity detection
                "interim_results": "false"  # Final results only
            }
            
            # Only add language parameter if not using auto-detection
            if not use_auto_detect:
                params["language"] = language
            
            # Read the file
            with open(file_path, 'rb') as audio_file:
                buffer_data = audio_file.read()
            
            # Choose content type by extension
            content_type = "audio/wav"
            if file_path.lower().endswith('.webm'):
                content_type = "audio/webm"
            
            # Make the request
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": content_type
            }
            
            response = requests.post(url, params=params, headers=headers, data=buffer_data, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                logger.info(f"📋 Deepgram API Response: {result}")
                
                # Check if language was detected (in metadata)
                detected_language = None
                if result.get("metadata") and result["metadata"].get("language"):
                    detected_language = result["metadata"]["language"]
                    logger.info(f"🌍 Deepgram detected language: {detected_language}")
                
                # Extract transcription text
                if result.get("results") and result["results"].get("channels"):
                    channel = result["results"]["channels"][0]
                    logger.info(f"📊 Channel data: {channel}")
                    
                    if channel.get("alternatives"):
                        alternative = channel["alternatives"][0]
                        transcription_text = alternative.get("transcript")
                        confidence = alternative.get("confidence", 0)
                        
                        logger.info(f"📝 Raw transcript: '{transcription_text}'")
                        logger.info(f"🎯 Confidence: {confidence:.2f}")
                        if detected_language:
                            logger.info(f"🌍 Detected language: {detected_language}")
                        
                        if transcription_text and transcription_text.strip():
                            logger.info(f"✅ Deepgram transcription successful")
                            logger.info(f"📝 Text: {transcription_text}")
                            
                            return transcription_text.strip()
                        else:
                            logger.warning("⚠️ Deepgram returned empty transcription")
                            logger.warning(f"🔍 Alternative data: {alternative}")
                            return None
                    else:
                        logger.warning("⚠️ Deepgram returned no alternatives")
                        logger.warning(f"🔍 Channel data: {channel}")
                        return None
                else:
                    logger.warning("⚠️ Deepgram returned no results")
                    logger.warning(f"🔍 Full response: {result}")
                    return None
            else:
                logger.error(f"❌ Deepgram API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Deepgram file transcription failed: {e}")
            return None
    
    def _analyze_audio_quality(self, audio_buffer: bytes) -> Dict[str, Any]:
        """Analyze audio quality to help with transcription decisions"""
        try:
            # Convert bytes to numpy array for analysis
            # Assume 16-bit signed little-endian PCM
            # Ensure buffer size is even (multiple of 2 bytes for 16-bit samples)
            if len(audio_buffer) % 2 != 0:
                # Remove the last byte if odd length
                audio_buffer = audio_buffer[:-1]
                logger.debug(f"🔧 Trimmed audio buffer to even length: {len(audio_buffer)} bytes")
            
            if len(audio_buffer) == 0:
                return {"is_silence": True, "rms": 0, "clipping_percentage": 0}
            
            audio_data = np.frombuffer(audio_buffer, dtype=np.int16)
            
            # Calculate RMS (Root Mean Square) - indicates volume level
            rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
            
            # Detect clipping (values at or near maximum/minimum)
            max_val = np.max(np.abs(audio_data))
            clipping_threshold = 0.95 * 32767  # 95% of max 16-bit value
            clipped_samples = np.sum(np.abs(audio_data) > clipping_threshold)
            clipping_percentage = (clipped_samples / len(audio_data)) * 100
            
            # Detect silence (very low RMS)
            silence_threshold = 100  # Adjust based on testing
            is_silence = rms < silence_threshold
            
            # Detect noise (high frequency content without clear speech patterns)
            # Simple heuristic: check for consistent patterns vs random noise
            if len(audio_data) > 1000:
                # Calculate spectral characteristics
                fft = np.fft.fft(audio_data[:1000])  # Use first 1000 samples
                freqs = np.fft.fftfreq(1000)
                power_spectrum = np.abs(fft) ** 2
                
                # Check for speech-like frequency distribution (rough heuristic)
                speech_freq_range = (freqs >= 0.1) & (freqs <= 0.4)  # Roughly 1.6-6.4 kHz at 16kHz sample rate
                speech_power = np.sum(power_spectrum[speech_freq_range])
                total_power = np.sum(power_spectrum[freqs > 0])
                
                speech_ratio = speech_power / total_power if total_power > 0 else 0
                is_likely_speech = speech_ratio > 0.1  # At least 10% power in speech range
            else:
                is_likely_speech = True  # Assume speech for short samples
            
            return {
                "is_silence": is_silence,
                "rms": float(rms),
                "clipping_percentage": float(clipping_percentage),
                "max_amplitude": int(max_val),
                "is_likely_speech": is_likely_speech,
                "sample_count": len(audio_data)
            }
            
        except Exception as e:
            logger.warning(f"⚠️ Audio quality analysis failed: {e}")
            return {"is_silence": False, "rms": 0, "clipping_percentage": 0}
    
    def _strip_markdown_for_tts(self, text: str) -> str:
        """Strip markdown formatting from text before TTS to avoid reading 'star star' etc."""
        import re
        
        # Remove bold/italic markers (**, *, __, _)
        text = re.sub(r'\*\*([^*]+)\*\*', r'\1', text)  # **bold**
        text = re.sub(r'\*([^*]+)\*', r'\1', text)      # *italic*
        text = re.sub(r'__([^_]+)__', r'\1', text)      # __bold__
        text = re.sub(r'_([^_]+)_', r'\1', text)        # _italic_
        
        # Remove headers (# ## ### etc.)
        text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
        
        # Remove code blocks and inline code
        text = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)  # ```code blocks```
        text = re.sub(r'`([^`]+)`', r'\1', text)  # `inline code`
        
        # Remove links [text](url) -> text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # Remove horizontal rules
        text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
        
        # Remove blockquotes
        text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
        
        # Clean up extra whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = text.strip()
        
        return text

    def synthesize_speech(
        self,
        text: str,
        voice: str = "aura-asteria-en",
        model: str = None,
        encoding: Optional[str] = None,
        sample_rate: Optional[int] = None,
        container: Optional[str] = None,
    ) -> Optional[bytes]:
        """
        Synthesize speech from text using Deepgram's Aura TTS API
        
        Args:
            text: Text to convert to speech
            voice: Voice name (default: "aura-asteria-en" - natural female voice)
                   Options: aura-asteria-en, aura-luna-en, aura-stella-en, aura-athena-en, etc.
            model: Model to use (optional - Deepgram TTS doesn't require model parameter)
            
        Returns:
            Audio bytes (MP3 format) or None if failed
        """
        try:
            # Strip markdown formatting to avoid TTS reading "star star" etc.
            clean_text = self._strip_markdown_for_tts(text)
            logger.info(f"🔊 Deepgram TTS: Synthesizing speech for text: '{clean_text[:50]}...'")
            
            # Use Deepgram's TTS API
            url = "https://api.deepgram.com/v1/speak"
            
            # Configure parameters - model is not required for TTS, only voice
            params = {
                "voice": voice
            }
            # Only add model if explicitly provided (though it's usually not needed)
            if model:
                params["model"] = model
            if encoding:
                params["encoding"] = encoding
            if sample_rate:
                params["sample_rate"] = str(sample_rate)
            if container:
                params["container"] = container
            
            # Request body - use clean_text with markdown stripped
            payload = {
                "text": clean_text
            }
            
            # Make the request
            headers = {
                "Authorization": f"Token {self.api_key}",
                "Content-Type": "application/json"
            }
            
            response = requests.post(url, params=params, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                audio_bytes = response.content
                logger.info(f"✅ Deepgram TTS successful: {len(audio_bytes)} bytes")
                return audio_bytes
            else:
                logger.error(f"❌ Deepgram TTS API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Deepgram TTS synthesis failed: {e}")
            return None

# Global service instance
_deepgram_service = None

def get_deepgram_service() -> DeepgramService:
    """Get or create the global Deepgram service instance"""
    global _deepgram_service
    if _deepgram_service is None:
        _deepgram_service = DeepgramService()
    return _deepgram_service
