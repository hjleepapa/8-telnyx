"""
Cartesia Streaming STT Service
Real-time speech-to-text using Cartesia's WebSocket API with Redis buffering
Similar pattern to Deepgram's StreamingSTTSession but for Cartesia Ink-Whisper
"""

import os
import json
import logging
import asyncio
import websockets
import threading
from typing import Optional, Callable, Dict, Any
from queue import Queue
from datetime import datetime
import base64
from urllib.parse import quote

logger = logging.getLogger(__name__)

try:
    from cartesia import Cartesia
    CARTESIA_SDK_AVAILABLE = True
except ImportError:
    CARTESIA_SDK_AVAILABLE = False
    logger.warning("Cartesia SDK not available. Install with: pip install cartesia")

try:
    from ..redis_manager import redis_manager
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    logger.warning("Redis not available")


class CartesiaStreamingSTT:
    """
    Real-time STT using Cartesia's Streaming WebSocket API
    Buffers audio in Redis, streams to Cartesia, handles partial/final transcriptions
    
    Similar to Deepgram's StreamingSTTSession but uses Cartesia Ink-Whisper model
    """
    
    def __init__(
        self,
        session_id: str,
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str], None]] = None,
        on_user_speech: Optional[Callable[[], None]] = None,
        language: str = "en"
    ):
        """
        Initialize Cartesia streaming STT session
        
        Args:
            session_id: Unique session identifier
            on_partial: Callback for partial transcriptions
            on_final: Callback for final transcriptions
            on_user_speech: Callback when user voice detected
            language: Language code (en, es, fr, etc.)
        """
        self.session_id = session_id
        self.on_partial = on_partial or (lambda x: None)
        self.on_final = on_final or (lambda x: None)
        self.on_user_speech = on_user_speech or (lambda: None)
        self.language = language
        
        # API configuration
        self.api_key = os.getenv('CARTESIA_API_KEY')
        self.stt_model = "ink-whisper"  # Cartesia's latest STT model
        self.language_code = language  # ISO 639-1 code
        
        # Streaming state
        self.is_running = False
        self.websocket = None
        self.audio_queue: Queue = Queue()
        self.buffer_bytes = bytearray()
        
        # Threading
        self.thread = None
        self.stop_event = threading.Event()
        self.ready_event = threading.Event()
        
        # Audio buffering (for VAD and context)
        self.chunk_index = 0
        self.silence_counter = 0
        self.silence_threshold = 100  # RMS threshold for silence
        
    def start(self):
        """Start the streaming STT session"""
        if not self.api_key:
            logger.error("❌ Cartesia API key not set")
            return False
        
        logger.info(f"🎤 Starting Cartesia Streaming STT for session {self.session_id}")
        
        self.is_running = True
        self.stop_event.clear()
        
        # Start streaming thread
        self.thread = threading.Thread(target=self._run_streaming_loop, daemon=True)
        self.thread.start()
        
        # Wait for connection ready
        if not self.ready_event.wait(timeout=5.0):
            logger.warning("⚠️ Cartesia streaming connection timeout")
            return False
        
        logger.info("✅ Cartesia Streaming STT ready")
        return True
    
    def send_audio_chunk(self, audio_chunk: bytes):
        """
        Send audio chunk to streaming STT
        Audio should be PCM 16-bit LE, 16kHz or 48kHz
        
        Args:
            audio_chunk: Raw audio bytes
        """
        if not self.is_running:
            logger.warning("⚠️ Streaming STT not running, cannot send audio")
            return
        
        # Queue the chunk for processing
        self.audio_queue.put(audio_chunk)
        logger.debug(f"📨 Queued audio chunk: {len(audio_chunk)} bytes")
    
    def stop(self):
        """Stop the streaming STT session"""
        logger.info(f"🛑 Stopping Cartesia Streaming STT for session {self.session_id}")
        
        self.is_running = False
        self.stop_event.set()
        
        # Send stop signal to queue
        self.audio_queue.put(None)
        
        # Wait for thread to finish
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=2.0)
        
        logger.info("✅ Cartesia Streaming STT stopped")
    
    def _run_streaming_loop(self):
        """Main streaming loop - runs in separate thread"""
        try:
            # Use asyncio for WebSocket communication
            asyncio.run(self._async_streaming_loop())
        except Exception as e:
            logger.error(f"❌ Streaming loop error: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.is_running = False
    
    async def _async_streaming_loop(self):
        """Async WebSocket streaming to Cartesia"""
        try:
            # Connect to Cartesia WebSocket endpoint with URL-encoded API key
            encoded_key = quote(self.api_key, safe='')
            websocket_url = f"wss://api.cartesia.ai/stt/websocket?api_key={encoded_key}"
            
            logger.debug(f"🔗 Connecting to Cartesia WebSocket: {websocket_url[:50]}...")
            
            try:
                async with websockets.connect(websocket_url) as websocket:
                    self.websocket = websocket
                    
                    # Send initialization message with model and language
                    init_message = {
                        "type": "init",
                        "model": self.stt_model,
                        "language": self.language_code,
                        "sample_rate": 16000,  # Request 16kHz for optimal STT
                        "encoding": "pcm_s16le",
                    }
                    
                    await websocket.send(json.dumps(init_message))
                    logger.info("📤 Sent Cartesia init message")
                    
                    # Confirm ready
                    self.ready_event.set()
                    
                    # Start receiving messages in background
                    receive_task = asyncio.create_task(self._receive_messages(websocket))
                    
                    # Send audio chunks from queue
                    send_task = asyncio.create_task(self._send_audio_chunks(websocket))
                    
                    # Wait for tasks
                    await asyncio.gather(receive_task, send_task)
            except Exception as ws_error:
                logger.error(f"❌ WebSocket connection error: {type(ws_error).__name__}: {ws_error}")
                raise
                
        except Exception as e:
            logger.error(f"❌ WebSocket error: {e}")
            self.ready_event.set()  # Unblock caller
        finally:
            self.websocket = None
    
    async def _send_audio_chunks(self, websocket):
        """Send audio chunks from queue to WebSocket"""
        try:
            while self.is_running and self.websocket:
                try:
                    # Get chunk with timeout (non-blocking)
                    chunk = self.audio_queue.get(timeout=0.5)
                    
                    if chunk is None:  # Stop signal
                        logger.info("🛑 Received stop signal")
                        
                        # Send finalize message
                        finalize_msg = {"type": "finalize"}
                        await websocket.send(json.dumps(finalize_msg))
                        break
                    
                    # Accumulate bytes for VAD
                    self.buffer_bytes.extend(chunk)
                    
                    # Detect voice activity (simple RMS check)
                    if self._has_voice_activity(chunk):
                        self.silence_counter = 0
                        if self.silence_counter == 0:  # Transition from silence to speech
                            logger.info("🎤 User speech detected")
                            self.on_user_speech()
                    else:
                        self.silence_counter += 1
                    
                    # Send audio message
                    audio_message = {
                        "type": "audio",
                        "data": base64.b64encode(chunk).decode('utf-8'),
                    }
                    await websocket.send(json.dumps(audio_message))
                    self.chunk_index += 1
                    
                except TimeoutError:
                    # Queue timeout - continue waiting for audio
                    continue
                    
        except Exception as e:
            logger.error(f"❌ Error sending audio: {e}")
    
    async def _receive_messages(self, websocket):
        """Receive transcription messages from WebSocket"""
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    msg_type = data.get("type")
                    
                    if msg_type == "partial":
                        # Partial transcription (interim results)
                        partial_text = data.get("text", "")
                        if partial_text:
                            logger.debug(f"🔤 Partial: {partial_text}")
                            self.on_partial(partial_text)
                    
                    elif msg_type == "final":
                        # Final transcription (confirmed)
                        final_text = data.get("text", "")
                        if final_text:
                            logger.info(f"✅ Final: {final_text}")
                            self.on_final(final_text)
                    
                    elif msg_type == "error":
                        error_msg = data.get("error", "Unknown error")
                        logger.error(f"❌ Cartesia error: {error_msg}")
                    
                except json.JSONDecodeError:
                    logger.warning(f"⚠️ Invalid JSON from Cartesia: {message}")
                    
        except Exception as e:
            logger.error(f"❌ Error receiving messages: {e}")
    
    def _has_voice_activity(self, audio_chunk: bytes) -> bool:
        """
        Simple voice activity detection using RMS
        
        Args:
            audio_chunk: PCM 16-bit audio bytes
            
        Returns:
            True if voice detected, False if silence
        """
        try:
            import numpy as np
            
            # Ensure even length
            if len(audio_chunk) % 2 != 0:
                audio_chunk = audio_chunk[:-1]
            
            if len(audio_chunk) == 0:
                return False
            
            # Convert to int16 array
            audio_data = np.frombuffer(audio_chunk, dtype=np.int16)
            
            # Calculate RMS (Root Mean Square)
            rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
            
            # Simple threshold
            is_voice = rms > self.silence_threshold
            
            return is_voice
            
        except Exception as e:
            logger.warning(f"⚠️ VAD analysis failed: {e}")
            return True  # Assume voice on error - safer approach


# Global streaming sessions store
_cartesia_streaming_sessions: Dict[str, CartesiaStreamingSTT] = {}


def get_cartesia_streaming_session(
    session_id: str,
    on_partial: Optional[Callable[[str], None]] = None,
    on_final: Optional[Callable[[str], None]] = None,
    on_user_speech: Optional[Callable[[], None]] = None,
    language: str = "en"
) -> CartesiaStreamingSTT:
    """Get or create Cartesia streaming STT session"""
    
    if session_id not in _cartesia_streaming_sessions:
        session = CartesiaStreamingSTT(
            session_id=session_id,
            on_partial=on_partial,
            on_final=on_final,
            on_user_speech=on_user_speech,
            language=language
        )
        _cartesia_streaming_sessions[session_id] = session
    
    return _cartesia_streaming_sessions[session_id]


def remove_cartesia_streaming_session(session_id: str):
    """Remove and cleanup Cartesia streaming session"""
    if session_id in _cartesia_streaming_sessions:
        session = _cartesia_streaming_sessions[session_id]
        session.stop()
        del _cartesia_streaming_sessions[session_id]
        logger.info(f"🗑️ Removed Cartesia streaming session: {session_id}")
