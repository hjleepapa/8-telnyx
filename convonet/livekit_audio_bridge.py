import asyncio
import threading
import time
import uuid
from typing import Optional

import jwt

try:
    from livekit import rtc
    # Debug: Print rtc module contents immediately
    print(f"🔍 LiveKit RTC Module Contents: {dir(rtc)}", flush=True)
    
    try:
        from livekit.rtc import RoomEvent
    except ImportError:
        # Fallback for older/newer versions or debugging
        print(f"⚠️ Could not import RoomEvent from livekit.rtc. Available: {dir(rtc)}")
        if hasattr(rtc, 'RoomEvent'):
            RoomEvent = rtc.RoomEvent
        else:
            print("⚠️ Defining fallback RoomEvent class")
            class RoomEvent:
                PARTICIPANT_CONNECTED = "participant_connected"
                PARTICIPANT_DISCONNECTED = "participant_disconnected"
                TRACK_PUBLISHED = "track_published"
                TRACK_UNPUBLISHED = "track_unpublished"
                TRACK_SUBSCRIBED = "track_subscribed"
                TRACK_UNSUBSCRIBED = "track_unsubscribed"
    
    # Try to print version
    try:
        import pkg_resources
        version = pkg_resources.get_distribution("livekit").version
        print(f"✅ LiveKit SDK {version} imported successfully", flush=True)
    except Exception:
        print(f"✅ LiveKit SDK imported (version unknown)", flush=True)

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
        
        # Log that we received a frame object of some kind
        if self._frame_count == 0:
            print(f"📡 LiveKit first audio frame received: {type(frame)}", flush=True)

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
        """Ensure we are subscribed to all remote audio tracks"""
        if not self.room:
            return

        print(f"🔎 LiveKit ensure subscribe: participants={len(self.room.remote_participants)}", flush=True)
        
        for p_id, participant in self.room.remote_participants.items():
            # Helper to log attributes for debugging
            try:
                if not getattr(participant, "_debug_logged", False):
                    print(f"🕵️ DEBUG {participant.identity} keys: {list(participant.__dict__.keys()) if hasattr(participant, '__dict__') else 'no __dict__'}", flush=True)
                    # Dump everything that looks like a track if strict lookups failed
                    participant._debug_logged = True
            except:
                pass

            # Try to find tracks in various potential locations
            pubs = None
            if hasattr(participant, "track_publications") and participant.track_publications:
                pubs = participant.track_publications
            elif hasattr(participant, "tracks") and participant.tracks:
                pubs = participant.tracks
            elif hasattr(participant, "audio_track_publications") and participant.audio_track_publications:
                pubs = participant.audio_track_publications
            elif hasattr(participant, "track_publications_by_sid") and participant.track_publications_by_sid:
                pubs = participant.track_publications_by_sid
            elif hasattr(participant, "publications") and participant.publications:
                pubs = participant.publications
            elif hasattr(participant, "_track_publications") and participant._track_publications:
                pubs = getattr(participant, "_track_publications", None)
            
            # If still nothing, do a deep search and log it
            if not pubs:
                try:
                    # One-time deep introspection log
                    print(f"🕵️ DEBUG Introspecting participant {participant.identity} for missing tracks...", flush=True)
                    for a in dir(participant):
                        if "track" in a.lower() or "pub" in a.lower():
                            try:
                                v = getattr(participant, a, "N/A")
                                print(f"   - {a}: {v}", flush=True)
                            except:
                                pass
                except Exception as e:
                    print(f"�️ DEBUG introspection failed: {e}", flush=True)
                continue
            
            # Normalize to iterator
            pub_items = []
            if isinstance(pubs, dict):
                pub_items = pubs.values()
            elif isinstance(pubs, list) or hasattr(pubs, '__iter__'):
                pub_items = list(pubs)

            if not pub_items:
                 print(f"🔎 LiveKit found empty publications list for {participant.identity}", flush=True)

            for publication in pub_items:
                kind = getattr(publication, "kind", None)
                kind_name = str(kind).lower() if kind is not None else ""
                
                if kind == rtc.TrackKind.KIND_AUDIO or "audio" in kind_name:
                    if not getattr(publication, "subscribed", False):
                        try:
                            print(f"🎙️ LiveKit manually subscribing to {kind_name} track for {participant.identity}", flush=True)
                            if hasattr(publication, "set_subscribed"):
                                result = publication.set_subscribed(True)
                                if asyncio.iscoroutine(result):
                                    asyncio.run_coroutine_threadsafe(result, self.loop)
                                print(f"✅ LiveKit ensured audio subscribed for {participant.identity}", flush=True)
                            else:
                                print(f"⚠️ Publication has no set_subscribed: {publication}", flush=True)
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

        # Catch-all event logger
        try:
            # We cannot easily hook "all" events in pyee without hacking, but we can hook the ones we know
            pass
        except Exception:
            pass

        @self.room.on(RoomEvent.PARTICIPANT_CONNECTED)
        def _on_participant_connected(participant):
            try:
                print(f"👤 LiveKit participant connected: {participant.identity}", flush=True)
                try:
                    self._ensure_audio_subscriptions()
                    self._schedule_subscription_retry(0.5, reason="participant_connected_0.5s")
                    self._schedule_subscription_retry(1.5, reason="participant_connected_1.5s")
                    self._schedule_subscription_retry(3.0, reason="participant_connected_3.0s")
                except Exception:
                    pass
            except Exception:
                pass

        @self.room.on(RoomEvent.TRACK_PUBLISHED)
        def _on_track_published(publication, participant):
            try:
                kind = getattr(publication, "kind", None)
                kind_name = str(kind).lower() if kind is not None else ""
                print(f"🎙️ LiveKit Room Event: track_published by {participant.identity}, kind={kind_name}", flush=True)
                
                # Always force subscribe to audio tracks, regardless of auto_subscribe
                if kind == rtc.TrackKind.KIND_AUDIO or "audio" in kind_name:
                    async def _subscribe():
                        try:
                            print(f"🎙️ LiveKit subscribing to {kind_name} track from {participant.identity}", flush=True)
                            if hasattr(publication, "set_subscribed"):
                                result = publication.set_subscribed(True)
                                if asyncio.iscoroutine(result):
                                    await result
                                print(f"✅ LiveKit subscribed to {participant.identity}", flush=True)
                            else:
                                print(f"⚠️ Publication has no set_subscribed method: {dir(publication)}", flush=True)
                        except Exception as e:
                            print(f"⚠️ LiveKit subscribe failed: {e}", flush=True)
                    
                    # Run immediately in the loop
                    asyncio.run_coroutine_threadsafe(_subscribe(), self.loop)
            except Exception as e:
                print(f"⚠️ LiveKit on_track_published error: {e}", flush=True)

        @self.room.on(RoomEvent.TRACK_SUBSCRIBED)
        def _on_track_subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO:
                track_sid = getattr(track, "sid", None)
                print(f"🎧 LiveKit subscribed to audio track from {participant.identity} (sid={track_sid})", flush=True)
                # CRITICAL: Use run_coroutine_threadsafe to schedule on self.loop, not create_task
                asyncio.run_coroutine_threadsafe(self._consume_audio_track(track), self.loop)

        connect_options = None
        try:
            print(f"🔧 LiveKit connecting with auto_subscribe=True", flush=True)
            # Try multiple ways to set auto_subscribe based on SDK version
            try:
                connect_options = rtc.RoomOptions(auto_subscribe=True)
                print(f"✅ Created RoomOptions with auto_subscribe=True", flush=True)
            except Exception as e1:
                print(f"⚠️ RoomOptions(auto_subscribe=True) failed: {e1}", flush=True)
                try:
                    connect_options = rtc.RoomOptions()
                    connect_options.auto_subscribe = True
                    print(f"✅ Created RoomOptions and set auto_subscribe=True", flush=True)
                except Exception as e2:
                    print(f"⚠️ Setting RoomOptions.auto_subscribe failed: {e2}", flush=True)
                    connect_options = None

            if connect_options:
                await self.room.connect(self.url, self.token, options=connect_options)
            else:
                # Fallback to direct kwargs if options object fails
                try:
                    await self.room.connect(self.url, self.token, auto_subscribe=True)
                    print(f"✅ Connected with auto_subscribe=True via kwargs", flush=True)
                except Exception as e3:
                    print(f"⚠️ Connection with auto_subscribe=True via kwargs failed: {e3}", flush=True)
                    await self.room.connect(self.url, self.token)
                    print(f"✅ Connected with default options", flush=True)
        except Exception as e:
            print(f"❌ LiveKit connection failed in _connect: {e}", flush=True)
            raise e
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
        
        # Room status monitor task
        async def _monitor_room():
            while not self._closed:
                try:
                    name = getattr(self.room, "name", "unknown")
                    parts = list(getattr(self.room, "remote_participants", {}).keys())
                    print(f"📊 Room '{name}' monitor: participants={parts}", flush=True)
                    for pid in parts:
                        p = self.room.remote_participants[pid]
                        # Check tracks
                        t_pubs = getattr(p, "track_publications", {})
                        if not t_pubs:
                            t_pubs = getattr(p, "_track_publications", {})
                        if not t_pubs and hasattr(p, "tracks"):
                            t_pubs = getattr(p, "tracks", {})
                        
                        try:
                            # Safely get keys
                            keys = list(t_pubs.keys()) if hasattr(t_pubs, "keys") else str(len(t_pubs)) if hasattr(t_pubs, "__len__") else "unknown"
                            print(f"  └─ Participant '{pid}' tracks: {keys}", flush=True)
                        except:
                            pass
                except Exception as me:
                    print(f"⚠️ Room monitor error: {me}", flush=True)
                await asyncio.sleep(5.0)
        
        asyncio.create_task(_monitor_room())

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
