import asyncio
import threading
import time
import uuid
from typing import Optional

import jwt

try:
    from livekit import rtc
    LIVEKIT_AVAILABLE = True
except Exception as e:
    print(f"⚠️ LiveKit SDK not available: {e}")
    LIVEKIT_AVAILABLE = False


def generate_livekit_token(api_key: str, api_secret: str, identity: str, room: str, ttl_seconds: int = 3600) -> str:
    now = int(time.time())
    payload = {
        "jti": str(uuid.uuid4()),
        "iss": api_key,
        "sub": identity,
        "nbf": now,
        "exp": now + ttl_seconds,
        "video": {
            "room": room,
            "roomJoin": True,
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
        },
    }
    return jwt.encode(payload, api_secret, algorithm="HS256")


class LiveKitRoomSession:
    def __init__(self, url: str, token: str, sample_rate: int = 24000, channels: int = 1):
        self.url = url
        self.token = token
        self.sample_rate = sample_rate
        self.channels = channels
        self.recording_enabled = False
        self.input_buffer = bytearray()
        self.audio_source = None
        self.room = None
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.ready = threading.Event()
        self._closed = False
        self._frame_count = 0
        self._frame_debugged = False

    def start(self):
        if not LIVEKIT_AVAILABLE:
            return
        self.thread.start()
        self.ready.wait(timeout=10)

    def close(self):
        self._closed = True
        try:
            if self.room:
                asyncio.run_coroutine_threadsafe(self.room.disconnect(), self.loop)
        except Exception:
            pass

    def set_recording(self, enabled: bool):
        self.recording_enabled = enabled
        if enabled:
            self.input_buffer = bytearray()
            self._frame_count = 0
            try:
                self._ensure_audio_subscriptions()
                self._schedule_subscription_retry(0.5, reason="recording_start_0.5s")
                self._schedule_subscription_retry(1.5, reason="recording_start_1.5s")
            except Exception:
                pass

    def pop_audio_buffer(self) -> bytes:
        data = bytes(self.input_buffer)
        self.input_buffer = bytearray()
        return data

    def send_pcm(self, pcm_bytes: bytes, sample_rate: Optional[int] = None, channels: Optional[int] = None):
        if not LIVEKIT_AVAILABLE or not self.audio_source or not pcm_bytes:
            return
        sr = sample_rate or self.sample_rate
        ch = channels or self.channels
        samples_per_channel = int(sr * 0.02)  # 20ms frames
        frame_bytes = samples_per_channel * ch * 2  # 16-bit audio
        padded = pcm_bytes
        if len(padded) % frame_bytes != 0:
            pad_len = frame_bytes - (len(padded) % frame_bytes)
            padded += b"\x00" * pad_len

        def _queue_frames():
            for i in range(0, len(padded), frame_bytes):
                chunk = padded[i:i + frame_bytes]
                frame = rtc.AudioFrame(chunk, sr, ch, samples_per_channel)
                yield frame

        async def _send():
            for frame in _queue_frames():
                await self.audio_source.capture_frame(frame)

        asyncio.run_coroutine_threadsafe(_send(), self.loop)

    def _handle_audio_frame(self, frame):
        if not self.recording_enabled:
            return
        pcm = None
        frame_obj = frame
        if hasattr(frame, "frame"):
            frame_obj = getattr(frame, "frame", frame)
        for attr in ("data", "samples", "buffer", "pcm"):
            if hasattr(frame_obj, attr):
                pcm = getattr(frame_obj, attr, None)
                if pcm is not None:
                    break
        if pcm is None and hasattr(frame_obj, "to_bytes"):
            try:
                pcm = frame_obj.to_bytes()
            except Exception:
                pcm = None
        if pcm is None:
            if not self._frame_debugged:
                self._frame_debugged = True
                try:
                    attrs = [a for a in dir(frame) if not a.startswith("_")]
                    nested_attrs = []
                    if hasattr(frame, "frame"):
                        nested = getattr(frame, "frame", None)
                        if nested is not None:
                            nested_attrs = [a for a in dir(nested) if not a.startswith("_")]
                    print(f"⚠️ LiveKit audio frame missing pcm data. attrs={attrs} nested={nested_attrs}", flush=True)
                except Exception:
                    pass
            return
        if isinstance(pcm, memoryview):
            pcm_bytes = pcm.tobytes()
        elif isinstance(pcm, (bytes, bytearray)):
            pcm_bytes = bytes(pcm)
        elif hasattr(pcm, "tobytes"):
            pcm_bytes = pcm.tobytes()
        else:
            pcm_bytes = bytes(pcm)
        if not pcm_bytes:
            return
        self.input_buffer.extend(pcm_bytes)
        self._frame_count += 1
        if self._frame_count <= 3 or self._frame_count % 50 == 0:
            sample_rate = getattr(frame_obj, "sample_rate", None)
            channels = getattr(frame_obj, "num_channels", None)
            spc = getattr(frame_obj, "samples_per_channel", None)
            print(f"🎧 LiveKit audio frame {self._frame_count}: {len(pcm_bytes)} bytes sr={sample_rate} ch={channels} spc={spc}", flush=True)

    def _ensure_audio_subscriptions(self):
        """Subscribe to any remote audio track publications (sync or async)."""
        if not self.room:
            return
        participants = getattr(self.room, "remote_participants", {})
        try:
            print(f"🔎 LiveKit ensure subscribe: participants={len(participants)}", flush=True)
        except Exception:
            pass
        for _, participant in participants.items():
            pubs = None
            if hasattr(participant, "track_publications"):
                pubs = participant.track_publications
            elif hasattr(participant, "tracks"):
                pubs = participant.tracks
            elif hasattr(participant, "audio_track_publications"):
                pubs = participant.audio_track_publications
            elif hasattr(participant, "track_publications_by_sid"):
                pubs = participant.track_publications_by_sid
            elif hasattr(participant, "publications"):
                pubs = participant.publications
            elif hasattr(participant, "_track_publications"):
                pubs = getattr(participant, "_track_publications", None)
            if not pubs:
                try:
                    debug_attrs = [name for name in dir(participant) if "track" in name or "pub" in name]
                    print(f"🔎 LiveKit ensure subscribe: no publications for {participant.identity} attrs={debug_attrs}", flush=True)
                except Exception:
                    pass
                continue
            if isinstance(pubs, dict):
                pub_items = pubs.items()
            else:
                pub_items = enumerate(list(pubs))
            try:
                pub_list = []
                for _, publication in pub_items:
                    kind = getattr(publication, "kind", None)
                    pub_list.append(str(kind))
                print(f"🔎 LiveKit publications for {participant.identity}: {pub_list}", flush=True)
                if isinstance(pubs, dict):
                    pub_items = pubs.items()
                else:
                    pub_items = enumerate(list(pubs))
            except Exception:
                if isinstance(pubs, dict):
                    pub_items = pubs.items()
                else:
                    pub_items = enumerate(list(pubs))
            for _, publication in pub_items:
                kind = getattr(publication, "kind", None)
                kind_name = str(kind).lower() if kind is not None else ""
                if kind == rtc.TrackKind.KIND_AUDIO or "audio" in kind_name:
                    try:
                        result = publication.set_subscribed(True)
                        if asyncio.iscoroutine(result):
                            asyncio.run_coroutine_threadsafe(result, self.loop)
                        print(f"✅ LiveKit ensured audio subscribed for {participant.identity}", flush=True)
                    except Exception as e:
                        print(f"⚠️ LiveKit ensure subscribe failed: {e}", flush=True)

    def _schedule_subscription_retry(self, delay_sec: float, reason: str):
        if not self.loop:
            return
        async def _retry():
            try:
                await asyncio.sleep(delay_sec)
                print(f"🔁 LiveKit subscription retry ({reason})", flush=True)
                self._ensure_audio_subscriptions()
            except Exception as e:
                print(f"⚠️ LiveKit subscription retry failed: {e}", flush=True)
        asyncio.run_coroutine_threadsafe(_retry(), self.loop)

    async def _consume_audio_track(self, track):
        try:
            track_sid = getattr(track, "sid", None)
            print(f"🎧 LiveKit audio stream start (sid={track_sid})", flush=True)
            audio_stream = rtc.AudioStream(track)
            async for frame in audio_stream:
                self._handle_audio_frame(frame)
        except Exception as e:
            print(f"⚠️ LiveKit audio stream error: {e}")

    async def _connect(self):
        self.room = rtc.Room()

        @self.room.on("participant_connected")
        def _on_participant_connected(participant):
            try:
                print(f"👤 LiveKit participant connected: {participant.identity}", flush=True)
                try:
                    room_name = getattr(self.room, "name", None)
                    participants = []
                    for _, remote in getattr(self.room, "remote_participants", {}).items():
                        identity = getattr(remote, "identity", None)
                        if identity:
                            participants.append(identity)
                    print(f"🧭 LiveKit room '{room_name}' participants: {participants}", flush=True)
                    try:
                        self._ensure_audio_subscriptions()
                        self._schedule_subscription_retry(0.5, reason="participant_connected_0.5s")
                        self._schedule_subscription_retry(1.5, reason="participant_connected_1.5s")
                    except Exception:
                        pass
                except Exception:
                    pass
            except Exception:
                pass

        @self.room.on("track_published")
        def _on_track_published(publication, participant):
            kind = getattr(publication, "kind", None)
            kind_name = str(kind).lower() if kind is not None else ""
            if kind == rtc.TrackKind.KIND_AUDIO or "audio" in kind_name:
                print(f"🎙️ LiveKit audio track published by {participant.identity}", flush=True)
                async def _subscribe():
                    try:
                        result = publication.set_subscribed(True)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as e:
                        print(f"⚠️ LiveKit subscribe failed: {e}", flush=True)
                asyncio.create_task(_subscribe())

        @self.room.on("track_subscribed")
        def _on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                track_sid = getattr(track, "sid", None)
                print(f"🎧 LiveKit subscribed to audio track from {participant.identity} (sid={track_sid})", flush=True)
                asyncio.create_task(self._consume_audio_track(track))

        connect_options = None
        try:
            connect_options = rtc.RoomOptions(auto_subscribe=True)
        except Exception:
            try:
                connect_options = rtc.RoomOptions()
                connect_options.auto_subscribe = True
            except Exception:
                connect_options = None

        if connect_options:
            await self.room.connect(self.url, self.token, connect_options)
        else:
            await self.room.connect(self.url, self.token)
        try:
            room_name = getattr(self.room, "name", None)
            local_identity = self.room.local_participant.identity
            participants = []
            for _, remote in getattr(self.room, "remote_participants", {}).items():
                identity = getattr(remote, "identity", None)
                if identity:
                    participants.append(identity)
            print(f"✅ LiveKit room '{room_name}' connected as {local_identity}", flush=True)
            print(f"🧭 LiveKit room '{room_name}' participants: {participants}", flush=True)
        except Exception:
            pass
        self.audio_source = rtc.AudioSource(self.sample_rate, self.channels)
        local_track = rtc.LocalAudioTrack.create_audio_track("assistant_audio", self.audio_source)
        await self.room.local_participant.publish_track(local_track)
        self.ready.set()

    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self._connect())
            self.loop.run_forever()
        except Exception as e:
            print(f"⚠️ LiveKit session loop error: {e}")
        finally:
            self.loop.close()


class LiveKitSessionManager:
    def __init__(self, url: str, api_key: str, api_secret: str):
        self.url = url
        self.api_key = api_key
        self.api_secret = api_secret
        self.sessions = {}
        self.lock = threading.Lock()

    def is_available(self) -> bool:
        return LIVEKIT_AVAILABLE and bool(self.url and self.api_key and self.api_secret)

    def ensure_session(self, session_id: str, room_name: str, assistant_identity: str) -> Optional[LiveKitRoomSession]:
        if not self.is_available():
            return None
        with self.lock:
            if session_id in self.sessions:
                return self.sessions[session_id]
            token = generate_livekit_token(self.api_key, self.api_secret, assistant_identity, room_name)
            session = LiveKitRoomSession(self.url, token)
            session.start()
            self.sessions[session_id] = session
            return session

    def get_session(self, session_id: str) -> Optional[LiveKitRoomSession]:
        return self.sessions.get(session_id)

    def set_recording(self, session_id: str, enabled: bool):
        session = self.get_session(session_id)
        if session:
            session.set_recording(enabled)

    def pop_audio_buffer(self, session_id: str) -> bytes:
        session = self.get_session(session_id)
        if not session:
            return b""
        return session.pop_audio_buffer()

    def send_pcm(self, session_id: str, pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1):
        session = self.get_session(session_id)
        if session:
            session.send_pcm(pcm_bytes, sample_rate=sample_rate, channels=channels)

    def close_session(self, session_id: str):
        with self.lock:
            session = self.sessions.pop(session_id, None)
            if session:
                session.close()
