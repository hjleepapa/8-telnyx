"""
WebRTC Voice Assistant Server
Provides high-quality audio streaming and real-time speech recognition
"""

import asyncio
import json
import os
import base64
import time
import re
from uuid import UUID
from urllib.parse import quote
from flask import Blueprint, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room

# Apply nest_asyncio to allow nested event loops (needed for eventlet compatibility)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass  # nest_asyncio not available, may cause issues with eventlet
# Note: OpenAI import removed - using Claude LLM and Deepgram TTS
from convonet.assistant_graph_todo import get_agent
from convonet.state import AgentState
from convonet.voice_intent_utils import has_transfer_intent
from langchain_core.messages import HumanMessage
from twilio.rest import Client

# Deepgram WebRTC integration
from deepgram_webrtc_integration import transcribe_audio_with_deepgram_webrtc, get_deepgram_webrtc_info
from deepgram_service import get_deepgram_service

# ElevenLabs integration
try:
    from convonet.elevenlabs_service import get_elevenlabs_service, EmotionType
    from convonet.voice_preferences import get_voice_preferences
    from convonet.emotion_detection import get_emotion_detector
    ELEVENLABS_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ ElevenLabs not available: {e}")
    ELEVENLABS_AVAILABLE = False

# Import the blueprint (optional - not used in this module)
# from convonet.routes import convonet_todo_bp

# Sentry integration for monitoring Redis interactions and errors
try:
    import sentry_sdk
    from sentry_sdk.integrations.flask import FlaskIntegration
    from sentry_sdk.integrations.socketio import SocketIOIntegration
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False
# Optional Redis imports - app should work without them
try:
    from convonet.redis_manager import redis_manager, create_session, get_session, update_session, delete_session
    REDIS_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Redis not available: {e}")
    REDIS_AVAILABLE = False
    # Create dummy functions for fallback
    class DummyRedisManager:
        def is_available(self):
            return False
    redis_manager = DummyRedisManager()
    def create_session(*args, **kwargs):
        return False
    def get_session(*args, **kwargs):
        return None
    def update_session(*args, **kwargs):
        return False
    def delete_session(*args, **kwargs):
        return False

# Optional test PIN support (disabled by default unless explicitly enabled)
ENABLE_TEST_PIN = os.getenv('ENABLE_TEST_PIN', 'false').lower() == 'true'
TEST_VOICE_PIN = os.getenv('TEST_VOICE_PIN', '1234')

webrtc_bp = Blueprint('webrtc_voice', __name__, url_prefix='/webrtc')

# Initialize Deepgram service for STT and TTS
# Note: Using Deepgram for both STT and TTS, Claude for LLM
deepgram_service = None
def get_deepgram_tts_service():
    """Get Deepgram service for TTS"""
    global deepgram_service
    if deepgram_service is None:
        deepgram_service = get_deepgram_service()
    return deepgram_service

# Active sessions storage (fallback for when Redis is unavailable)
active_sessions = {}

# Global references for background tasks
socketio = None
flask_app = None


def build_customer_profile_from_session(session_data: dict | None) -> dict | None:
    """
    Build a comprehensive customer profile for the call center popup.
    Includes conversation history from LangGraph and activities from tool calls.
    """
    if not session_data:
        return None
    
    profile = {
        "customer_id": session_data.get('user_id') or session_data.get('user_name') or "convonet_caller",
        "name": session_data.get('user_name') or "Convonet Caller",
        "email": None,
        "phone": None,
        "account_status": "Active",
        "tier": "Standard",
        "notes": "Captured from Convonet voice assistant",
        "conversation_history": [],
        "activities": []
    }
    
    user_id = session_data.get('user_id')
    if user_id:
        try:
            from convonet.mcps.local_servers.db_todo import SessionLocal, _init_database
            from convonet.models.user_models import User as UserModel
            
            _init_database()
            with SessionLocal() as db_session:
                user = db_session.query(UserModel).filter(UserModel.id == UUID(user_id)).first()
                if user:
                    profile.update({
                        "customer_id": str(user.id),
                        "name": user.full_name if hasattr(user, "full_name") else f"{user.first_name} {user.last_name}",
                        "email": user.email,
                        "voice_pin": user.voice_pin,
                        "account_status": "Verified" if user.is_verified else "Unverified",
                    })
        except Exception as e:
            print(f"⚠️ Unable to load customer profile for call center: {e}")
    
    # Retrieve conversation history from LangGraph
    try:
        from convonet.assistant_graph_todo import get_agent
        from langchain_core.messages import HumanMessage, AIMessage, ToolMessage
        
        thread_id = f"user-{user_id}" if user_id else None
        if thread_id:
            agent = get_agent(user_id=user_id, agent_type="todo")  # Default to todo agent
            if agent and hasattr(agent, 'graph'):
                config = {"configurable": {"thread_id": thread_id}}
                try:
                    state = agent.graph.get_state(config=config)
                    if state and hasattr(state, 'values') and state.values:
                        messages = state.values.get('messages', [])
                        
                        # Parse messages into conversation history
                        conversation = []
                        for msg in messages:
                            if isinstance(msg, HumanMessage):
                                conversation.append({
                                    "role": "user",
                                    "content": msg.content if hasattr(msg, 'content') else str(msg),
                                    "timestamp": getattr(msg, 'timestamp', None)
                                })
                            elif isinstance(msg, AIMessage):
                                content = msg.content if hasattr(msg, 'content') else str(msg)
                                # Extract text from content (might be list of dicts)
                                if isinstance(content, list):
                                    text_parts = [part.get('text', '') for part in content if isinstance(part, dict) and part.get('type') == 'text']
                                    content = ' '.join(text_parts) if text_parts else str(content)
                                conversation.append({
                                    "role": "assistant",
                                    "content": content,
                                    "timestamp": getattr(msg, 'timestamp', None)
                                })
                            elif isinstance(msg, ToolMessage):
                                # Extract activities from tool calls
                                tool_name = getattr(msg, 'name', None) or 'unknown_tool'
                                tool_content = msg.content if hasattr(msg, 'content') else str(msg)
                                
                                # Parse tool content to extract activity info
                                activity = {
                                    "type": "tool_call",
                                    "tool": tool_name,
                                    "result": tool_content[:200] if isinstance(tool_content, str) else str(tool_content)[:200],  # Truncate long results
                                    "timestamp": getattr(msg, 'timestamp', None)
                                }
                                
                                # Identify specific activity types
                                if 'calendar' in tool_name.lower() or 'event' in tool_name.lower():
                                    activity["activity_type"] = "calendar_event"
                                    try:
                                        if isinstance(tool_content, str):
                                            import json
                                            result_data = json.loads(tool_content) if tool_content.startswith('{') else {}
                                            if 'title' in result_data:
                                                activity["title"] = result_data.get('title', '')
                                            if 'event_from' in result_data or 'start' in result_data:
                                                activity["date"] = result_data.get('event_from') or result_data.get('start', '')
                                    except:
                                        pass
                                elif 'todo' in tool_name.lower():
                                    activity["activity_type"] = "todo"
                                    try:
                                        if isinstance(tool_content, str):
                                            import json
                                            result_data = json.loads(tool_content) if tool_content.startswith('{') else {}
                                            if 'title' in result_data or 'task' in result_data:
                                                activity["title"] = result_data.get('title') or result_data.get('task', '')
                                    except:
                                        pass
                                elif 'mortgage' in tool_name.lower():
                                    activity["activity_type"] = "mortgage"
                                    try:
                                        if isinstance(tool_content, str):
                                            import json
                                            result_data = json.loads(tool_content) if tool_content.startswith('{') else {}
                                            if 'application_id' in result_data:
                                                activity["title"] = f"Mortgage Application {result_data.get('application_id', '')[:8]}"
                                    except:
                                        pass
                                
                                profile["activities"].append(activity)
                        
                        profile["conversation_history"] = conversation[-20:]  # Last 20 messages
                        profile["activities"] = profile["activities"][-10:]  # Last 10 activities
                        
                except Exception as e:
                    print(f"⚠️ Unable to retrieve LangGraph conversation history: {e}")
    except Exception as e:
        print(f"⚠️ Error building conversation history: {e}")
    
    return profile


def cache_call_center_profile(extension: str, session_data: dict | None, call_sid: str = None, call_id: str = None):
    """
    Store customer info in Redis so the call-center popup can display real data.
    
    Uses unique cache keys with call_sid or call_id to prevent overwrites when multiple calls
    from the same user transfer to the same extension.
    
    Args:
        extension: Agent extension number
        session_data: Session data containing user info
        call_sid: Twilio Call SID (for Twilio calls)
        call_id: Call ID (for WebRTC calls, typically session_id)
    """
    if not extension or not REDIS_AVAILABLE or not redis_manager.is_available():
        return
    
    profile = build_customer_profile_from_session(session_data)
    if not profile:
        return
    
    profile["extension"] = extension
    if call_sid:
        profile["call_sid"] = call_sid
    if call_id:
        profile["call_id"] = call_id
    
    try:
        # Store with unique key if call_sid or call_id provided
        if call_sid:
            unique_key = f"callcenter:customer:{extension}:{call_sid}"
            redis_manager.redis_client.setex(unique_key, 300, json.dumps(profile))
            print(f"💾 Cached customer profile with unique key: {unique_key}")
        elif call_id:
            unique_key = f"callcenter:customer:{extension}:{call_id}"
            redis_manager.redis_client.setex(unique_key, 300, json.dumps(profile))
            print(f"💾 Cached customer profile with unique key: {unique_key}")
        
        # Also store with extension-only key for backward compatibility (most recent call)
        fallback_key = f"callcenter:customer:{extension}"
        redis_manager.redis_client.setex(fallback_key, 300, json.dumps(profile))
        print(f"💾 Cached customer profile with fallback key: {fallback_key}")
    except Exception as e:
        print(f"⚠️ Failed to cache call center profile: {e}")


def is_transfer_in_progress(session_id: str, session_record: dict | None = None) -> bool:
    """Check whether a transfer is already in progress for this WebRTC session."""
    try:
        if session_record and 'transfer_in_progress' in session_record:
            return str(session_record['transfer_in_progress']).lower() == 'true'
        
        if redis_manager.is_available():
            value = redis_manager.redis_client.hget(f"session:{session_id}", "transfer_in_progress")
            if value is not None:
                return str(value).lower() == 'true'
        else:
            if session_id in active_sessions:
                return bool(active_sessions[session_id].get('transfer_in_progress'))
    except Exception as e:
        print(f"⚠️ Unable to read transfer flag for session {session_id}: {e}")
    return False


def set_transfer_flag(session_id: str, value: bool, session_record: dict | None = None):
    """Persist the transfer_in_progress flag for this WebRTC session."""
    str_value = 'True' if value else 'False'
    try:
        if session_record is not None:
            session_record['transfer_in_progress'] = str_value
        
        if redis_manager.is_available():
            redis_manager.redis_client.hset(f"session:{session_id}", "transfer_in_progress", str_value)
        else:
            if session_id not in active_sessions:
                active_sessions[session_id] = {}
            active_sessions[session_id]['transfer_in_progress'] = value
    except Exception as e:
        print(f"⚠️ Unable to set transfer flag for session {session_id}: {e}")


def initiate_agent_transfer(session_id: str, extension: str, department: str, reason: str, session_data: dict | None):
    """
    Use Twilio Programmable Voice to originate a real call path to the target agent (and optionally the user).

    Returns:
        (success: bool, details: dict)
    """
    account_sid = os.getenv('TWILIO_ACCOUNT_SID')
    auth_token = os.getenv('TWILIO_AUTH_TOKEN')
    caller_id = (
        os.getenv('TWILIO_TRANSFER_CALLER_ID')
        or os.getenv('TWILIO_CALLER_ID')
        or os.getenv('TWILIO_NUMBER')
    )
    # Get base URL - prefer explicit transfer URL, then public URL, then Render URL
    base_url = (
        os.getenv('VOICE_ASSISTANT_TRANSFER_BASE_URL') 
        or os.getenv('PUBLIC_BASE_URL')
        or os.getenv('RENDER_EXTERNAL_URL')  # Render automatically sets this
        or 'https://convonet-anthropic.onrender.com'  # Fallback to Render service URL
    )
    freepbx_domain = os.getenv('FREEPBX_DOMAIN', '136.115.41.45')

    if not (account_sid and auth_token and caller_id and base_url):
        missing = []
        if not account_sid:
            missing.append('TWILIO_ACCOUNT_SID')
        if not auth_token:
            missing.append('TWILIO_AUTH_TOKEN')
        if not caller_id:
            missing.append('TWILIO_TRANSFER_CALLER_ID / TWILIO_CALLER_ID / TWILIO_NUMBER')
        if not base_url:
            missing.append('VOICE_ASSISTANT_TRANSFER_BASE_URL / PUBLIC_BASE_URL')
        message = f"Transfer aborted: missing configuration values: {', '.join(missing)}"
        print(f"⚠️ {message}")
        return False, {'error': message}

    # For WebRTC transfers, we directly dial the FusionPBX extension
    # The WebRTC user can't join a Twilio conference, so we just connect the agent
    transfer_url = f"{base_url.rstrip('/')}/convonet_todo/twilio/voice_assistant/transfer_bridge?extension={quote(extension)}"

    client = Client(account_sid, auth_token)
    response_details = {
        'extension': extension,
        'transfer_url': transfer_url,
        'agent_call_sid': None,
        'user_call_sid': None
    }

    try:
        # Use domain/IP for Twilio (Twilio needs resolvable domain/IP)
        # FusionPBX dialplan must be configured to route external calls to extensions
        sip_target = f"sip:{extension}@{freepbx_domain};transport=udp"
        print(f"📞 Creating Twilio call:")
        print(f"   To: {sip_target}")
        print(f"   From: {caller_id}")
        print(f"   URL: {transfer_url}")
        agent_call = client.calls.create(
            to=sip_target,
            from_=caller_id,
            url=transfer_url,
            method='POST'  # Explicitly set POST method
        )
        response_details['agent_call_sid'] = agent_call.sid
        print(f"📞 ✅ Initiated agent call via Twilio (Call SID: {agent_call.sid}) to {sip_target}")
        print(f"📞 Call status: {agent_call.status}")
        print(f"📞 Twilio will POST to: {transfer_url}")
        
        # Cache customer profile with call_sid after getting Call SID
        if agent_call.sid and session_data:
            cache_call_center_profile(extension, session_data, call_sid=agent_call.sid)
    except Exception as agent_error:
        message = f"Failed to originate agent call: {agent_error}"
        print(f"❌ {message}")
        response_details['error'] = message
        return False, response_details

    # For WebRTC transfers, we don't call the user back because:
    # 1. WebRTC is browser-based, not a phone number
    # 2. The user needs to manually call the agent or use a different method
    # Instead, we provide instructions to the user via the WebRTC interface
    print(f"ℹ️ WebRTC transfer: Agent call initiated to extension {extension}. User should contact agent separately or use call center dashboard.")
    response_details['user_instructions'] = f"Please contact extension {extension} via the call center dashboard at {base_url}/call-center/"

    return True, response_details

# Sentry helper functions
def sentry_capture_redis_operation(operation: str, session_id: str, success: bool, error: str = None):
    """Capture Redis operations in Sentry for monitoring"""
    if SENTRY_AVAILABLE:
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("component", "webrtc_voice_server")
            scope.set_tag("operation", f"redis_{operation}")
            scope.set_context("redis_operation", {
                "session_id": session_id,
                "operation": operation,
                "success": success,
                "error": error
            })
            if success:
                sentry_sdk.add_breadcrumb(
                    message=f"Redis {operation} successful",
                    category="redis",
                    level="info"
                )
            else:
                sentry_sdk.capture_message(f"Redis {operation} failed: {error}", level="error")

def sentry_capture_voice_event(event: str, session_id: str, user_id: str = None, details: dict = None):
    """Capture voice assistant events in Sentry"""
    if SENTRY_AVAILABLE:
        with sentry_sdk.configure_scope() as scope:
            scope.set_tag("component", "webrtc_voice_server")
            scope.set_tag("event", event)
            scope.set_context("voice_event", {
                "session_id": session_id,
                "user_id": user_id,
                "event": event,
                "details": details or {}
            })
            sentry_sdk.add_breadcrumb(
                message=f"Voice event: {event}",
                category="voice_assistant",
                level="info"
            )


@webrtc_bp.route('/voice-assistant')
def voice_assistant():
    """Render the WebRTC voice assistant interface"""
    return render_template('webrtc_voice_assistant.html')


@webrtc_bp.route('/debug-session/<session_id>')
def debug_session(session_id):
    """Debug endpoint to check Redis session data"""
    try:
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if session_data:
                # Convert bytes to strings for JSON serialization
                debug_data = {}
                for key, value in session_data.items():
                    if isinstance(value, bytes):
                        debug_data[key] = value.decode('utf-8', errors='ignore')
                    else:
                        debug_data[key] = str(value)
                
                # Add audio buffer info
                audio_buffer = session_data.get('audio_buffer', '')
                debug_data['audio_buffer_length'] = len(audio_buffer)
                debug_data['audio_buffer_preview'] = audio_buffer[:100] + "..." if len(audio_buffer) > 100 else audio_buffer
                
                # Test base64 decoding
                try:
                    if audio_buffer:
                        decoded = base64.b64decode(audio_buffer)
                        debug_data['decoded_audio_length'] = len(decoded)
                        debug_data['base64_valid'] = True
                    else:
                        debug_data['decoded_audio_length'] = 0
                        debug_data['base64_valid'] = True
                except Exception as e:
                    debug_data['base64_valid'] = False
                    debug_data['base64_error'] = str(e)
                
                return jsonify({
                    'success': True,
                    'session_id': session_id,
                    'data': debug_data,
                    'storage': 'redis'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Session not found in Redis',
                    'session_id': session_id
                })
        else:
            # Check in-memory storage
            if session_id in active_sessions:
                session_data = active_sessions[session_id]
                debug_data = {
                    'authenticated': session_data.get('authenticated', False),
                    'user_id': session_data.get('user_id'),
                    'user_name': session_data.get('user_name'),
                    'is_recording': session_data.get('is_recording', False),
                    'audio_buffer_length': len(session_data.get('audio_buffer', b'')),
                    'storage': 'memory'
                }
                return jsonify({
                    'success': True,
                    'session_id': session_id,
                    'data': debug_data,
                    'storage': 'memory'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'Session not found in memory',
                    'session_id': session_id
                })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'session_id': session_id
        })


@webrtc_bp.route('/clear-session/<session_id>')
def clear_session(session_id):
    """Clear Redis session data for testing"""
    try:
        if redis_manager.is_available():
            # Clear the session
            delete_session(session_id)
            return jsonify({
                'success': True,
                'message': f'Session {session_id} cleared from Redis'
            })
        else:
            # Clear from memory
            if session_id in active_sessions:
                del active_sessions[session_id]
                return jsonify({
                    'success': True,
                    'message': f'Session {session_id} cleared from memory'
                })
            else:
                return jsonify({
                    'success': False,
                    'message': f'Session {session_id} not found'
                })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'session_id': session_id
        })


def init_socketio(socketio_instance: SocketIO, app):
    """Initialize Socket.IO event handlers"""
    
    # Store socketio instance and Flask app for background tasks
    global socketio, flask_app, socketio_instance_global
    socketio = socketio_instance
    socketio_instance_global = socketio_instance  # Store for use in nested functions
    flask_app = app  # Store Flask app directly (passed as parameter)
    
    @socketio.on('connect', namespace='/voice')
    def handle_connect():
        """Handle client connection"""
        session_id = request.sid
        print(f"✅ WebRTC client connected: {session_id}")
        
        # Capture connection event in Sentry
        sentry_capture_voice_event("client_connected", session_id)
        
        # Initialize session in Redis (with fallback to in-memory)
        session_data = {
            'authenticated': 'False',
            'user_id': '',
            'user_name': '',
            'audio_buffer': '',
            'is_recording': 'False',
            'connected_at': str(time.time())
        }
        
        try:
            if redis_manager.is_available():
                success = create_session(session_id, session_data, ttl=3600)  # 1 hour TTL
                if success:
                    print(f"✅ Session stored in Redis: {session_id}")
                    sentry_capture_redis_operation("create_session", session_id, True)
                else:
                    print(f"❌ Failed to store session in Redis: {session_id}")
                    sentry_capture_redis_operation("create_session", session_id, False, "Redis create_session returned False")
            else:
                # Fallback to in-memory storage
                active_sessions[session_id] = {
                    'authenticated': False,
                    'user_id': None,
                    'user_name': None,
                    'audio_buffer': b'',
                    'is_recording': False
                }
                print(f"⚠️ Using in-memory storage (Redis unavailable): {session_id}")
                sentry_capture_voice_event("redis_fallback", session_id, details={"storage": "in_memory"})
        except Exception as e:
            print(f"❌ Error creating session: {e}")
            sentry_capture_redis_operation("create_session", session_id, False, str(e))
            # Fallback to in-memory storage on error
            active_sessions[session_id] = {
                'authenticated': False,
                'user_id': None,
                'user_name': None,
                'audio_buffer': b'',
                'is_recording': False
            }
        
        emit('connected', {'session_id': session_id})
    
    
    @socketio.on('disconnect', namespace='/voice')
    def handle_disconnect():
        """Handle client disconnection"""
        session_id = request.sid
        print(f"❌ WebRTC client disconnected: {session_id}")
        
        # Get user_id before deleting session (for pending response handling)
        user_id = None
        try:
            session_data = get_session(session_id)
            if session_data:
                user_id = session_data.get('user_id')
        except:
            pass
        
        # Capture disconnection event in Sentry
        sentry_capture_voice_event("client_disconnected", session_id, user_id)
        set_transfer_flag(session_id, False)
        
        try:
            if redis_manager.is_available():
                success = delete_session(session_id)
                if success:
                    print(f"✅ Session deleted from Redis: {session_id}")
                    sentry_capture_redis_operation("delete_session", session_id, True)
                else:
                    print(f"❌ Failed to delete session from Redis: {session_id}")
                    sentry_capture_redis_operation("delete_session", session_id, False, "Redis delete_session returned False")
            else:
                if session_id in active_sessions:
                    del active_sessions[session_id]
                    print(f"✅ Session deleted from memory: {session_id}")
                    sentry_capture_voice_event("session_deleted_memory", session_id)
        except Exception as e:
            print(f"❌ Error deleting session: {e}")
            sentry_capture_redis_operation("delete_session", session_id, False, str(e))
        
        # Note: If there's a pending response being processed for this user_id,
        # it will be stored when TTS completes and session is found to be gone
    
    
    @socketio.on('authenticate', namespace='/voice')
    def handle_authenticate(data):
        """Handle user authentication"""
        session_id = request.sid
        pin = data.get('pin', '')
        
        print(f"🔐 Authentication request for session {session_id}: PIN={pin}")
        
        # Capture authentication attempt in Sentry
        sentry_capture_voice_event("authentication_attempt", session_id, details={"pin_provided": bool(pin)})
        
        try:
            # TEST MODE (optional): allow a configurable PIN when explicitly enabled
            if ENABLE_TEST_PIN and pin == TEST_VOICE_PIN:
                print(f"✅ Test authentication successful with PIN: {pin}")
                auth_updates = {
                    'authenticated': 'True',
                    'user_id': 'test_user',
                    'user_name': 'Test User',
                    'authenticated_at': str(time.time())
                }
                
                try:
                    if redis_manager.is_available():
                        success = update_session(session_id, auth_updates)
                        if success:
                            print(f"✅ Test authentication stored in Redis")
                            sentry_capture_redis_operation("update_session", session_id, True)
                            sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "redis", "mode": "test"})
                        else:
                            print(f"❌ Failed to update session in Redis")
                            sentry_capture_redis_operation("update_session", session_id, False, "Redis update_session returned False")
                            # Fallback to in-memory
                            active_sessions[session_id]['authenticated'] = True
                            active_sessions[session_id]['user_id'] = 'test_user'
                            active_sessions[session_id]['user_name'] = 'Test User'
                            print(f"✅ Test authentication stored in memory (Redis fallback)")
                            sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "memory_fallback", "mode": "test"})
                    else:
                        # Fallback to in-memory
                        active_sessions[session_id]['authenticated'] = True
                        active_sessions[session_id]['user_id'] = 'test_user'
                        active_sessions[session_id]['user_name'] = 'Test User'
                        print(f"✅ Test authentication stored in memory")
                        sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "memory", "mode": "test"})
                except Exception as redis_error:
                    print(f"❌ Redis error during test authentication: {redis_error}")
                    sentry_capture_redis_operation("update_session", session_id, False, str(redis_error))
                    # Fallback to in-memory storage
                    active_sessions[session_id]['authenticated'] = True
                    active_sessions[session_id]['user_id'] = 'test_user'
                    active_sessions[session_id]['user_name'] = 'Test User'
                    print(f"✅ Test authentication stored in memory (Redis error fallback)")
                    sentry_capture_voice_event("authentication_success", session_id, "test_user", {"user_name": "Test User", "storage": "memory_error_fallback", "mode": "test"})
                
                # Check if user was recently authenticated (re-authentication scenario)
                # We check by user_id, not session_id, because sessions are recreated on disconnect
                was_already_authenticated = False
                try:
                    if redis_manager.is_available():
                        # Check if test_user was authenticated recently (within last 5 minutes)
                        recent_auth_key = f"recent_auth:test_user"
                        recent_auth_data = redis_manager.redis_client.get(recent_auth_key)
                        if recent_auth_data:
                            was_already_authenticated = True
                            print(f"🔄 Re-authentication detected for test_user (session {session_id})")
                except Exception as auth_check_error:
                    print(f"⚠️ Error checking recent authentication: {auth_check_error}")
                    pass
                
                emit('authenticated', {
                    'success': True,
                    'user_name': 'Test User',
                    'message': "Welcome! You're in test mode." if not was_already_authenticated else "Reconnected! You're in test mode."
                })
                
                # Check for pending responses for test user
                try:
                    import json
                    redis_key = f"pending_response:test_user"
                    if redis_manager.is_available():
                        pending_data = redis_manager.redis_client.get(redis_key)
                        if pending_data:
                            pending_response = json.loads(pending_data)
                            print(f"📬 Found pending response for test_user, sending to session {session_id}", flush=True)
                            
                            # Send pending response with a small delay to ensure client is ready
                            def send_pending_response_test():
                                import eventlet
                                eventlet.sleep(0.5)  # Small delay to ensure client is ready
                                
                                # Check if session still exists before sending
                                current_session = get_session(session_id)
                                if current_session:
                                    print(f"📤 Sending pending response to test_user session {session_id} (delayed)", flush=True)
                                    socketio.emit('agent_response', {
                                        'success': True,
                                        'text': pending_response['text'],
                                        'audio': pending_response['audio'],
                                        'pending': True
                                    }, namespace='/voice', room=session_id)
                                    redis_manager.redis_client.delete(redis_key)
                                    print(f"✅ Pending response sent and cleared for test_user", flush=True)
                                else:
                                    print(f"⚠️ Session {session_id} no longer exists, cannot send pending response", flush=True)
                            
                            socketio.start_background_task(send_pending_response_test)
                except Exception as pending_error:
                    print(f"⚠️ Error checking/sending pending response: {pending_error}", flush=True)
                    import traceback
                    traceback.print_exc()
                
                # Track recent authentication for re-authentication detection
                try:
                    if redis_manager.is_available() and not was_already_authenticated:
                        # Store recent authentication timestamp (5 minute TTL)
                        recent_auth_key = f"recent_auth:test_user"
                        redis_manager.redis_client.setex(recent_auth_key, 300, str(time.time()))
                except Exception as track_error:
                    print(f"⚠️ Error tracking recent authentication: {track_error}")
                
                # Only send welcome greeting on first authentication, not on re-authentication
                if not was_already_authenticated:
                    # Send welcome greeting with audio (background task)
                    socketio.start_background_task(
                        send_welcome_greeting, 
                        session_id, 
                        'Test User'
                    )
                else:
                    print(f"⏭️ Skipping welcome greeting (re-authentication)")
                return
            
            # Import here to avoid circular imports
            from convonet.mcps.local_servers.db_todo import _init_database, SessionLocal
            from convonet.models.user_models import User as UserModel
            
            _init_database()
            
            with SessionLocal() as db_session:
                user = db_session.query(UserModel).filter(
                    UserModel.voice_pin == pin,
                    UserModel.is_active == True
                ).first()
                
                if user:
                    # Check if user was recently authenticated (re-authentication scenario)
                    # We check by user_id, not session_id, because sessions are recreated on disconnect
                    was_already_authenticated = False
                    user_id_str = str(user.id)
                    try:
                        if redis_manager.is_available():
                            # Check if user was authenticated recently (within last 5 minutes)
                            recent_auth_key = f"recent_auth:{user_id_str}"
                            recent_auth_data = redis_manager.redis_client.get(recent_auth_key)
                            if recent_auth_data:
                                was_already_authenticated = True
                                print(f"🔄 Re-authentication detected for user {user.id} (session {session_id})")
                    except Exception as auth_check_error:
                        print(f"⚠️ Error checking recent authentication: {auth_check_error}")
                        pass
                    
                    # Authentication successful
                    auth_updates = {
                        'authenticated': 'True',
                        'user_id': str(user.id),
                        'user_name': user.first_name,
                        'authenticated_at': str(time.time())
                    }
                    
                    try:
                        if redis_manager.is_available():
                            success = update_session(session_id, auth_updates)
                            if success:
                                print(f"✅ Authentication stored in Redis: {user.email}")
                                sentry_capture_redis_operation("update_session", session_id, True)
                                sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "redis", "re_authentication": was_already_authenticated})
                                
                                # Check for pending responses for this user
                                try:
                                    import json
                                    redis_key = f"pending_response:{user.id}"
                                    pending_data = redis_manager.redis_client.get(redis_key)
                                    if pending_data:
                                        pending_response = json.loads(pending_data)
                                        print(f"📬 Found pending response for user {user.id}, sending to new session {session_id}", flush=True)
                                        
                                        # Store pending response info to send when client is ready
                                        # We'll wait for client_ready event instead of using a fixed delay
                                        if not hasattr(socketio, '_pending_responses'):
                                            socketio._pending_responses = {}
                                        
                                        socketio._pending_responses[session_id] = {
                                            'response': pending_response,
                                            'redis_key': redis_key,
                                            'user_id': str(user.id),
                                            'original_session_id': pending_response.get('original_session_id')
                                        }
                                        print(f"💾 Stored pending response info for session {session_id}, waiting for client_ready signal", flush=True)
                                        
                                        # Also send with a fallback delay in case client_ready is not received
                                        def send_pending_response_fallback():
                                            import eventlet
                                            # Fallback: send after 3 seconds if client_ready wasn't received
                                            eventlet.sleep(3.0)
                                            
                                            # Check if still pending (not sent via client_ready)
                                            if session_id in getattr(socketio, '_pending_responses', {}):
                                                current_session = get_session(session_id)
                                                if not current_session:
                                                    print(f"⚠️ Session {session_id} no longer exists in fallback, keeping pending response in Redis", flush=True)
                                                    # Clean up pending response info but keep in Redis
                                                    try:
                                                        del socketio._pending_responses[session_id]
                                                    except:
                                                        pass
                                                    return
                                                
                                                # Check if client is actually connected
                                                try:
                                                    participants = list(socketio.server.manager.get_participants('/voice', session_id))
                                                    if not participants or len(participants) == 0:
                                                        print(f"⚠️ Client not in Socket.IO room in fallback, keeping pending response in Redis", flush=True)
                                                        # Clean up pending response info but keep in Redis
                                                        try:
                                                            del socketio._pending_responses[session_id]
                                                        except:
                                                            pass
                                                        return
                                                except Exception as room_check_error:
                                                    print(f"⚠️ Error checking Socket.IO room in fallback: {room_check_error}", flush=True)
                                                
                                                print(f"📤 Sending pending response via fallback (3s delay) to session {session_id}", flush=True)
                                                
                                                # Use callback to verify delivery before clearing
                                                def fallback_delivery_callback(ack_data):
                                                    if ack_data:
                                                        print(f"✅ Fallback pending response delivery confirmed for session {session_id}", flush=True)
                                                        # Only clear if delivery confirmed
                                                        try:
                                                            redis_manager.redis_client.delete(redis_key)
                                                            if session_id in getattr(socketio, '_pending_responses', {}):
                                                                del socketio._pending_responses[session_id]
                                                            print(f"✅ Pending response cleared (fallback, delivery confirmed) for user {user.id}", flush=True)
                                                            sentry_capture_voice_event("pending_response_delivered", session_id, str(user.id), details={"original_session": pending_response.get('original_session_id'), "method": "fallback"})
                                                        except Exception as cleanup_error:
                                                            print(f"⚠️ Error cleaning up pending response: {cleanup_error}", flush=True)
                                                    else:
                                                        print(f"⚠️ Fallback pending response delivery NOT confirmed, keeping in Redis", flush=True)
                                                        # Clean up pending response info but keep in Redis
                                                        try:
                                                            if session_id in getattr(socketio, '_pending_responses', {}):
                                                                del socketio._pending_responses[session_id]
                                                        except:
                                                            pass
                                                
                                                socketio.emit('agent_response', {
                                                    'success': True,
                                                    'text': pending_response['text'],
                                                    'audio': pending_response['audio'],
                                                    'pending': True
                                                }, namespace='/voice', room=session_id, callback=fallback_delivery_callback)
                                        
                                        socketio.start_background_task(send_pending_response_fallback)
                                except Exception as pending_error:
                                    print(f"⚠️ Error checking/sending pending response: {pending_error}", flush=True)
                                    import traceback
                                    traceback.print_exc()
                            else:
                                print(f"❌ Failed to update session in Redis: {user.email}")
                                sentry_capture_redis_operation("update_session", session_id, False, "Redis update_session returned False")
                                # Fallback to in-memory
                                active_sessions[session_id]['authenticated'] = True
                                active_sessions[session_id]['user_id'] = str(user.id)
                                active_sessions[session_id]['user_name'] = user.first_name
                                print(f"✅ Authentication stored in memory (Redis fallback): {user.email}")
                                sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "memory_fallback", "re_authentication": was_already_authenticated})
                        else:
                            # Fallback to in-memory
                            active_sessions[session_id]['authenticated'] = True
                            active_sessions[session_id]['user_id'] = str(user.id)
                            active_sessions[session_id]['user_name'] = user.first_name
                            print(f"✅ Authentication stored in memory: {user.email}")
                            sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "memory", "re_authentication": was_already_authenticated})
                    except Exception as redis_error:
                        print(f"❌ Redis error during authentication: {redis_error}")
                        sentry_capture_redis_operation("update_session", session_id, False, str(redis_error))
                        # Fallback to in-memory storage
                        active_sessions[session_id]['authenticated'] = True
                        active_sessions[session_id]['user_id'] = str(user.id)
                        active_sessions[session_id]['user_name'] = user.first_name
                        print(f"✅ Authentication stored in memory (Redis error fallback): {user.email}")
                        sentry_capture_voice_event("authentication_success", session_id, str(user.id), {"user_name": user.first_name, "storage": "memory_error_fallback", "re_authentication": was_already_authenticated})
                    
                    emit('authenticated', {
                        'success': True,
                        'user_name': user.first_name,
                        'message': f"Welcome back, {user.first_name}!" if not was_already_authenticated else f"Reconnected, {user.first_name}!"
                    })
                    
                    # Track recent authentication for re-authentication detection
                    try:
                        if redis_manager.is_available() and not was_already_authenticated:
                            # Store recent authentication timestamp (5 minute TTL)
                            recent_auth_key = f"recent_auth:{user_id_str}"
                            redis_manager.redis_client.setex(recent_auth_key, 300, str(time.time()))
                    except Exception as track_error:
                        print(f"⚠️ Error tracking recent authentication: {track_error}")
                    
                    # Only send welcome greeting on first authentication, not on re-authentication
                    if not was_already_authenticated:
                        # Send welcome greeting with audio (background task)
                        socketio.start_background_task(
                            send_welcome_greeting, 
                            session_id, 
                            user.first_name
                        )
                    else:
                        print(f"⏭️ Skipping welcome greeting (re-authentication)")
                else:
                    # Authentication failed
                    print(f"❌ Authentication failed: Invalid PIN")
                    sentry_capture_voice_event("authentication_failed", session_id, details={"reason": "invalid_pin"})
                    emit('authenticated', {
                        'success': False,
                        'message': "Invalid PIN. Please try again."
                    })
        
        except Exception as e:
            print(f"❌ Authentication error: {e}")
            sentry_capture_voice_event("authentication_error", session_id, details={"error": str(e)})
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
            emit('authenticated', {
                'success': False,
                'message': "Authentication error. Please try again."
            })
    
    @socketio.on('client_ready', namespace='/voice')
    def handle_client_ready(data):
        """Handle client ready signal - send pending responses if any"""
        session_id = request.sid
        print(f"✅ Client ready signal received from session {session_id}", flush=True)
        
        # Check if there's a pending response for this session
        if hasattr(socketio, '_pending_responses') and session_id in socketio._pending_responses:
            pending_info = socketio._pending_responses[session_id]
            pending_response = pending_info['response']
            redis_key = pending_info['redis_key']
            user_id = pending_info['user_id']
            original_session_id = pending_info.get('original_session_id')
            
            print(f"📤 Sending pending response to ready client (session {session_id})", flush=True)
            print(f"📤 Response text length: {len(pending_response.get('text', ''))}", flush=True)
            print(f"📤 Response audio length: {len(pending_response.get('audio', ''))}", flush=True)
            
            # Verify session exists and client is actually connected before sending
            current_session = get_session(session_id)
            if not current_session:
                print(f"⚠️ Session {session_id} no longer exists, cannot send pending response", flush=True)
                # Clean up pending response info, but keep in Redis for next reconnect
                try:
                    del socketio._pending_responses[session_id]
                except:
                    pass
                return
            
            # Check if client is actually in the Socket.IO room (actually connected)
            try:
                participants = list(socketio.server.manager.get_participants('/voice', session_id))
                if not participants or len(participants) == 0:
                    print(f"⚠️ Client not in Socket.IO room for session {session_id}, cannot send pending response", flush=True)
                    # Clean up pending response info, but keep in Redis for next reconnect
                    try:
                        del socketio._pending_responses[session_id]
                    except:
                        pass
                    return
                print(f"✅ Client is in Socket.IO room for session {session_id} ({len(participants)} participant(s))", flush=True)
            except Exception as room_check_error:
                print(f"⚠️ Error checking Socket.IO room: {room_check_error}", flush=True)
                # Continue anyway, but log the error
            
            # Send the pending response with callback to verify delivery
            def delivery_callback(ack_data):
                if ack_data:
                    print(f"✅ Pending response delivery confirmed by client for session {session_id}", flush=True)
                    # Clean up after confirmed delivery
                    try:
                        if redis_manager.is_available():
                            redis_manager.redis_client.delete(redis_key)
                        if hasattr(socketio, '_pending_responses') and session_id in socketio._pending_responses:
                            del socketio._pending_responses[session_id]
                        print(f"✅ Pending response cleared for user {user_id} (via client_ready, delivery confirmed)", flush=True)
                        sentry_capture_voice_event("pending_response_delivered", session_id, user_id, details={"original_session": original_session_id, "method": "client_ready", "delivery_confirmed": True})
                    except Exception as cleanup_error:
                        print(f"⚠️ Error cleaning up pending response: {cleanup_error}", flush=True)
                else:
                    print(f"⚠️ Pending response delivery NOT confirmed for session {session_id}", flush=True)
                    # Keep pending response in case we need to retry
                    sentry_capture_voice_event("pending_response_delivery_failed", session_id, user_id, details={"original_session": original_session_id, "method": "client_ready"})
            
            socketio.emit('agent_response', {
                'success': True,
                'text': pending_response['text'],
                'audio': pending_response['audio'],
                'pending': True  # Flag to indicate this is a pending response
            }, namespace='/voice', room=session_id, callback=delivery_callback)
            
            print(f"📤 Pending response event emitted to session {session_id} (waiting for delivery confirmation)", flush=True)
    
    
    @socketio.on('start_recording', namespace='/voice')
    def handle_start_recording():
        """Start audio recording"""
        session_id = request.sid
        
        # Get session data
        session_data = None
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if not session_data:
                emit('error', {'message': 'Session not found'})
                return
        else:
            if session_id not in active_sessions:
                emit('error', {'message': 'Session not found'})
                return
            session_data = active_sessions[session_id]
        
        # Check authentication
        is_authenticated = session_data.get('authenticated') == 'True' if redis_manager.is_available() else session_data.get('authenticated', False)
        if not is_authenticated:
            emit('error', {'message': 'Please authenticate first'})
            return
        
        print(f"🎤 Recording started: {session_id}")
        
        # Update recording state and clear audio buffer
        if redis_manager.is_available():
            # Clear the audio buffer completely
            redis_client = redis_manager.redis_client
            if redis_client:
                redis_client.hset(f"session:{session_id}", "audio_buffer", "")
                redis_client.hset(f"session:{session_id}", "is_recording", "True")
                print(f"🔍 Debug: cleared Redis audio buffer for session: {session_id}")
            else:
                update_session(session_id, {
                    'is_recording': 'True',
                    'audio_buffer': ''  # Start with empty string for base64 concatenation
                })
        else:
            active_sessions[session_id]['is_recording'] = True
            active_sessions[session_id]['audio_buffer'] = b''  # Start with empty bytes for binary concatenation
            print(f"🔍 Debug: cleared in-memory audio buffer for session: {session_id}")
        
        emit('recording_started', {'success': True})
    
    
    @socketio.on('audio_data', namespace='/voice')
    def handle_audio_data(data):
        """Receive audio data chunks from client"""
        session_id = request.sid
        
        # Get session data
        session_data = None
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if not session_data:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "audio_data"})
                return
        else:
            if session_id not in active_sessions:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "audio_data", "storage": "memory"})
                return
            session_data = active_sessions[session_id]
        
        # Check if recording
        is_recording = session_data.get('is_recording') == 'True' if redis_manager.is_available() else session_data.get('is_recording', False)
        if not is_recording:
            sentry_capture_voice_event("audio_received_not_recording", session_id, details={"is_recording": is_recording})
            return
        
        # Append audio chunk to buffer
        audio_chunk = base64.b64decode(data['audio'])
        print(f"🔍 Debug: received audio chunk: {len(audio_chunk)} bytes")
        
        try:
            if redis_manager.is_available():
                # For Redis, we need to handle binary data differently
                # Store as base64 string in Redis
                current_buffer = session_data.get('audio_buffer', '')
                
                # Decode current buffer to binary, append new chunk, then re-encode
                if current_buffer:
                    try:
                        # Decode current buffer to binary
                        current_binary = base64.b64decode(current_buffer)
                        print(f"🔍 Debug: current buffer decoded to binary, length: {len(current_binary)} bytes")
                        
                        # Append new chunk to binary data
                        combined_binary = current_binary + audio_chunk
                        print(f"🔍 Debug: combined binary length: {len(combined_binary)} bytes")
                        
                        # Re-encode to base64
                        updated_buffer = base64.b64encode(combined_binary).decode('utf-8')
                        print(f"🔍 Debug: re-encoded to base64, length: {len(updated_buffer)} chars")
                        
                    except Exception as e:
                        print(f"⚠️ Error processing current buffer, using only new chunk: {e}")
                        # If current buffer is corrupted, use only new chunk
                        updated_buffer = base64.b64encode(audio_chunk).decode('utf-8')
                        print(f"🔍 Debug: using only new chunk, base64 length: {len(updated_buffer)} chars")
                else:
                    # No current buffer, just encode new chunk
                    updated_buffer = base64.b64encode(audio_chunk).decode('utf-8')
                    print(f"🔍 Debug: new chunk encoded to base64, length: {len(updated_buffer)} chars")
                
                # Validate the final buffer
                try:
                    test_decode = base64.b64decode(updated_buffer)
                    print(f"🔍 Debug: final buffer validation - decoded length: {len(test_decode)} bytes")
                except Exception as e:
                    print(f"❌ Final buffer validation failed: {e}")
                    # This should not happen, but if it does, use only new chunk
                    updated_buffer = base64.b64encode(audio_chunk).decode('utf-8')
                    print(f"🔍 Debug: fallback to new chunk only, length: {len(updated_buffer)} chars")
                
                # Use Redis append operation for better performance
                try:
                    # Get the Redis client directly for append operation
                    redis_client = redis_manager.redis_client
                    if redis_client:
                        # Use Redis HSET to update the audio buffer
                        redis_client.hset(f"session:{session_id}", "audio_buffer", updated_buffer)
                        print(f"🔍 Debug: updated Redis audio buffer: {len(updated_buffer)} chars")
                        sentry_capture_redis_operation("update_audio_buffer", session_id, True)
                    else:
                        # Fallback to update_session method
                        success = update_session(session_id, {'audio_buffer': updated_buffer})
                        if success:
                            print(f"🔍 Debug: updated Redis audio buffer (fallback): {len(updated_buffer)} chars")
                            sentry_capture_redis_operation("update_audio_buffer", session_id, True)
                        else:
                            print(f"❌ Failed to update Redis audio buffer")
                            sentry_capture_redis_operation("update_audio_buffer", session_id, False, "Redis update_session returned False")
                except Exception as redis_error:
                    print(f"❌ Redis direct operation failed: {redis_error}")
                    # Fallback to update_session method
                    success = update_session(session_id, {'audio_buffer': updated_buffer})
                    if success:
                        print(f"🔍 Debug: updated Redis audio buffer (error fallback): {len(updated_buffer)} chars")
                        sentry_capture_redis_operation("update_audio_buffer", session_id, True)
                    else:
                        print(f"❌ Failed to update Redis audio buffer (error fallback)")
                        sentry_capture_redis_operation("update_audio_buffer", session_id, False, f"Redis error: {redis_error}")
            else:
                # In-memory storage
                active_sessions[session_id]['audio_buffer'] += audio_chunk
                print(f"🔍 Debug: updated in-memory audio buffer: {len(active_sessions[session_id]['audio_buffer'])} bytes")
                sentry_capture_voice_event("audio_buffer_updated", session_id, details={"storage": "memory", "buffer_size": len(active_sessions[session_id]['audio_buffer'])})
        except Exception as e:
            print(f"❌ Error updating audio buffer: {e}")
            sentry_capture_redis_operation("update_audio_buffer", session_id, False, str(e))
            if SENTRY_AVAILABLE:
                sentry_sdk.capture_exception(e)
    
    
    @socketio.on('stop_recording', namespace='/voice')
    def handle_stop_recording(data=None):
        """Stop recording and process audio"""
        session_id = request.sid
        
        # Capture stop recording event in Sentry
        sentry_capture_voice_event("stop_recording", session_id)
        
        # Get session data
        session_data = None
        if redis_manager.is_available():
            session_data = get_session(session_id)
            if not session_data:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "stop_recording"})
                emit('error', {'message': 'Session not found'})
                return
        else:
            if session_id not in active_sessions:
                sentry_capture_voice_event("session_not_found", session_id, details={"operation": "stop_recording", "storage": "memory"})
                emit('error', {'message': 'Session not found'})
                return
            session_data = active_sessions[session_id]
        
        # Check if recording
        is_recording = session_data.get('is_recording') == 'True' if redis_manager.is_available() else session_data.get('is_recording', False)
        if not is_recording:
            sentry_capture_voice_event("stop_recording_not_recording", session_id, details={"is_recording": is_recording})
            emit('error', {'message': 'Not recording'})
            return
        
        print(f"🛑 Recording stopped: {session_id}")
        
        # Update recording state
        try:
            if redis_manager.is_available():
                success = update_session(session_id, {'is_recording': 'False'})
                if success:
                    sentry_capture_redis_operation("update_recording_state", session_id, True)
                else:
                    sentry_capture_redis_operation("update_recording_state", session_id, False, "Redis update_session returned False")
            else:
                session_data['is_recording'] = False
                sentry_capture_voice_event("recording_state_updated", session_id, details={"storage": "memory"})
        except Exception as e:
            print(f"❌ Error updating recording state: {e}")
            sentry_capture_redis_operation("update_recording_state", session_id, False, str(e))
        
        # Get audio buffer - now from client data or session
        audio_buffer = None
        
        # Check if audio data is provided directly from client
        if data and 'audio' in data:
            try:
                # Preserve base64 for Redis audio player, and decode for processing
                audio_buffer_b64_from_client = data['audio']
                audio_buffer = base64.b64decode(audio_buffer_b64_from_client)
                print(f"🎵 Received complete WebM blob from client: {len(audio_buffer)} bytes")
                sentry_capture_voice_event("audio_blob_received", session_id, details={"buffer_size": len(audio_buffer), "source": "client"})

                # Store the complete base64 blob into Redis/in-memory for the audio player tool
                try:
                    if redis_manager.is_available():
                        stored = update_session(session_id, {'audio_buffer': audio_buffer_b64_from_client})
                        if stored:
                            print(f"💾 Stored complete audio blob in Redis for session {session_id}: {len(audio_buffer_b64_from_client)} chars")
                            sentry_capture_redis_operation("store_audio_blob_on_stop", session_id, True)
                        else:
                            print("⚠️ Redis update_session returned False while storing audio blob")
                            sentry_capture_redis_operation("store_audio_blob_on_stop", session_id, False, "update_session returned False")
                    else:
                        session_data['audio_buffer'] = audio_buffer_b64_from_client
                        print(f"💾 Stored complete audio blob in memory for session {session_id}: {len(audio_buffer_b64_from_client)} chars")
                        sentry_capture_voice_event("audio_blob_stored_memory", session_id, details={"length": len(audio_buffer_b64_from_client)})
                except Exception as store_err:
                    print(f"⚠️ Failed to store audio blob for audio player: {store_err}")
                    sentry_capture_redis_operation("store_audio_blob_on_stop", session_id, False, str(store_err))
            except Exception as decode_error:
                print(f"❌ Error decoding client audio blob: {decode_error}")
                sentry_capture_voice_event("audio_decode_error", session_id, details={"error": str(decode_error), "source": "client"})
                emit('transcription', {
                    'success': False,
                    'message': 'Error decoding audio data.'
                })
                return
        else:
            # Fallback to session buffer (legacy)
            try:
                if redis_manager.is_available():
                    audio_buffer_b64 = session_data.get('audio_buffer', '')
                    if not audio_buffer_b64:
                        print("❌ No audio data in Redis session")
                        sentry_capture_voice_event("no_audio_data", session_id, details={"storage": "redis"})
                        emit('transcription', {
                            'success': False,
                            'message': 'No audio data received.'
                        })
                        return
                    
                    audio_buffer = base64.b64decode(audio_buffer_b64)
                    print(f"🔍 Debug: decoded session audio_buffer length: {len(audio_buffer)}")
                    sentry_capture_voice_event("audio_buffer_retrieved", session_id, details={"storage": "redis", "buffer_size": len(audio_buffer)})
                else:
                    audio_buffer = session_data['audio_buffer']
                    print(f"🔍 Debug: in-memory audio_buffer length: {len(audio_buffer)}")
                    sentry_capture_voice_event("audio_buffer_retrieved", session_id, details={"storage": "memory", "buffer_size": len(audio_buffer)})
            except Exception as e:
                print(f"❌ Error retrieving session audio buffer: {e}")
                sentry_capture_voice_event("audio_buffer_error", session_id, details={"error": str(e)})
                if SENTRY_AVAILABLE:
                    sentry_sdk.capture_exception(e)
                emit('error', {'message': 'Error retrieving audio data'})
                return
        
        # Check minimum audio length for meaningful speech recognition
        # WebRTC chunks are very small, so we need a much lower threshold
        min_audio_length = 10000  # Much lower threshold for WebRTC
        if len(audio_buffer) < min_audio_length:
            sentry_capture_voice_event("audio_too_short", session_id, details={"buffer_size": len(audio_buffer), "threshold": min_audio_length})
            emit('transcription', {
                'success': False,
                'message': f'Audio too short ({len(audio_buffer)} bytes). Please speak longer. Try saying "Create a todo task to buy groceries" and hold the button much longer.'
            })
            return
        
        # Analyze audio buffer to understand what's in it (do not mutate original buffer)
        try:
            # If this is WebM (EBML header), skip PCM-based analysis
            is_webm = len(audio_buffer) >= 4 and audio_buffer[:4] == b"\x1a\x45\xdf\xa3"
            if not is_webm:
                import numpy as np
                analysis_buffer = audio_buffer
                if len(analysis_buffer) % 2 != 0:
                    analysis_buffer = analysis_buffer[:-1]
                
                if len(analysis_buffer) > 0:
                    audio_data = np.frombuffer(analysis_buffer, dtype=np.int16)
                    rms = np.sqrt(np.mean(audio_data.astype(np.float32) ** 2))
                    unique_values = len(np.unique(audio_data))
                    max_val = np.max(audio_data)
                    min_val = np.min(audio_data)
                    
                    print(f"🔍 Audio Analysis: RMS={rms:.2f}, Unique={unique_values}, Range=[{min_val}, {max_val}], Samples={len(audio_data)}")
                    
                    # Check for silence
                    if rms < 100:
                        print("⚠️ Audio appears to be silence")
                        emit('transcription', {
                            'success': False,
                            'message': 'No speech detected. Please speak clearly into your microphone.'
                        })
                        return
                    
                    # Check for constant values
                    if unique_values < 10:
                        print("⚠️ Audio has very few unique values - might be constant signal")
                        emit('transcription', {
                            'success': False,
                            'message': 'Audio appears to be constant signal. Please check your microphone.'
                        })
                        return
        except Exception as e:
            print(f"⚠️ Audio analysis failed: {e}")
        
        # Process audio asynchronously
        sentry_capture_voice_event("audio_processing_started", session_id, details={"buffer_size": len(audio_buffer)})
        socketio.start_background_task(process_audio_async, session_id, audio_buffer)
    
    
    def send_welcome_greeting(session_id, user_name):
        """Send welcome greeting with TTS audio after authentication"""
        with flask_app.app_context():
            try:
                print(f"🎤 Generating welcome greeting for {user_name}")
                
                # Generate welcome message
                welcome_text = f"Welcome back, {user_name}! I'm your Convonet productivity assistant. How can I help you today?"
                
                # Generate TTS audio using Deepgram
                deepgram_tts = get_deepgram_tts_service()
                audio_bytes = deepgram_tts.synthesize_speech(welcome_text, voice="aura-asteria-en")
                
                if not audio_bytes:
                    raise Exception("Deepgram TTS failed to generate audio")
                
                # Convert to base64
                audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                
                # Send to client
                socketio.emit('welcome_greeting', {
                    'text': welcome_text,
                    'audio': audio_base64
                }, namespace='/voice', room=session_id)
                
                print(f"✅ Welcome greeting sent to {user_name}")
                
            except Exception as e:
                print(f"❌ Error generating welcome greeting: {e}")
    
    
    def process_audio_async(session_id, audio_buffer):
        """Process audio in background task"""
        import sys
        print(f"🚀 process_audio_async STARTED for session: {session_id}, buffer size: {len(audio_buffer)}", flush=True)
        sys.stdout.flush()
        # Use the stored Flask app instance for application context
        print(f"🔧 Entering Flask app context...", flush=True)
        sys.stdout.flush()
        with flask_app.app_context():
            print(f"✅ Flask app context entered", flush=True)
            sys.stdout.flush()
            try:
                # Get session data
                print(f"🔍 Getting session data from Redis/memory...", flush=True)
                sys.stdout.flush()
                session = None
                session_record = None
                if redis_manager.is_available():
                    print(f"📦 Redis is available, getting session from Redis...", flush=True)
                    sys.stdout.flush()
                    session_data = get_session(session_id)
                    if not session_data:
                        sentry_capture_voice_event("session_not_found_processing", session_id, details={"operation": "audio_processing"})
                        return
                    session_record = session_data
                    # Convert Redis session data to expected format
                    session = {
                        'user_id': session_data.get('user_id'),
                        'user_name': session_data.get('user_name')
                    }
                else:
                    session = active_sessions.get(session_id)
                    if not session:
                        sentry_capture_voice_event("session_not_found_processing", session_id, details={"operation": "audio_processing", "storage": "memory"})
                        return
                    session_record = session
                
                print(f"🎧 Processing audio: {len(audio_buffer)} bytes")
                sentry_capture_voice_event("audio_processing_started", session_id, session.get('user_id'), details={"buffer_size": len(audio_buffer)})
                
                # Step 1: Transcribe audio using AssemblyAI
                socketio.emit('status', {'message': 'Transcribing with Deepgram...'}, namespace='/voice', room=session_id)
                sentry_capture_voice_event("transcription_started", session_id, session.get('user_id'), details={"method": "deepgram"})
                
                # Use Deepgram for transcription (WebRTC-optimized solution)
                print(f"🎧 Deepgram: Processing audio buffer: {len(audio_buffer)} bytes")
                
                # Use Deepgram integration with automatic language detection
                # STT (transcription) should always use auto-detection to support multi-language
                # TTS language preference is separate and handled later
                import sys
                print(f"🔧 About to call transcribe_audio_with_deepgram_webrtc with auto language detection...", flush=True)
                sys.stdout.flush()
                try:
                    # Always use None/"auto" for automatic language detection (supports 30+ languages)
                    # This allows Deepgram to detect Korean, Japanese, Spanish, etc. automatically
                    transcribed_text = transcribe_audio_with_deepgram_webrtc(audio_buffer, language=None)
                    print(f"✅ transcribe_audio_with_deepgram_webrtc returned: {transcribed_text[:50] if transcribed_text else 'None'}...", flush=True)
                    sys.stdout.flush()
                except Exception as e:
                    print(f"❌ Deepgram integration failed: {e}", flush=True)
                    sys.stdout.flush()
                    import traceback
                    traceback.print_exc()
                    socketio.emit('error', {'message': 'Deepgram service not available. Please check configuration.'}, namespace='/voice', room=session_id)
                    sentry_capture_voice_event("transcription_failed", session_id, session.get('user_id'), details={"method": "deepgram", "error": str(e)})
                    return
                
                print(f"🔍 Checking if transcribed_text is empty...", flush=True)
                sys.stdout.flush()
                if not transcribed_text:
                    print("❌ Deepgram transcription failed")
                    socketio.emit('error', {
                        'message': 'Transcription failed. Please try speaking more clearly or check your microphone.',
                        'details': 'The audio was captured but no speech was detected. Make sure you are speaking clearly into your microphone.'
                    }, namespace='/voice', room=session_id)
                    sentry_capture_voice_event("transcription_failed", session_id, session.get('user_id'), details={"method": "deepgram"})
                    return
                
                print(f"✅ Deepgram transcription successful: {transcribed_text}", flush=True)
                sys.stdout.flush()
                print(f"📝 About to call sentry_capture_voice_event...", flush=True)
                sys.stdout.flush()
                sentry_capture_voice_event("transcription_completed", session_id, session.get('user_id'), details={"text_length": len(transcribed_text), "method": "deepgram"})
                print(f"✅ sentry_capture_voice_event completed", flush=True)
                sys.stdout.flush()
                
                # Send transcription to client
                print(f"📤 Sending transcription to client...", flush=True)
                sys.stdout.flush()
                socketio.emit('transcription', {
                    'success': True,
                    'text': transcribed_text,
                    'method': 'assemblyai'
                }, namespace='/voice', room=session_id)
                print(f"✅ Transcription sent to client", flush=True)
                sys.stdout.flush()
                
                print(f"🔍 Checking for transfer intent...", flush=True)
                sys.stdout.flush()
                transfer_requested = has_transfer_intent(transcribed_text)
                print(f"✅ Transfer intent check complete: {transfer_requested}", flush=True)
                sys.stdout.flush()
                
                def start_transfer_flow(target_extension: str, department: str, reason: str, source: str = "agent"):
                    print(f"🔄 Transfer requested: Extension={target_extension}, Department={department}, Reason={reason}")
                    if is_transfer_in_progress(session_id, session_record):
                        print(f"⚠️ Transfer already in progress for session {session_id}, skipping duplicate request")
                        return
                    set_transfer_flag(session_id, True, session_record)
                    sentry_capture_voice_event("transfer_initiated", session_id, session.get('user_id'), details={
                        "extension": target_extension,
                        "department": department,
                        "reason": reason,
                        "platform": "webrtc",
                        "source": source
                    })
                    
                    # Cache customer profile with call_id=session_id for WebRTC calls
                    cache_call_center_profile(target_extension, session_record, call_id=session_id)
                    
                    transfer_instructions = {
                        'extension': target_extension,
                        'department': department,
                        'reason': reason
                    }
                    
                    transfer_success, transfer_details = initiate_agent_transfer(
                        session_id=session_id,
                        extension=target_extension,
                        department=department,
                        reason=reason,
                        session_data=session_record
                    )
                    if not transfer_success:
                        set_transfer_flag(session_id, False, session_record)

                    transfer_message_text = f"I'm transferring you to {department} (extension {target_extension})."

                    socketio.emit('transfer_initiated', {
                        'success': True,
                        'extension': target_extension,
                        'department': department,
                        'reason': reason,
                        'instructions': transfer_instructions,
                        'message': transfer_message_text,
                        'call_started': transfer_success,
                        'call_details': transfer_details
                    }, namespace='/voice', room=session_id)

                    socketio.emit('transfer_status', {
                        'success': transfer_success,
                        'details': transfer_details
                    }, namespace='/voice', room=session_id)

                    print(f"🔄 Transfer instructions sent to WebRTC client for extension {target_extension}")

                    transfer_message = f"I'm transferring you to {department}. Extension {target_extension}."
                    try:
                        # Generate TTS audio using Deepgram
                        deepgram_tts = get_deepgram_tts_service()
                        audio_bytes = deepgram_tts.synthesize_speech(transfer_message, voice="aura-asteria-en")
                        
                        if not audio_bytes:
                            raise Exception("Deepgram TTS failed to generate audio")
                        
                        audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                        
                        socketio.emit('agent_response', {
                            'success': True,
                            'text': transfer_message,
                            'audio': audio_base64,
                            'transfer': True
                        }, namespace='/voice', room=session_id)
                    except Exception as e:
                        print(f"❌ Error generating TTS for transfer: {e}")
                
                if transfer_requested:
                    print(f"🔄 Transfer requested, starting transfer flow...", flush=True)
                    sys.stdout.flush()
                    start_transfer_flow('2001', 'support', 'User requested transfer to human agent', source="caller_intent")
                    return

                # Step 2: Process with agent
                print(f"🚀 About to emit status message...", flush=True)
                sys.stdout.flush()
                socketio.emit('status', {'message': 'Processing request...'}, namespace='/voice', room=session_id)
                print(f"✅ Status message emitted", flush=True)
                sys.stdout.flush()
                
                print(f"📝 About to call sentry_capture_voice_event for agent_processing_started...", flush=True)
                sys.stdout.flush()
                sentry_capture_voice_event("agent_processing_started", session_id, session.get('user_id'), details={"transcribed_text": transcribed_text})
                print(f"✅ sentry_capture_voice_event for agent_processing_started completed", flush=True)
                sys.stdout.flush()
                
                print(f"🤖 Starting agent processing for: {transcribed_text[:100]}", flush=True)
                sys.stdout.flush()
                print(f"🔧 About to call process_with_agent in separate thread...", flush=True)
                sys.stdout.flush()
                try:
                    print(f"🔧 Setting up ThreadPoolExecutor...", flush=True)
                    sys.stdout.flush()
                    # Use eventlet's spawn_n to run async code in completely separate greenlet
                    # This prevents blocking the main eventlet worker
                    import eventlet
                    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
                    
                    result_container = {'response': None, 'transfer': None, 'error': None, 'done': False}
                    
                    print(f"🔧 Defining run_async_in_thread function...", flush=True)
                    sys.stdout.flush()
                    def run_async_in_thread():
                        """Run async function in a new thread with its own event loop"""
                        import sys
                        print(f"🧵 Thread started for async execution", flush=True)
                        sys.stdout.flush()
                        
                        # Create new event loop for this thread
                        print(f"🔧 Creating new event loop in thread...", flush=True)
                        sys.stdout.flush()
                        new_loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(new_loop)
                        print(f"✅ Event loop created and set", flush=True)
                        sys.stdout.flush()
                        
                        # Use timeout that matches routes.py execution_timeout (15s for Claude/OpenAI, 12s for Gemini)
                        # Add buffer for tool execution which can be slow (MCP calls, API calls, etc.)
                        # Increased to 60s to allow for database operations and external API calls
                        # Tool execution (calendar event creation, database operations) can take time
                        timeout_seconds = 60.0  # Increased from 20s to allow tool execution time
                        try:
                            print(f"🔄 Running process_with_agent in thread (timeout: {timeout_seconds}s)...", flush=True)
                            sys.stdout.flush()
                            result = new_loop.run_until_complete(
                                asyncio.wait_for(
                                    process_with_agent(
                                        transcribed_text,
                                        session['user_id'],
                                        session['user_name'],
                                        socketio=socketio_instance,
                                        session_id=session_id
                                    ),
                                    timeout=timeout_seconds
                                )
                            )
                            print(f"✅ process_with_agent completed in thread", flush=True)
                            sys.stdout.flush()
                            result_container['response'] = result[0]
                            result_container['transfer'] = result[1]
                            result_container['done'] = True
                            return result
                        except asyncio.TimeoutError:
                            print(f"⏱️ Async timeout in thread after {timeout_seconds} seconds", flush=True)
                            sys.stdout.flush()
                            result_container['error'] = 'timeout'
                            result_container['done'] = True
                            raise
                        except Exception as e:
                            print(f"❌ Error in thread: {e}", flush=True)
                            sys.stdout.flush()
                            import traceback
                            traceback.print_exc()
                            result_container['error'] = str(e)
                            result_container['done'] = True
                            raise
                        finally:
                            print(f"🧵 Closing event loop...", flush=True)
                            sys.stdout.flush()
                            try:
                                # Cancel any pending tasks
                                pending = asyncio.all_tasks(new_loop)
                                for task in pending:
                                    task.cancel()
                                new_loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                            except:
                                pass
                            new_loop.close()
                            print(f"🧵 Thread event loop closed", flush=True)
                            sys.stdout.flush()
                    
                    # Run in thread pool with aggressive timeout for Gemini hackathon
                    print(f"🚀 Submitting to ThreadPoolExecutor...", flush=True)
                    sys.stdout.flush()
                    with ThreadPoolExecutor(max_workers=1) as executor:
                        print(f"✅ ThreadPoolExecutor created, submitting task...", flush=True)
                        sys.stdout.flush()
                        future = executor.submit(run_async_in_thread)
                        print(f"✅ Task submitted to executor, future created", flush=True)
                        sys.stdout.flush()
                        # Use 60s timeout to allow for tool execution (database operations, API calls)
                        # Tool execution (MCP calls, API calls, database operations) can take time
                        # Increased from 25s to 60s to handle complex operations like calendar event creation
                        executor_timeout = 90.0  # Increased for MCP tools loading (was 60s)
                        try:
                            print(f"⏳ Waiting for result with {executor_timeout}s timeout...", flush=True)
                            sys.stdout.flush()
                            agent_response, transfer_marker = future.result(timeout=executor_timeout)
                            print(f"🤖 Agent response received: {agent_response[:100] if agent_response else 'None'}", flush=True)
                            sys.stdout.flush()
                        except FutureTimeoutError:
                            print(f"⏱️ ThreadPoolExecutor timed out after {executor_timeout} seconds", flush=True)
                            sys.stdout.flush()
                            agent_response = "I'm sorry, I'm taking too long to process that request. Please try a simpler request or try again."
                            transfer_marker = None
                            # Cancel the future if possible
                            try:
                                future.cancel()
                                print(f"✅ Future cancelled", flush=True)
                                sys.stdout.flush()
                            except:
                                pass
                        except asyncio.TimeoutError as e:
                            print(f"⏱️ Async timeout: {e}", flush=True)
                            sys.stdout.flush()
                            agent_response = "I'm sorry, I'm taking too long to process that request. Please try a simpler request or switch to Claude model."
                            transfer_marker = None
                        except Exception as e:
                            print(f"❌ Exception in ThreadPoolExecutor: {e}", flush=True)
                            sys.stdout.flush()
                            import traceback
                            traceback.print_exc()
                            agent_response = "I'm sorry, I encountered an error. Please try again."
                            transfer_marker = None
                except asyncio.TimeoutError:
                    print(f"⏱️ Agent processing timed out after 18 seconds (async timeout)")
                    agent_response = "I'm sorry, I'm taking too long to process that request. Please try a simpler request."
                    transfer_marker = None
                except Exception as e:
                    print(f"❌ Error in agent processing: {e}")
                    import traceback
                    traceback.print_exc()
                    agent_response = "I'm sorry, I encountered an error. Please try again."
                    transfer_marker = None
                sentry_capture_voice_event("agent_processing_completed", session_id, session.get('user_id'), details={"response_length": len(agent_response)})
                
                effective_marker = transfer_marker or (agent_response if isinstance(agent_response, str) and agent_response.startswith("TRANSFER_INITIATED:") else None)
                if effective_marker:
                    if transfer_requested:
                        marker_data = effective_marker.replace("TRANSFER_INITIATED:", "")
                        parts = marker_data.split("|")
                        target_extension = parts[0] if len(parts) > 0 else '2001'
                        department = parts[1] if len(parts) > 1 else 'support'
                        reason = parts[2] if len(parts) > 2 else 'User requested transfer'
                        start_transfer_flow(target_extension, department, reason)
                        return
                    else:
                        print("Transfer marker detected but caller did not request a human. Ignoring marker.")
                        agent_response = agent_response if not isinstance(agent_response, str) or not agent_response.startswith("TRANSFER_INITIATED:") else "Let me know how else I can help."
                
                # Step 3: Convert response to speech using ElevenLabs (with Deepgram fallback)
                socketio.emit('status', {'message': 'Generating speech...'}, namespace='/voice', room=session_id)
                sentry_capture_voice_event("tts_generation_started", session_id, session.get('user_id'))
                
                # Get user preferences
                user_id = session.get('user_id')
                print(f"🔍 TTS Debug: ELEVENLABS_AVAILABLE={ELEVENLABS_AVAILABLE}, user_id={user_id}", flush=True)
                
                voice_prefs = get_voice_preferences() if ELEVENLABS_AVAILABLE else None
                print(f"🔍 TTS Debug: voice_prefs={voice_prefs is not None}", flush=True)
                
                audio_bytes = None
                tts_provider = "deepgram"  # Default fallback
                
                # Try ElevenLabs first if available and enabled
                if ELEVENLABS_AVAILABLE and voice_prefs:
                    print(f"🔍 TTS Debug: Entering ElevenLabs block", flush=True)
                    try:
                        elevenlabs = get_elevenlabs_service()
                        print(f"🔍 TTS Debug: elevenlabs service obtained, is_available()={elevenlabs.is_available() if elevenlabs else False}", flush=True)
                        if elevenlabs.is_available():
                            prefs = voice_prefs.get_user_preferences(user_id) if user_id else voice_prefs._get_default_preferences()
                            print(f"🔍 TTS Debug: prefs={prefs}, use_elevenlabs={prefs.get('use_elevenlabs', True)}", flush=True)
                            
                            # Check if user wants ElevenLabs
                            if prefs.get("use_elevenlabs", True):
                                print(f"🔍 TTS Debug: use_elevenlabs is True, proceeding with ElevenLabs", flush=True)
                                voice_id = prefs.get("voice_id")
                                language = prefs.get("language", "en")
                                emotion_enabled = prefs.get("emotion_enabled", True)
                                
                                # Detect emotion if enabled
                                if emotion_enabled:
                                    emotion_detector = get_emotion_detector()
                                    # Get transcribed_text from outer scope
                                    user_input_text = transcribed_text if 'transcribed_text' in locals() else ""
                                    emotion = emotion_detector.detect_emotion_from_context(
                                        user_input=user_input_text,
                                        agent_response=agent_response
                                    )
                                    print(f"🎭 Using ElevenLabs with emotion: {emotion.value}", flush=True)
                                    audio_bytes = elevenlabs.synthesize_with_emotion(
                                        text=agent_response,
                                        emotion=emotion,
                                        voice_id=voice_id
                                    )
                                else:
                                    # Use multilingual if language is not English
                                    if language != "en":
                                        print(f"🌍 Using ElevenLabs multilingual for {language}", flush=True)
                                        audio_bytes = elevenlabs.synthesize_multilingual(
                                            text=agent_response,
                                            language=language,
                                            voice_id=voice_id
                                        )
                                    else:
                                        print(f"🔊 Using ElevenLabs standard TTS", flush=True)
                                        audio_bytes = elevenlabs.synthesize(
                                            text=agent_response,
                                            voice_id=voice_id
                                        )
                                
                                if audio_bytes:
                                    tts_provider = "elevenlabs"
                                    print(f"✅ ElevenLabs TTS successful: {len(audio_bytes)} bytes", flush=True)
                                    # Update status to show ElevenLabs is being used
                                    socketio.emit('status', {'message': 'Generating speech with ElevenLabs...'}, namespace='/voice', room=session_id)
                    except Exception as e:
                        print(f"⚠️ ElevenLabs TTS failed, falling back to Deepgram: {e}", flush=True)
                        import traceback
                        traceback.print_exc()
                else:
                    if not ELEVENLABS_AVAILABLE:
                        print(f"🔍 TTS Debug: ELEVENLABS_AVAILABLE is False", flush=True)
                    if not voice_prefs:
                        print(f"🔍 TTS Debug: voice_prefs is None", flush=True)
                
                # Fallback to Deepgram if ElevenLabs failed or not available
                if not audio_bytes:
                    print(f"🔊 Using Deepgram TTS (fallback)", flush=True)
                    # Update status to show Deepgram is being used
                    socketio.emit('status', {'message': 'Generating speech with Deepgram...'}, namespace='/voice', room=session_id)
                    deepgram_tts = get_deepgram_tts_service()
                    audio_bytes = deepgram_tts.synthesize_speech(agent_response, voice="aura-asteria-en")
                    tts_provider = "deepgram"
                
                if not audio_bytes:
                    raise Exception(f"{tts_provider.capitalize()} TTS failed to generate audio")
                
                # Convert speech to base64 for transmission
                audio_base64 = base64.b64encode(audio_bytes).decode('utf-8')
                print(f"🔊 TTS generated: {len(audio_bytes)} bytes, base64: {len(audio_base64)} chars", flush=True)
                print(f"🔊 TTS audio preview: {audio_base64[:100]}...", flush=True)
                sentry_capture_voice_event("tts_generation_completed", session_id, session.get('user_id'), details={"audio_size": len(audio_base64)})
                
                # Send response to client
                print(f"📤 Sending agent_response event to session {session_id}...", flush=True)
                print(f"📤 Response text length: {len(agent_response)}, audio base64 length: {len(audio_base64)}", flush=True)
                
                # Check if session still exists before emitting
                session_still_exists = False
                if redis_manager.is_available():
                    session_data = get_session(session_id)
                    session_still_exists = session_data is not None
                else:
                    session_still_exists = session_id in active_sessions
                
                # Get user_id for pending response storage
                # session variable should be available from earlier in the function (line ~1115)
                user_id = None
                try:
                    # First try from session dict (created from Redis/memory session data)
                    if 'session' in locals() and session:
                        user_id = session.get('user_id')
                        print(f"👤 Got user_id from session dict: {user_id}", flush=True)
                    
                    # If not found, try to get from Redis directly
                    if not user_id:
                        session_data = get_session(session_id)
                        if session_data:
                            user_id = session_data.get('user_id')
                            print(f"👤 Got user_id from Redis session: {user_id}", flush=True)
                except Exception as user_id_error:
                    print(f"⚠️ Error getting user_id for pending response: {user_id_error}", flush=True)
                    import traceback
                    traceback.print_exc()
                
                if user_id:
                    print(f"✅ user_id available for pending response storage: {user_id}", flush=True)
                else:
                    print(f"⚠️ No user_id available for pending response storage (session_id: {session_id})", flush=True)
                    print(f"⚠️ Session dict: {session if 'session' in locals() else 'not in scope'}", flush=True)
                
                if not session_still_exists:
                    print(f"⚠️ Session {session_id} no longer exists (client may have disconnected)", flush=True)
                    
                    # Store pending response for user_id so it can be sent when client reconnects
                    if user_id:
                        try:
                            pending_response = {
                                'text': agent_response,
                                'audio': audio_base64,
                                'created_at': time.time(),
                                'original_session_id': session_id
                            }
                            if redis_manager.is_available():
                                import json
                                redis_key = f"pending_response:{user_id}"
                                redis_manager.redis_client.setex(redis_key, 300, json.dumps(pending_response))  # 5 min TTL
                                print(f"💾 Stored pending response for user_id {user_id} (will be sent on reconnect)", flush=True)
                                sentry_capture_voice_event("pending_response_stored", session_id, user_id, details={"reason": "session_gone_during_tts"})
                            else:
                                # Fallback to in-memory (not ideal but better than losing response)
                                if not hasattr(webrtc_voice_server, 'pending_responses'):
                                    webrtc_voice_server.pending_responses = {}
                                webrtc_voice_server.pending_responses[user_id] = pending_response
                                print(f"💾 Stored pending response in memory for user_id {user_id}", flush=True)
                        except Exception as store_error:
                            print(f"⚠️ Failed to store pending response: {store_error}", flush=True)
                    
                    sentry_capture_voice_event("agent_response_skipped_session_gone", session_id, user_id, details={"reason": "session_no_longer_exists", "pending_stored": user_id is not None})
                else:
                    print(f"✅ Session {session_id} still exists, proceeding with emit", flush=True)
                    try:
                        # Use the global socketio instance
                        emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                        if not emit_socketio:
                            raise Exception("socketio instance not available")
                        
                        # Check if client is actually connected to Socket.IO room (more reliable than callback)
                        # Socket.IO automatically puts each client in a room with their session_id
                        # A "room" in Socket.IO is a channel/group - when you emit to a room, all clients in that room receive the message
                        # Each client is automatically in a room named after their session_id
                        client_actually_connected = False
                        try:
                            # Check if there are any clients in the room for this session
                            # get_participants() returns a generator, so we need to convert to list
                            room_clients = list(emit_socketio.server.manager.get_participants('/voice', session_id))
                            client_actually_connected = len(room_clients) > 0
                            print(f"🔍 Socket.IO room check: {len(room_clients)} client(s) in room {session_id}, connected={client_actually_connected}", flush=True)
                        except Exception as room_check_error:
                            print(f"⚠️ Error checking Socket.IO room: {room_check_error}", flush=True)
                            import traceback
                            traceback.print_exc()
                            # If we can't check, assume connected (fallback to old behavior)
                            client_actually_connected = True
                        
                        if not client_actually_connected:
                            print(f"⚠️ Client not in Socket.IO room (disconnected), storing as pending", flush=True)
                            # Client is not actually connected, store as pending immediately
                            if user_id:
                                try:
                                    import json
                                    pending_response = {
                                        'text': agent_response,
                                        'audio': audio_base64,
                                        'created_at': time.time(),
                                        'original_session_id': session_id
                                    }
                                    redis_key = f"pending_response:{user_id}"
                                    if redis_manager.is_available():
                                        redis_manager.redis_client.setex(redis_key, 300, json.dumps(pending_response))
                                        print(f"💾 Stored pending response for user_id {user_id} (client not in room)", flush=True)
                                        sentry_capture_voice_event("pending_response_stored_no_room", session_id, user_id, details={"reason": "client_not_in_room"})
                                    else:
                                        if not hasattr(webrtc_voice_server, 'pending_responses'):
                                            webrtc_voice_server.pending_responses = {}
                                        webrtc_voice_server.pending_responses[user_id] = pending_response
                                        print(f"💾 Stored pending response in memory for user_id {user_id}", flush=True)
                                except Exception as store_error:
                                    print(f"⚠️ Error storing pending response: {store_error}", flush=True)
                        else:
                            # Client is connected, emit the response
                            print(f"📤 Emitting agent_response to session {session_id} in /voice namespace...", flush=True)
                            
                            # CRITICAL: Store as pending BEFORE emitting as a safety net
                            # Even if client is in room, they might disconnect before receiving the message
                            # We'll clear it if callback confirms delivery
                            if user_id:
                                try:
                                    import json
                                    pending_response = {
                                        'text': agent_response,
                                        'audio': audio_base64,
                                        'created_at': time.time(),
                                        'original_session_id': session_id
                                    }
                                    redis_key = f"pending_response:{user_id}"
                                    if redis_manager.is_available():
                                        redis_manager.redis_client.setex(redis_key, 300, json.dumps(pending_response))
                                        print(f"💾 Stored pending response as backup for user_id {user_id} (will clear if delivery confirmed)", flush=True)
                                except Exception as store_error:
                                    print(f"⚠️ Error storing pending response backup: {store_error}", flush=True)
                            
                            # Simplified callback - clear pending if delivery confirmed
                            def emit_callback(success):
                                if success:
                                    print(f"✅ agent_response callback: delivered to session {session_id}", flush=True)
                                    # Clear pending response if emit succeeded
                                    if user_id:
                                        try:
                                            import json
                                            redis_key = f"pending_response:{user_id}"
                                            if redis_manager.is_available():
                                                redis_manager.redis_client.delete(redis_key)
                                                print(f"🧹 Cleared pending response for user_id {user_id} (response delivered)", flush=True)
                                        except Exception as clear_error:
                                            print(f"⚠️ Error clearing pending response: {clear_error}", flush=True)
                                else:
                                    print(f"⚠️ agent_response callback: delivery failed to session {session_id}", flush=True)
                                    # Pending response already stored before emit, so we're covered
                            
                            emit_socketio.emit('agent_response', {
                                'success': True,
                                'text': agent_response,
                                'audio': audio_base64
                            }, namespace='/voice', room=session_id, callback=emit_callback)
                            print(f"✅ agent_response event emitted to session {session_id} (Socket.IO will handle delivery)", flush=True)
                    except Exception as emit_error:
                        print(f"❌ Error during emit: {emit_error}", flush=True)
                        import traceback
                        traceback.print_exc()
                        # Try to send error notification
                        try:
                            emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                            if emit_socketio:
                                emit_socketio.emit('error', {
                                    'message': f"Error sending response: {str(emit_error)}"
                                }, namespace='/voice', room=session_id)
                        except:
                            pass
                        import traceback
                        traceback.print_exc()
                        # Try sending error to client
                        try:
                            emit_socketio = socketio if socketio else (socketio_instance_global if 'socketio_instance_global' in globals() else None)
                            if emit_socketio:
                                emit_socketio.emit('error', {
                                    'message': f"Error sending response: {str(emit_error)}"
                                }, namespace='/voice', room=session_id)
                        except:
                            pass
                
                sentry_capture_voice_event("audio_processing_completed", session_id, session.get('user_id'), details={"success": True})
            
            except Exception as e:
                print(f"❌ Error processing audio: {e}")
                import traceback
                traceback.print_exc()
                
                sentry_capture_voice_event("audio_processing_error", session_id, session.get('user_id') if 'session' in locals() else None, details={"error": str(e)})
                if SENTRY_AVAILABLE:
                    sentry_sdk.capture_exception(e)
                
                socketio.emit('error', {
                    'message': f"Error processing audio: {str(e)}"
                }, namespace='/voice', room=session_id)


async def process_with_agent(
    text: str, 
    user_id: str, 
    user_name: str,
    socketio=None,
    session_id: str | None = None,
) -> str:
    """Process user input with the agent"""
    try:
        # Capture agent processing start in Sentry
        if SENTRY_AVAILABLE:
            with sentry_sdk.configure_scope() as scope:
                scope.set_tag("component", "webrtc_voice_server")
                scope.set_tag("operation", "agent_processing")
                scope.set_context("agent_processing", {
                    "user_id": user_id,
                    "user_name": user_name,
                    "text_length": len(text),
                    "text_preview": text[:100] + "..." if len(text) > 100 else text
                })
                sentry_sdk.add_breadcrumb(
                    message="Agent processing started",
                    category="agent",
                    level="info"
                )
        
        # Use the same agent processing as Twilio for consistency
        from convonet.routes import _run_agent_async
        
        # Use the same agent processing function as Twilio
        result = await _run_agent_async(
            prompt=text,
            user_id=user_id,
            user_name=user_name,
            reset_thread=False,
            include_metadata=True,
            socketio=socketio,
            session_id=session_id,
        )
        
        if isinstance(result, dict):
            return result.get("response", ""), result.get("transfer_marker")
        return result, None
    
    except asyncio.TimeoutError:
        # Capture timeout in Sentry
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_message("Agent processing timeout", level="warning")
        return "I'm sorry, I'm taking too long to process that request. Please try again.", None
    except Exception as e:
        print(f"❌ Agent error: {e}")
        # Capture agent error in Sentry
        if SENTRY_AVAILABLE:
            sentry_sdk.capture_exception(e)
        return "I'm sorry, I encountered an error. Please try again.", None

