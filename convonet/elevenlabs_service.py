"""
ElevenLabs TTS Service
Provides voice cloning, emotional responses, and multilingual support
"""

import os
import logging
from typing import Optional, Dict, Any, List
from enum import Enum

logger = logging.getLogger(__name__)

try:
    from elevenlabs import ElevenLabs
    from elevenlabs import VoiceSettings
    ELEVENLABS_AVAILABLE = True
except ImportError:
    ELEVENLABS_AVAILABLE = False
    logger.warning("ElevenLabs SDK not available. Install with: pip install elevenlabs")


class EmotionType(str, Enum):
    """Emotion types for voice synthesis"""
    HAPPY = "happy"
    SAD = "sad"
    EXCITED = "excited"
    CALM = "calm"
    STRESSED = "stressed"
    EMPATHETIC = "empathetic"
    PROFESSIONAL = "professional"
    CASUAL = "casual"
    NEUTRAL = "neutral"


class ElevenLabsService:
    """Service for ElevenLabs TTS with voice cloning, emotions, and multilingual support"""
    
    def __init__(self, api_key: Optional[str] = None):
        if not ELEVENLABS_AVAILABLE:
            raise ImportError("ElevenLabs SDK not available. Install with: pip install elevenlabs")
        
        self.api_key = api_key or os.getenv('ELEVENLABS_API_KEY')
        if not self.api_key:
            logger.warning("⚠️ ELEVENLABS_API_KEY not set. ElevenLabs TTS will not work.")
            self.client = None
        else:
            self.client = ElevenLabs(api_key=self.api_key)
        
        # Default voice settings
        self.default_voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel (default ElevenLabs voice)
        self.default_model = "eleven_multilingual_v2"  # Supports 29+ languages
        
        # Emotion-based voice settings
        self.emotion_settings = {
            EmotionType.HAPPY: {
                "stability": 0.3,
                "similarity_boost": 0.8,
                "style": 0.5,
                "use_speaker_boost": True
            },
            EmotionType.SAD: {
                "stability": 0.7,
                "similarity_boost": 0.6,
                "style": 0.3,
                "use_speaker_boost": False
            },
            EmotionType.EXCITED: {
                "stability": 0.2,
                "similarity_boost": 0.9,
                "style": 0.8,
                "use_speaker_boost": True
            },
            EmotionType.CALM: {
                "stability": 0.6,
                "similarity_boost": 0.7,
                "style": 0.2,
                "use_speaker_boost": True
            },
            EmotionType.STRESSED: {
                "stability": 0.5,
                "similarity_boost": 0.7,
                "style": 0.4,
                "use_speaker_boost": False
            },
            EmotionType.EMPATHETIC: {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.3,
                "use_speaker_boost": True
            },
            EmotionType.PROFESSIONAL: {
                "stability": 0.7,
                "similarity_boost": 0.8,
                "style": 0.1,
                "use_speaker_boost": True
            },
            EmotionType.CASUAL: {
                "stability": 0.4,
                "similarity_boost": 0.7,
                "style": 0.6,
                "use_speaker_boost": True
            },
            EmotionType.NEUTRAL: {
                "stability": 0.5,
                "similarity_boost": 0.75,
                "style": 0.5,
                "use_speaker_boost": True
            }
        }
    
    def is_available(self) -> bool:
        """Check if ElevenLabs service is available"""
        return ELEVENLABS_AVAILABLE and self.client is not None
    
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
        text = re.sub(r'```[^`]*```', '', text, flags=re.DOTALL)
        text = re.sub(r'`([^`]+)`', r'\1', text)
        
        # Remove links [text](url) -> text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        # Remove horizontal rules
        text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
        
        # Remove blockquotes
        text = re.sub(r'^>\s+', '', text, flags=re.MULTILINE)
        
        # Clean up extra whitespace
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()
    
    def synthesize(
        self,
        text: str,
        voice_id: Optional[str] = None,
        model: Optional[str] = None,
        stability: float = 0.5,
        similarity_boost: float = 0.75,
        style: float = 0.5,
        use_speaker_boost: bool = True
    ) -> Optional[bytes]:
        """
        Synthesize speech from text using ElevenLabs TTS
        
        Args:
            text: Text to convert to speech
            voice_id: Voice ID (default: Rachel)
            model: Model to use (default: eleven_multilingual_v2)
            stability: Voice stability (0.0-1.0)
            similarity_boost: Similarity boost (0.0-1.0)
            style: Style setting (0.0-1.0)
            use_speaker_boost: Enable speaker boost
            
        Returns:
            Audio bytes (MP3 format) or None if failed
        """
        if not self.is_available():
            logger.error("❌ ElevenLabs service not available")
            return None
        
        try:
            # Strip markdown formatting to avoid TTS reading "star star" etc.
            clean_text = self._strip_markdown_for_tts(text)
            logger.info(f"🔊 ElevenLabs TTS: Synthesizing speech for text: '{clean_text[:50]}...'")
            
            voice_id = voice_id or self.default_voice_id
            model = model or self.default_model
            
            # Create voice settings
            voice_settings = VoiceSettings(
                stability=stability,
                similarity_boost=similarity_boost,
                style=style,
                use_speaker_boost=use_speaker_boost
            )
            
            # Generate audio using ElevenLabs SDK
            # The ElevenLabs SDK v1.0+ uses text_to_speech.convert() method
            # Check available methods and use the correct one
            audio_generator = None
            
            # Method 1: text_to_speech.convert() (SDK v1.0+ - most common)
            if hasattr(self.client, 'text_to_speech') and hasattr(self.client.text_to_speech, 'convert'):
                logger.info("🔊 Using ElevenLabs text_to_speech.convert() method")
                audio_generator = self.client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=clean_text,
                    model_id=model,
                    voice_settings=voice_settings
                )
            # Method 2: Direct convert() (alternative structure)
            elif hasattr(self.client, 'convert'):
                logger.info("🔊 Using ElevenLabs convert() method")
                audio_generator = self.client.convert(
                    voice_id=voice_id,
                    text=clean_text,
                    model_id=model,
                    voice_settings=voice_settings
                )
            # Method 3: Legacy generate() method (older SDK versions)
            elif hasattr(self.client, 'generate'):
                logger.info("🔊 Using ElevenLabs generate() method")
                audio_generator = self.client.generate(
                    text=clean_text,
                    voice=voice_id,
                    model=model,
                    voice_settings=voice_settings
                )
            else:
                # Log available methods for debugging
                available_methods = [m for m in dir(self.client) if not m.startswith('_')]
                logger.error(f"❌ No suitable TTS method found. Available methods: {available_methods}")
                raise AttributeError(f"ElevenLabs client has no TTS method. Available: {available_methods}")
            
            # Convert generator to bytes
            audio_bytes = b"".join(audio_generator)
            
            logger.info(f"✅ ElevenLabs TTS successful: {len(audio_bytes)} bytes")
            return audio_bytes
            
        except Exception as e:
            logger.error(f"❌ ElevenLabs TTS synthesis failed: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def synthesize_with_emotion(
        self,
        text: str,
        emotion: EmotionType,
        voice_id: Optional[str] = None,
        model: Optional[str] = None
    ) -> Optional[bytes]:
        """
        Synthesize speech with emotional tone
        
        Args:
            text: Text to convert to speech
            emotion: Emotion type
            voice_id: Voice ID (default: Rachel)
            model: Model to use (default: eleven_multilingual_v2)
            
        Returns:
            Audio bytes (MP3 format) or None if failed
        """
        if not self.is_available():
            return None
        
        # Get emotion settings
        settings = self.emotion_settings.get(emotion, self.emotion_settings[EmotionType.NEUTRAL])
        
        logger.info(f"🎭 ElevenLabs TTS with emotion '{emotion.value}': '{text[:50]}...'")
        
        return self.synthesize(
            text=text,
            voice_id=voice_id,
            model=model,
            **settings
        )
    
    def synthesize_multilingual(
        self,
        text: str,
        language: str = "en",
        voice_id: Optional[str] = None,
        model: Optional[str] = None
    ) -> Optional[bytes]:
        """
        Synthesize speech in specified language
        
        Args:
            text: Text to convert to speech
            language: Language code (en, es, fr, de, ja, etc.)
            voice_id: Voice ID (default: Rachel)
            model: Model to use (default: eleven_multilingual_v2)
            
        Returns:
            Audio bytes (MP3 format) or None if failed
        """
        if not self.is_available():
            return None
        
        # Use multilingual model
        model = model or self.default_model
        
        logger.info(f"🌍 ElevenLabs TTS in {language}: '{text[:50]}...'")
        
        return self.synthesize(
            text=text,
            voice_id=voice_id,
            model=model
        )
    
    def clone_voice(
        self,
        audio_samples: List[bytes],
        voice_name: str,
        description: Optional[str] = None
    ) -> Optional[str]:
        """
        Clone a voice from audio samples
        
        Args:
            audio_samples: List of audio bytes (at least 1 minute total)
            voice_name: Name for the cloned voice
            description: Optional description
            
        Returns:
            Voice ID if successful, None otherwise
        """
        if not self.is_available():
            logger.error("❌ ElevenLabs service not available")
            return None
        
        try:
            logger.info(f"🎤 Cloning voice '{voice_name}' from {len(audio_samples)} samples...")
            
            # Clone voice using the voices.clone method
            # Note: The API may require file paths or file-like objects
            # For now, we'll save to temp files if needed
            import tempfile
            import os
            
            temp_files = []
            try:
                # Save audio samples to temporary files
                for i, audio_bytes in enumerate(audio_samples):
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.mp3')
                    temp_file.write(audio_bytes)
                    temp_file.close()
                    temp_files.append(temp_file.name)
                
                # Clone voice using file paths
                voice = self.client.voices.clone(
                    name=voice_name,
                    description=description or f"Cloned voice: {voice_name}",
                    files=temp_files
                )
                
                voice_id = voice.voice_id
                logger.info(f"✅ Voice cloned successfully: {voice_id}")
                return voice_id
                
            finally:
                # Clean up temporary files
                for temp_file in temp_files:
                    try:
                        os.unlink(temp_file)
                    except:
                        pass
            
        except Exception as e:
            logger.error(f"❌ Voice cloning failed: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def get_voice(self, voice_id: str) -> Optional[Dict[str, Any]]:
        """Get voice information by ID"""
        if not self.is_available():
            return None
        
        try:
            voice = self.client.voices.get(voice_id)
            return {
                "voice_id": voice.voice_id,
                "name": voice.name,
                "category": voice.category,
                "description": getattr(voice, 'description', None)
            }
        except Exception as e:
            logger.error(f"❌ Failed to get voice: {e}")
            return None
    
    def list_voices(self) -> List[Dict[str, Any]]:
        """List all available voices"""
        if not self.is_available():
            return []
        
        try:
            voices = self.client.voices.get_all()
            return [
                {
                    "voice_id": voice.voice_id,
                    "name": voice.name,
                    "category": voice.category,
                    "description": getattr(voice, 'description', None)
                }
                for voice in voices.voices
            ]
        except Exception as e:
            logger.error(f"❌ Failed to list voices: {e}")
            return []
    
    def synthesize_with_style(
        self,
        text: str,
        style: str = "conversational",
        voice_id: Optional[str] = None,
        model: Optional[str] = None
    ) -> Optional[bytes]:
        """
        Synthesize speech with specific style
        
        Args:
            text: Text to convert to speech
            style: Style (conversational, professional, casual, formal)
            voice_id: Voice ID (default: Rachel)
            model: Model to use (default: eleven_multilingual_v2)
            
        Returns:
            Audio bytes (MP3 format) or None if failed
        """
        if not self.is_available():
            return None
        
        # Map style to emotion settings
        style_to_emotion = {
            "conversational": EmotionType.CASUAL,
            "professional": EmotionType.PROFESSIONAL,
            "casual": EmotionType.CASUAL,
            "formal": EmotionType.PROFESSIONAL
        }
        
        emotion = style_to_emotion.get(style.lower(), EmotionType.NEUTRAL)
        return self.synthesize_with_emotion(text, emotion, voice_id, model)


# Global service instance
_elevenlabs_service = None


def get_elevenlabs_service() -> ElevenLabsService:
    """Get or create the global ElevenLabs service instance"""
    global _elevenlabs_service
    if _elevenlabs_service is None:
        _elevenlabs_service = ElevenLabsService()
    return _elevenlabs_service

