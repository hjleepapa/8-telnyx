"""
Inworld AI TTS Service - Real-time Text-to-Speech
WebSocket-based streaming TTS using Inworld's audio generation API
"""

import os
import asyncio
import threading
from typing import Optional, List, Dict
import json
from queue import Queue, Empty

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("⚠️ websockets not available for Inworld TTS")


class InworldTTSService:
    """
    Real-time TTS using Inworld's WebSocket API
    Supports multiple independent audio contexts and streaming text input
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        workspace: Optional[str] = None,
        character_id: Optional[str] = None,
        max_buffer_delay_ms: int = 100,
        buffer_char_threshold: int = 100,
        auto_mode: bool = True
    ):
        """
        Initialize Inworld TTS service
        
        Args:
            api_key: Inworld API key (reads from INWORLD_API_KEY if not provided)
            workspace: Workspace ID
            character_id: Character ID to use
            max_buffer_delay_ms: Maximum buffer delay in milliseconds
            buffer_char_threshold: Character threshold for automatic buffer flushing
            auto_mode: Enable auto mode for latency/quality balance
        """
        self.api_key = api_key or os.getenv('INWORLD_API_KEY')
        self.workspace = workspace or os.getenv('INWORLD_WORKSPACE')
        self.character_id = character_id or os.getenv('INWORLD_CHARACTER_ID')
        
        self.max_buffer_delay_ms = max_buffer_delay_ms
        self.buffer_char_threshold = buffer_char_threshold
        self.auto_mode = auto_mode
        
        # WebSocket URL for Inworld audio generation
        self.ws_url = "wss://api.inworld.ai/api/v1/ws/synthesize"
        
        # Active contexts: {context_id: audio_accumulator}
        self.contexts: Dict[str, bytes] = {}
        self.current_context_id = "default"
        
        # Audio output buffer
        self.audio_data = b''
        self.output_queue: Queue = Queue()
    
    async def _synthesize_async(self, text: str, context_id: Optional[str] = None) -> bytes:
        """
        Synthesize speech from text using WebSocket
        
        Args:
            text: Text to synthesize
            context_id: Optional context ID (creates new if not provided)
            
        Returns:
            Audio bytes
        """
        if not self.api_key:
            raise ValueError("INWORLD_API_KEY not set")
        
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError("websockets library required")
        
        context_id = context_id or self.current_context_id
        audio_data = b''
        
        try:
            print(f"🎵 Inworld TTS: Connecting to WebSocket (context: {context_id})...", flush=True)
            
            # Inworld TTS uses Basic auth: Authorization: Basic <base64(key:secret)>
            # The "Basic" value from Inworld Portal is the pre-encoded base64 string
            headers = {
                "Authorization": f"Basic {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with websockets.connect(self.ws_url, additional_headers=headers) as websocket:
                # Create context with configuration
                context_config = {
                    "type": "create_context",
                    "contextId": context_id,
                    "config": {
                        "maxBufferDelayMs": self.max_buffer_delay_ms,
                        "bufferCharThreshold": self.buffer_char_threshold,
                        "autoMode": self.auto_mode
                    }
                }
                print(f"📤 Creating context: {context_id}", flush=True)
                await websocket.send(json.dumps(context_config))
                
                # Send text for synthesis
                synthesis_request = {
                    "type": "synthesize",
                    "contextId": context_id,
                    "text": text
                }
                print(f"📤 Sending text for synthesis: {text[:50]}...", flush=True)
                await websocket.send(json.dumps(synthesis_request))
                
                # Receive audio chunks
                print("📥 Receiving audio from Inworld", flush=True)
                while True:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=5.0)
                        
                        # Parse message
                        try:
                            data = json.loads(message)
                        except json.JSONDecodeError:
                            # Binary audio data
                            audio_data += message
                            print(f"📥 Received audio chunk: {len(message)} bytes", flush=True)
                            continue
                        
                        if data.get("type") == "audio":
                            # Audio data in JSON format (base64 encoded)
                            if "audio" in data:
                                import base64
                                audio_chunk = base64.b64decode(data["audio"])
                                audio_data += audio_chunk
                                print(f"📥 Received audio chunk: {len(audio_chunk)} bytes", flush=True)
                        
                        elif data.get("type") == "context_closed":
                            print("✅ Context closed", flush=True)
                            break
                        
                        elif data.get("type") == "error":
                            error_msg = data.get("error", "Unknown error")
                            print(f"❌ Error from Inworld: {error_msg}", flush=True)
                            break
                    
                    except asyncio.TimeoutError:
                        print("✅ Inworld TTS stream complete (timeout)", flush=True)
                        break
                    except Exception as recv_error:
                        print(f"❌ Error receiving audio: {recv_error}", flush=True)
                        break
                
                # Close context
                close_request = {
                    "type": "close_context",
                    "contextId": context_id
                }
                await websocket.send(json.dumps(close_request))
            
            # Inworld returns PCM Float32 [-1,1]; convert to PCM 16-bit LE for LiveKit
            if audio_data and len(audio_data) >= 4 and len(audio_data) % 4 == 0:
                try:
                    import numpy as np
                    floats = np.frombuffer(audio_data, dtype=np.float32)
                    # Only convert if values look like normalized float32 audio (not already int16)
                    if len(floats) > 0 and np.max(np.abs(floats)) <= 1.5:
                        int16_audio = (np.clip(floats, -1.0, 1.0) * 32767).astype(np.int16)
                        audio_data = int16_audio.tobytes()
                        print(f"✅ Inworld TTS: Converted Float32 to PCM16 ({len(audio_data)} bytes)", flush=True)
                except Exception as conv_err:
                    print(f"⚠️ Inworld Float32->PCM16 conversion failed: {conv_err}", flush=True)
            
            print(f"✅ Inworld TTS synthesis complete: {len(audio_data)} bytes", flush=True)
            return audio_data
            
        except Exception as e:
            print(f"❌ Inworld TTS error: {type(e).__name__}: {e}", flush=True)
            raise
    
    def synthesize(self, text: str, context_id: Optional[str] = None) -> bytes:
        """
        Synthesize speech from text (synchronous wrapper)
        
        Args:
            text: Text to synthesize
            context_id: Optional context ID
            
        Returns:
            Audio bytes
        """
        if context_id:
            self.current_context_id = context_id
        
        print(f"🎤 Inworld TTS: Synthesizing '{text[:50]}...'", flush=True)
        
        try:
            # Run async synthesis in event loop
            audio = asyncio.run(self._synthesize_async(text, context_id))
            return audio
        except RuntimeError as e:
            # Handle case where event loop already exists (Flask context)
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Use thread-based approach if loop is already running
                    audio = self._synthesize_threaded(text, context_id)
                    return audio
            except:
                pass
            raise
    
    def _synthesize_threaded(self, text: str, context_id: Optional[str] = None) -> bytes:
        """
        Synthesize speech in a separate thread (for when event loop is running)
        
        Args:
            text: Text to synthesize
            context_id: Optional context ID
            
        Returns:
            Audio bytes
        """
        audio_result = []
        exception_result = []
        
        def run_in_new_loop():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                audio = loop.run_until_complete(self._synthesize_async(text, context_id))
                audio_result.append(audio)
            except Exception as e:
                exception_result.append(e)
            finally:
                loop.close()
        
        thread = threading.Thread(target=run_in_new_loop, daemon=False)
        thread.start()
        thread.join(timeout=60.0)  # 60 second timeout
        
        if exception_result:
            raise exception_result[0]
        
        if not audio_result:
            raise RuntimeError("Inworld TTS synthesis timeout or failed")
        
        return audio_result[0]
    
    def create_context(self, context_id: str) -> bool:
        """
        Create a new audio context
        
        Args:
            context_id: Unique context identifier
            
        Returns:
            True if successful
        """
        if context_id not in self.contexts:
            self.contexts[context_id] = b''
            print(f"✅ Created Inworld context: {context_id}", flush=True)
            return True
        return False
    
    def close_context(self, context_id: str) -> bool:
        """
        Close an audio context
        
        Args:
            context_id: Context to close
            
        Returns:
            True if successful
        """
        if context_id in self.contexts:
            del self.contexts[context_id]
            print(f"✅ Closed Inworld context: {context_id}", flush=True)
            return True
        return False
    
    def get_contexts(self) -> List[str]:
        """Get list of active contexts"""
        return list(self.contexts.keys())


# Singleton instance
_inworld_service: Optional[InworldTTSService] = None


def get_inworld_service(context_id: str = "default") -> InworldTTSService:
    """Get or create Inworld TTS service instance"""
    global _inworld_service
    
    if _inworld_service is None:
        _inworld_service = InworldTTSService()
        _inworld_service.create_context(context_id)
    elif context_id not in _inworld_service.get_contexts():
        _inworld_service.create_context(context_id)
    
    return _inworld_service


# REST API wrapper (for use in routes)
def inworld_tts_synthesize(text: str, context_id: str = "default") -> bytes:
    """
    Convenience function to synthesize speech
    
    Args:
        text: Text to synthesize
        context_id: Context to use
        
    Returns:
        Audio bytes
    """
    service = get_inworld_service(context_id)
    return service.synthesize(text, context_id=context_id)
