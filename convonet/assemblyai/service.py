"""
AssemblyAI Streaming Speech-to-Text Service
Real-time audio transcription using AssemblyAI's WebSocket Streaming API v3.

API Reference: https://www.assemblyai.com/docs/api-reference/streaming-api/streaming-api
- Server messages: SessionBegins/Begin, Turn, Termination
- Client messages: sendAudio (binary), ForceEndpoint, Terminate
- EU server: streaming.eu.assemblyai.com
"""

import os
import json
import asyncio
import threading
from typing import Optional, Callable, Dict, List
from queue import Queue, Empty

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    print("⚠️ websockets not available for AssemblyAI")


class AssemblyAIStreamingSTT:
    """
    Real-time Speech-to-Text using AssemblyAI's Streaming API
    Bi-directional WebSocket connection for audio transcription
    """
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        sample_rate: int = 16000,
        encoding: str = "pcm_s16le",
        language: str = "en",
        speech_model: str = "universal-streaming-english",
        language_detection: bool = False,
        vad_threshold: float = 0.4,
        end_of_turn_confidence_threshold: float = 0.4,
        format_turns: bool = True,
        use_eu_server: bool = False
    ):
        """
        Initialize AssemblyAI Streaming STT service
        
        Args:
            api_key: AssemblyAI API key (reads from ASSEMBLYAI_API_KEY if not provided)
            sample_rate: Audio sample rate in Hz (default: 16000)
            encoding: Audio encoding format (pcm_s16le or pcm_mulaw)
            language: Language code (en or multi for multilingual)
            speech_model: Model to use (universal-streaming-english or universal-streaming-multilingual)
            language_detection: Enable language detection (bool)
            vad_threshold: Voice activity detection threshold (0-1)
            end_of_turn_confidence_threshold: Confidence threshold for end of turn (0-1)
            format_turns: Enable turn-based formatting
            use_eu_server: Use EU server instead of default
        """
        self.api_key = api_key or os.getenv('ASSEMBLYAI_API_KEY')
        self.sample_rate = sample_rate
        self.encoding = encoding
        self.language = language
        self.speech_model = speech_model
        self.language_detection = language_detection
        self.vad_threshold = vad_threshold
        self.end_of_turn_confidence_threshold = end_of_turn_confidence_threshold
        self.format_turns = format_turns
        
        # WebSocket URL
        server = "streaming.eu.assemblyai.com" if use_eu_server else "streaming.assemblyai.com"
        self.ws_url = f"wss://{server}/v3/ws"
        
        # Build query parameters
        self.query_params = {
            "token": self.api_key,
            "sample_rate": str(sample_rate),
            "encoding": encoding,
            "language": language,
            "speech_model": speech_model,
            "language_detection": str(language_detection).lower(),
            "vad_threshold": str(vad_threshold),
            "end_of_turn_confidence_threshold": str(end_of_turn_confidence_threshold),
            "format_turns": str(format_turns).lower()
        }
        
        # Session state
        self.session_id = None
        self.websocket = None
        self.transcript_buffer = ""
        self.current_turn = {}
        
        # Callbacks
        self.on_transcript: Optional[Callable] = None
        self.on_turn_complete: Optional[Callable] = None
        self.on_error: Optional[Callable] = None
    
    async def connect_async(self) -> bool:
        """
        Establish WebSocket connection to AssemblyAI
        
        Returns:
            True if connection successful
        """
        if not self.api_key:
            raise ValueError("ASSEMBLYAI_API_KEY not set")
        
        if not WEBSOCKETS_AVAILABLE:
            raise ImportError("websockets library required")
        
        try:
            print(f"🎙️ AssemblyAI: Connecting to streaming API (sample_rate: {self.sample_rate})...", flush=True)
            
            # Build URL with query parameters
            query_str = "&".join(f"{k}={v}" for k, v in self.query_params.items())
            url = f"{self.ws_url}?{query_str}"
            
            self.websocket = await websockets.connect(url)
            
            # Wait for session confirmation
            response = await asyncio.wait_for(self.websocket.recv(), timeout=5.0)
            message = json.loads(response)
            
            # Spec: receiveSessionBegins sends type "Begin" (AsyncAPI streaming_sessionBegins)
            msg_type = message.get("type")
            if msg_type in ("SessionBegins", "Begin"):
                self.session_id = message.get("id")
                expires_at = message.get("expires_at")
                print(f"✅ AssemblyAI: Session created: {self.session_id}", flush=True)
                print(f"   Expires at: {expires_at}", flush=True)
                return True
            else:
                print(f"❌ Unexpected response: {message}", flush=True)
                return False
                
        except Exception as e:
            print(f"❌ AssemblyAI connection failed: {type(e).__name__}: {e}", flush=True)
            raise
    
    async def send_audio_async(self, audio_bytes: bytes) -> None:
        """
        Send audio data for transcription
        
        Args:
            audio_bytes: Audio data in configured encoding
        """
        if not self.websocket:
            raise RuntimeError("Not connected to AssemblyAI")
        
        try:
            await self.websocket.send(audio_bytes)
        except Exception as e:
            print(f"❌ Error sending audio: {e}", flush=True)
            raise
    
    async def receive_transcript_async(self, timeout: float = 5.0) -> Optional[str]:
        """
        Receive transcription results
        
        Args:
            timeout: Timeout in seconds for receiving message
            
        Returns:
            Transcribed text or None if timeout
        """
        if not self.websocket:
            return None
        
        try:
            response = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
            message = json.loads(response)
            
            if message.get("type") == "Turn":
                self.current_turn = message
                transcript = message.get("transcript", "")
                utterance = message.get("utterance", "")
                end_of_turn = message.get("end_of_turn", False)
                confidence = message.get("end_of_turn_confidence", 0.0)
                
                print(f"📝 Turn {message.get('turn_order', 0)}: '{transcript}' (confidence: {confidence:.2f})", flush=True)
                
                if end_of_turn:
                    if utterance:
                        self.transcript_buffer += utterance + " "
                    if self.on_turn_complete:
                        self.on_turn_complete(message)
                    return utterance if utterance else transcript
                
                if self.on_transcript:
                    self.on_transcript(message)
                
                return transcript
            
            elif message.get("type") == "Termination":
                print(f"✅ AssemblyAI: Session terminated", flush=True)
                return None
            
            else:
                print(f"⚠️ Unknown message type: {message.get('type')}", flush=True)
                return None
                
        except asyncio.TimeoutError:
            return None
        except Exception as e:
            print(f"❌ Error receiving transcript: {e}", flush=True)
            if self.on_error:
                self.on_error(e)
            raise
    
    async def close_async(self) -> None:
        """Close the WebSocket connection"""
        if self.websocket:
            try:
                # Send termination message
                # Spec: sendSessionTermination (streaming_sessionTermination)
                terminate_msg = json.dumps({"type": "Terminate"})
                await self.websocket.send(terminate_msg)
                await self.websocket.close()
                print("✅ AssemblyAI: Connection closed", flush=True)
            except Exception as e:
                print(f"⚠️ Error closing connection: {e}", flush=True)
            finally:
                self.websocket = None
    
    def connect(self) -> bool:
        """Synchronous wrapper for connect_async"""
        try:
            return asyncio.run(self.connect_async())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return self._connect_threaded()
            raise
    
    def send_audio(self, audio_bytes: bytes) -> None:
        """Synchronous wrapper for send_audio_async"""
        try:
            asyncio.run(self.send_audio_async(audio_bytes))
        except RuntimeError:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._send_audio_threaded(audio_bytes)
            else:
                raise
    
    def receive_transcript(self, timeout: float = 5.0) -> Optional[str]:
        """Synchronous wrapper for receive_transcript_async"""
        try:
            return asyncio.run(self.receive_transcript_async(timeout))
        except RuntimeError:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                return self._receive_transcript_threaded(timeout)
            else:
                raise
    
    def close(self) -> None:
        """Synchronous wrapper for close_async"""
        try:
            asyncio.run(self.close_async())
        except RuntimeError:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                self._close_threaded()
            else:
                raise
    
    def _connect_threaded(self) -> bool:
        """Connect in separate thread"""
        result = []
        exception = []
        
        def run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                res = loop.run_until_complete(self.connect_async())
                result.append(res)
            except Exception as e:
                exception.append(e)
            finally:
                loop.close()
        
        thread = threading.Thread(target=run, daemon=False)
        thread.start()
        thread.join(timeout=10.0)
        
        if exception:
            raise exception[0]
        return result[0] if result else False
    
    def _send_audio_threaded(self, audio_bytes: bytes) -> None:
        """Send audio in separate thread"""
        exception = []
        
        def run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.send_audio_async(audio_bytes))
            except Exception as e:
                exception.append(e)
            finally:
                loop.close()
        
        thread = threading.Thread(target=run, daemon=False)
        thread.start()
        thread.join(timeout=10.0)
        
        if exception:
            raise exception[0]
    
    def _receive_transcript_threaded(self, timeout: float) -> Optional[str]:
        """Receive transcript in separate thread"""
        result = []
        exception = []
        
        def run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                res = loop.run_until_complete(self.receive_transcript_async(timeout))
                result.append(res)
            except Exception as e:
                exception.append(e)
            finally:
                loop.close()
        
        thread = threading.Thread(target=run, daemon=False)
        thread.start()
        thread.join(timeout=timeout + 5.0)
        
        if exception:
            raise exception[0]
        return result[0] if result else None
    
    def _close_threaded(self) -> None:
        """Close in separate thread"""
        exception = []
        
        def run():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.close_async())
            except Exception as e:
                exception.append(e)
            finally:
                loop.close()
        
        thread = threading.Thread(target=run, daemon=False)
        thread.start()
        thread.join(timeout=5.0)
        
        if exception:
            print(f"⚠️ Error during threaded close: {exception[0]}", flush=True)
    
    def transcribe_audio_buffer(self, audio_bytes: bytes, timeout: float = 10.0) -> str:
        """
        Transcribe a complete audio buffer
        
        Args:
            audio_bytes: Entire audio buffer (16kHz PCM s16le)
            timeout: Timeout for transcription
            
        Returns:
            Transcribed text
        """
        async def _run() -> str:
            await self.connect_async()
            try:
                # AssemblyAI requires 100-2000ms per message; 100-450ms optimal
                chunk_size = 100 * self.sample_rate * 2 // 1000  # 100ms chunks
                for i in range(0, len(audio_bytes), chunk_size):
                    chunk = audio_bytes[i:i + chunk_size]
                    await self.send_audio_async(chunk)
                # Spec: sendForceEndpoint (streaming_forceEndpoint)
                await self.websocket.send(json.dumps({"type": "ForceEndpoint"}))
                results = []
                while True:
                    transcript = await self.receive_transcript_async(timeout)
                    if transcript:
                        results.append(transcript)
                    else:
                        break
                return " ".join(results).strip()
            finally:
                await self.close_async()

        try:
            return asyncio.run(_run())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(_run())
            finally:
                loop.close()
    
    def get_session_info(self) -> Dict:
        """Get current session information"""
        return {
            "session_id": self.session_id,
            "sample_rate": self.sample_rate,
            "encoding": self.encoding,
            "language": self.language,
            "speech_model": self.speech_model,
            "current_turn": self.current_turn,
            "transcript_buffer": self.transcript_buffer
        }


# Session registry for streaming connections
_streaming_sessions: Dict[str, AssemblyAIStreamingSTT] = {}


def get_assemblyai_streaming_session(session_id: str, create_if_missing: bool = True) -> Optional[AssemblyAIStreamingSTT]:
    """
    Get or create an AssemblyAI streaming session
    
    Args:
        session_id: Unique session identifier
        create_if_missing: Create new session if not found
        
    Returns:
        AssemblyAIStreamingSTT instance or None
    """
    if session_id in _streaming_sessions:
        return _streaming_sessions[session_id]
    
    if create_if_missing:
        session = AssemblyAIStreamingSTT()
        _streaming_sessions[session_id] = session
        print(f"🎙️ Created AssemblyAI STT session: {session_id}", flush=True)
        return session
    
    return None


def remove_assemblyai_streaming_session(session_id: str) -> bool:
    """
    Remove an AssemblyAI streaming session
    
    Args:
        session_id: Session to remove
        
    Returns:
        True if session was found and removed
    """
    if session_id in _streaming_sessions:
        try:
            _streaming_sessions[session_id].close()
        except:
            pass
        del _streaming_sessions[session_id]
        print(f"✅ Removed AssemblyAI STT session: {session_id}", flush=True)
        return True
    return False


def transcribe_with_assemblyai(audio_bytes: bytes) -> str:
    """
    Convenience function for one-shot transcription
    
    Args:
        audio_bytes: Audio buffer to transcribe
        
    Returns:
        Transcribed text
    """
    service = AssemblyAIStreamingSTT()
    return service.transcribe_audio_buffer(audio_bytes)
