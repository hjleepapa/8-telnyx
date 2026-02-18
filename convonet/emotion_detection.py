"""
Emotion Detection from Text
Detects emotion from user input and agent response for voice synthesis
"""

import logging
from typing import Optional
from enum import Enum
from convonet.elevenlabs import EmotionType

logger = logging.getLogger(__name__)


class EmotionDetector:
    """Detects emotion from text using keyword analysis"""
    
    # Emotion keywords (simple but effective for hackathon)
    EMOTION_KEYWORDS = {
        EmotionType.HAPPY: [
            "happy", "great", "excellent", "wonderful", "awesome", "fantastic",
            "thanks", "thank you", "perfect", "love", "amazing", "good", "nice"
        ],
        EmotionType.SAD: [
            "sad", "unhappy", "disappointed", "sorry", "regret", "unfortunate",
            "bad", "terrible", "awful", "horrible"
        ],
        EmotionType.EXCITED: [
            "excited", "can't wait", "looking forward", "awesome", "fantastic",
            "amazing", "wow", "great", "yes", "let's go"
        ],
        EmotionType.STRESSED: [
            "stressed", "overwhelmed", "too much", "busy", "hectic", "pressure",
            "deadline", "urgent", "rush", "hurry", "frustrated", "frustrating"
        ],
        EmotionType.CALM: [
            "calm", "relaxed", "peaceful", "easy", "simple", "no rush",
            "take your time", "whenever"
        ],
        EmotionType.EMPATHETIC: [
            "sorry", "understand", "feel", "difficult", "challenging",
            "support", "help", "assist"
        ]
    }
    
    def detect_emotion(self, text: str) -> EmotionType:
        """
        Detect emotion from text using keyword matching
        
        Args:
            text: Text to analyze
            
        Returns:
            Detected emotion type
        """
        if not text:
            return EmotionType.NEUTRAL
        
        text_lower = text.lower()
        
        # Count emotion keywords
        emotion_scores = {}
        for emotion, keywords in self.EMOTION_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in text_lower)
            if score > 0:
                emotion_scores[emotion] = score
        
        # Return emotion with highest score, or neutral
        if emotion_scores:
            detected_emotion = max(emotion_scores.items(), key=lambda x: x[1])[0]
            logger.info(f"🎭 Detected emotion: {detected_emotion.value} (score: {emotion_scores[detected_emotion]})")
            return detected_emotion
        
        return EmotionType.NEUTRAL
    
    def detect_emotion_from_context(
        self,
        user_input: str,
        agent_response: str
    ) -> EmotionType:
        """
        Detect emotion from both user input and agent response context
        
        Args:
            user_input: User's input text
            agent_response: Agent's response text
            
        Returns:
            Detected emotion type
        """
        # Analyze user input for stress/frustration
        user_emotion = self.detect_emotion(user_input)
        
        # If user is stressed, respond with empathetic/calm
        if user_emotion == EmotionType.STRESSED:
            logger.info("🎭 User appears stressed, responding with empathetic tone")
            return EmotionType.EMPATHETIC
        
        # If user is sad, respond with empathetic
        if user_emotion == EmotionType.SAD:
            logger.info("🎭 User appears sad, responding with empathetic tone")
            return EmotionType.EMPATHETIC
        
        # If user is excited, match excitement
        if user_emotion == EmotionType.EXCITED:
            logger.info("🎭 User appears excited, matching excitement")
            return EmotionType.EXCITED
        
        # Analyze agent response for emotion
        response_emotion = self.detect_emotion(agent_response)
        
        # Default to response emotion or neutral
        return response_emotion if response_emotion != EmotionType.NEUTRAL else EmotionType.NEUTRAL


# Global instance
_emotion_detector = None


def get_emotion_detector() -> EmotionDetector:
    """Get global emotion detector instance"""
    global _emotion_detector
    if _emotion_detector is None:
        _emotion_detector = EmotionDetector()
    return _emotion_detector

