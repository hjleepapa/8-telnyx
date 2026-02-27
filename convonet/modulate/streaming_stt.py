"""
Modulate Velma-2 Streaming STT
Real-time transcription via WebSocket with emotion, speaker diarization, accent.

Endpoint: wss://modulate-developer-apis.com/api/velma-2-stt-streaming
Auth: api_key query parameter
Quota: 5 concurrent, 1,000 hours/month, $0.025/hour

API Docs: https://modulate-developer-apis.com/web/docs.html

Config (env):
- MODULATE_API_KEY: required
- MODULATE_STREAMING_ENABLED: default true (use streaming when Modulate selected)
"""

import os
import json
import asyncio
import threading
import logging
from typing import Optional, Callable, Dict, Any

logger = logging.getLogger(__name__)

try:
    import websockets
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    logger.warning("websockets not available for Modulate streaming STT")


MODULATE_STREAMING_URL = "wss://modulate-developer-apis.com/api/velma-2-stt-streaming"
MODULATE_STREAMING_ENABLED = os.getenv("MODULATE_STREAMING_ENABLED", "true").lower() == "true"


class ModulateStreamingSTT:
    """
    Real-time STT using Modulate Velma-2 Streaming WebSocket API.
    Supports transcription, emotion detection, speaker diarization, accent.
    """

    def __init__(
        self,
        session_id: str,
        on_partial: Optional[Callable[[str], None]] = None,
        on_final: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None,
        on_emotion: Optional[Callable[[str], None]] = None,
        language: str = "en",
        emotion_signal: bool = True,
        speaker_diarization: bool = False,
    ):
        self.session_id = session_id
        self.on_partial = on_partial or (lambda x: None)
        self.on_final = on_final or (lambda t, m: None)
        self.on_emotion = on_emotion or (lambda e: None)
        self.language = language
        self.emotion_signal = emotion_signal
        self.speaker_diarization = speaker_diarization

        self.api_key = os.getenv("MODULATE_API_KEY")
        self.websocket = None
        self.is_connected = False
        self.is_running = False

        # Audio format: PCM 16-bit, 48kHz (WebRTC standard) or 16kHz
        self.sample_rate = 48000

    def is_available(self) -> bool:
        return bool(self.api_key and WEBSOCKETS_AVAILABLE)

    async def connect(self) -> bool:
        """Establish WebSocket connection to Modulate Velma-2 Streaming."""
        if not self.api_key:
            logger.error("❌ MODULATE_API_KEY not set")
            return False
        if not WEBSOCKETS_AVAILABLE:
            logger.error("❌ websockets library required: pip install websockets")
            return False

        try:
            url = f"{MODULATE_STREAMING_URL}?api_key={self.api_key}"
            if self.language:
                url += f"&language={self.language}"
            if self.emotion_signal:
                url += "&emotion_signal=true"
            if self.speaker_diarization:
                url += "&speaker_diarization=true"

            logger.info(f"🔌 Modulate Streaming: Connecting for session {self.session_id}")

            self.websocket = await websockets.connect(
                url,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=5,
            )
            self.is_connected = True
            self.is_running = True

            # Wait for session start (if API sends one)
            try:
                first = await asyncio.wait_for(self.websocket.recv(), timeout=3.0)
                msg = json.loads(first) if isinstance(first, str) else first
                if isinstance(msg, dict):
                    logger.info(f"📡 Modulate session: {msg.get('type', msg)}")
            except asyncio.TimeoutError:
                pass  # Some APIs don't send initial message
            except Exception as e:
                logger.debug(f"Initial message: {e}")

            logger.info(f"✅ Modulate Streaming STT connected: {self.session_id}")
            return True

        except Exception as e:
            logger.error(f"❌ Modulate Streaming connection failed: {e}")
            self.is_connected = False
            return False

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Send audio chunk to Modulate. PCM 16-bit LE expected."""
        if not self.websocket or not self.is_connected:
            logger.warning("⚠️ Modulate not connected, cannot send audio")
            return

        try:
            # Common patterns: binary PCM or JSON with base64
            # Modulate protocol TBD - try binary first (like AssemblyAI)
            await self.websocket.send(audio_bytes)
        except Exception as e:
            logger.error(f"❌ Modulate send_audio failed: {e}")
            raise

    async def send_audio_json(self, audio_bytes: bytes) -> None:
        """Alternative: send as JSON with base64 audio (if API expects it)."""
        if not self.websocket or not self.is_connected:
            return

        import base64
        payload = {
            "audio": base64.b64encode(audio_bytes).decode("utf-8"),
            "sample_rate": self.sample_rate,
        }
        await self.websocket.send(json.dumps(payload))

    async def receive_message(self, timeout: float = 5.0) -> Optional[Dict[str, Any]]:
        """Receive one message from WebSocket."""
        if not self.websocket:
            return None
        try:
            msg = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
            if isinstance(msg, bytes):
                return {"raw": msg}
            return json.loads(msg)
        except asyncio.TimeoutError:
            return None
        except json.JSONDecodeError:
            logger.warning(f"Modulate: non-JSON message: {msg[:100] if msg else None}")
            return None

    def _handle_message(self, data: Dict[str, Any]) -> Optional[str]:
        """
        Parse Modulate response. Protocol may vary - adjust per actual API.
        Common fields: text, transcript, is_final, emotion, speaker.
        """
        text = data.get("text") or data.get("transcript") or data.get("output", "")
        if not text and "utterances" in data:
            utts = data["utterances"]
            text = " ".join(u.get("text", "") for u in utts if isinstance(u, dict))

        is_final = data.get("is_final", data.get("final", False))
        emotion = data.get("emotion")

        if text:
            if is_final:
                self.on_final(text, {"emotion": emotion, **data})
                return text
            else:
                self.on_partial(text)

        if emotion and self.on_emotion:
            self.on_emotion(emotion)

        return text if is_final else None

    async def close(self) -> None:
        """Close WebSocket connection."""
        self.is_running = False
        self.is_connected = False
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.debug(f"Modulate close: {e}")
            self.websocket = None
        logger.info(f"🛑 Modulate Streaming STT closed: {self.session_id}")


class ModulateStreamingSTTSession:
    """
    Thread-based Modulate streaming STT session for voice assistant.
    Same interface as StreamingSTTSession (start, send_audio, stop).
    Runs async Modulate WebSocket in a background thread.
    """

    def __init__(
        self,
        session_id: str,
        on_final_transcript: Callable[[str], None],
        on_user_speech: Optional[Callable[[], None]] = None,
        on_partial_transcript: Optional[Callable[[str, bool], None]] = None,
        language: str = "en",
    ):
        self.session_id = session_id
        self.on_final_transcript = on_final_transcript
        self.on_user_speech = on_user_speech or (lambda: None)
        self.on_partial_transcript = on_partial_transcript or (lambda t, f: None)
        self.language = language
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.audio_queue = None
        self.stop_event = threading.Event()
        self.active = threading.Event()
        self.partial_segments = []

    def start(self):
        self.thread.start()

    def send_audio(self, audio_chunk: bytes):
        """Send PCM 16-bit 48kHz chunk to Modulate."""
        if not self.audio_queue or not self.active.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(self.audio_queue.put(audio_chunk), self.loop)
        except RuntimeError:
            pass  # loop closed

    def stop(self):
        self.stop_event.set()
        if self.audio_queue:
            try:
                asyncio.run_coroutine_threadsafe(self.audio_queue.put(None), self.loop)
            except RuntimeError:
                pass  # loop already closed

    def _run_loop(self):
        if not WEBSOCKETS_AVAILABLE or not os.getenv("MODULATE_API_KEY"):
            logger.warning("Modulate streaming: websockets or MODULATE_API_KEY not available")
            return
        try:
            asyncio.set_event_loop(self.loop)
            self.audio_queue = asyncio.Queue()
            self.loop.run_until_complete(self._run_connection())
        except Exception as e:
            logger.error(f"Modulate streaming loop error: {e}")
        finally:
            try:
                self.loop.close()
            except Exception:
                pass

    async def _run_connection(self):
        api_key = os.getenv("MODULATE_API_KEY")
        url = f"{MODULATE_STREAMING_URL}?api_key={api_key}&language={self.language}&emotion_signal=true"
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                self.active.set()
                logger.info(f"Modulate streaming connected: {self.session_id}")

                async def send_task():
                    while not self.stop_event.is_set():
                        try:
                            chunk = await asyncio.wait_for(self.audio_queue.get(), timeout=1.0)
                            if chunk is None:
                                await ws.close()
                                break
                            await ws.send(chunk)
                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            logger.debug(f"Modulate send: {e}")
                            break

                async def recv_task():
                    try:
                        async for msg in ws:
                            if isinstance(msg, bytes):
                                continue
                            try:
                                data = json.loads(msg)
                                self._handle_message(data)
                            except json.JSONDecodeError:
                                pass
                    except websockets.exceptions.ConnectionClosed:
                        pass
                    except Exception as e:
                        logger.debug(f"Modulate recv: {e}")

                await asyncio.gather(send_task(), recv_task())
        except Exception as e:
            logger.error(f"Modulate streaming connection error: {e}")
        finally:
            self.active.clear()

    def _handle_message(self, data: Dict[str, Any]):
        try:
            text = data.get("text") or data.get("transcript") or ""
            if not text and "utterances" in data:
                text = " ".join(u.get("text", "") for u in data["utterances"] if isinstance(u, dict))
            is_final = data.get("is_final", data.get("final", data.get("speech_final", False)))
            if text:
                if is_final:
                    self.partial_segments.append(text.strip())
                    full = " ".join(self.partial_segments).strip()
                    self.partial_segments = []
                    if full and self.on_final_transcript:
                        self.on_final_transcript(full)
                else:
                    if self.on_partial_transcript:
                        self.on_partial_transcript(text.strip(), False)
                    if self.on_user_speech:
                        self.on_user_speech()
        except Exception as e:
            logger.debug(f"Modulate _handle_message: {e}")


# Session registry
_modulate_streaming_sessions: Dict[str, ModulateStreamingSTT] = {}


def get_modulate_streaming_session(
    session_id: str,
    on_partial: Optional[Callable[[str], None]] = None,
    on_final: Optional[Callable[[str, Optional[Dict[str, Any]]], None]] = None,
    on_emotion: Optional[Callable[[str], None]] = None,
    language: str = "en",
) -> ModulateStreamingSTT:
    """Get or create Modulate streaming STT session."""
    if session_id not in _modulate_streaming_sessions:
        _modulate_streaming_sessions[session_id] = ModulateStreamingSTT(
            session_id=session_id,
            on_partial=on_partial,
            on_final=on_final,
            on_emotion=on_emotion,
            language=language,
        )
    return _modulate_streaming_sessions[session_id]


def remove_modulate_streaming_session(session_id: str) -> None:
    """Remove Modulate streaming session. Call session.close() first if connected."""
    if session_id in _modulate_streaming_sessions:
        del _modulate_streaming_sessions[session_id]
        logger.info(f"🗑️ Removed Modulate streaming session: {session_id}")
