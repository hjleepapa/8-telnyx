from flask import Blueprint, request, jsonify, render_template, Response
from flask_socketio import emit, join_room, leave_room
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph
from typing import Optional
import asyncio
import json
import os
import logging
import time
import sentry_sdk

# Apply nest_asyncio to allow nested event loops (needed for eventlet compatibility)
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass  # nest_asyncio not available, may cause issues with eventlet

from twilio.twiml.voice_response import VoiceResponse, Connect, Gather
from .state import AgentState
from .assistant_graph_todo import get_agent, TodoAgent, MortgageAgent, get_mortgage_agent
from .mortgage_intent_detection import detect_mortgage_intent
from .voice_intent_utils import has_transfer_intent
from .llm_provider_manager import get_llm_provider_manager, LLMProvider
from langchain_mcp_adapters.client import MultiServerMCPClient
from .redis_manager import redis_manager
import uuid

# Import new authentication and team routes (optional - commented out as api_routes moved to archive)
# from .api_routes.auth_routes import auth_bp
# from .api_routes.team_routes import team_bp
# from .api_routes.team_todo_routes import team_todo_bp

# Set up logging
logger = logging.getLogger(__name__)

# Global agent graph cache (initialized on first use)
_agent_graph_cache = None
_agent_graph_model = None  # Track which model was used for the cached graph
_agent_graph_provider = None  # Track which provider was used for the cached graph
_agent_graph_lock = asyncio.Lock()

# Global MCP tools cache (pre-loaded at startup to avoid hangs during requests)
_mcp_tools_cache = None
_mcp_tools_loading = False
_mcp_tools_lock = asyncio.Lock()

# Global MCP tools cache (loaded at startup to avoid hangs during requests)
_mcp_tools_cache = None
_mcp_tools_loading = False
_mcp_tools_lock = asyncio.Lock()

convonet_todo_bp = Blueprint(
    'convonet_todo',
    __name__,
    url_prefix='/convonet_todo',
    template_folder='templates',
    static_folder='static'
)

def get_webhook_base_url():
    """Get the webhook base URL for Twilio webhooks."""
    # Prefer explicit setting, then Render URL, then fallback
    return (
        os.getenv('WEBHOOK_BASE_URL')
        or os.getenv('RENDER_EXTERNAL_URL')
        or 'https://convonet-anthropic.onrender.com'
    )

def get_websocket_url():
    """Get the WebSocket URL for Twilio Media Streams."""
    # Prefer explicit setting, then derive from Render URL, then fallback
    websocket_url = os.getenv('WEBSOCKET_BASE_URL')
    if websocket_url:
        return websocket_url
    
    # Derive from Render URL or use default
    base_url = os.getenv('RENDER_EXTERNAL_URL') or 'https://convonet-anthropic.onrender.com'
    return base_url.replace('https://', 'wss://').replace('http://', 'ws://') + '/convonet_todo/ws'

# --- Twilio Voice Routes ---
@convonet_todo_bp.route('/twilio/call', methods=['POST'])
def twilio_call_webhook():
    """
    Handles incoming calls from Twilio.
    Asks for PIN authentication before allowing access to the assistant.
    """
    # Get the current webhook base URL
    webhook_base_url = get_webhook_base_url()
    
    # Check if this is a continuation of the conversation
    is_continuation = request.args.get('is_continuation', 'false').lower() == 'true'
    # Check if user is already authenticated in this session
    is_authenticated = request.args.get('authenticated', 'false').lower() == 'true'
    
    response = VoiceResponse()
    
    # If not authenticated, ask for PIN
    if not is_authenticated and not is_continuation:
        gather = response.gather(
            input='dtmf speech',  # Accept both DTMF (keypad) and speech
            action='/convonet_todo/twilio/verify_pin',
            method='POST',
            timeout=10,
            finish_on_key='#'  # Press # to finish (for DTMF), no num_digits requirement
        )
        gather.say("Welcome to Convonet productivity assistant. Please enter or say your 4 to 6 digit PIN, then press pound.", voice='Polly.Amy')
        
        response.say("I didn't receive a PIN. Please try again.", voice='Polly.Amy')
        response.redirect('/convonet_todo/twilio/call')
        
        print(f"Generated TwiML for PIN request: {str(response)}")
        return Response(str(response), mimetype='text/xml')
    
    # User is authenticated, proceed with normal conversation
    gather = response.gather(
        input='speech',
            action='/convonet_todo/twilio/process_audio',
        method='POST',
        speech_timeout='auto',
        timeout=10,
        barge_in=True
    )
    
    # Only say the welcome message if this is the first authenticated interaction
    if not is_continuation:
        gather.say("How can I help you today?", voice='Polly.Amy')
    
    # Fallback if no speech is detected
    response.say("I didn't hear anything. Please try again.", voice='Polly.Amy')
    response.redirect('/convonet_todo/twilio/call?is_continuation=true&authenticated=true')
    
    print(f"Generated TwiML for incoming call: {str(response)}")
    return Response(str(response), mimetype='text/xml')

@convonet_todo_bp.route('/twilio/verify_pin', methods=['POST'])
def verify_pin_webhook():
    """
    Verifies the user's PIN and authenticates the session.
    """
    try:
        # Get PIN from either DTMF digits or speech
        pin = request.form.get('Digits', '') or request.form.get('SpeechResult', '')
        call_sid = request.form.get('CallSid', '')
        
        print(f"Verifying PIN for call {call_sid}: {pin}")
        
        if not pin:
            response = VoiceResponse()
            response.say("I didn't receive a PIN. Please try again.", voice='Polly.Amy')
            response.redirect('/convonet_todo/twilio/call')
            return Response(str(response), mimetype='text/xml')
        
        # Convert spoken numbers to digits
        number_words = {
            'zero': '0', 'oh': '0', 'o': '0',
            'one': '1', 'two': '2', 'three': '3', 'four': '4', 'five': '5',
            'six': '6', 'seven': '7', 'eight': '8', 'nine': '9',
            'ten': '10', 'eleven': '11', 'twelve': '12'
        }
        
        # Clean up the PIN - remove non-alphanumeric except spaces
        # This handles cases like "1234." from DTMF or "one two three four" from speech
        cleaned_input = pin.strip()
        
        # First, try to extract any digits directly (handles DTMF like "1234" or "1234.")
        digits_only = ''.join(c for c in cleaned_input if c.isdigit())
        
        # If we got digits directly (DTMF input), use them
        if digits_only and len(digits_only) >= 4:
            clean_pin = digits_only
        else:
            # No direct digits, try speech-to-digit conversion
            words = cleaned_input.lower().replace('-', ' ').replace(',', ' ').replace('.', ' ').split()
            converted_digits = []
            for word in words:
                if word in number_words:
                    converted_digits.append(number_words[word])
                elif word.isdigit():
                    converted_digits.append(word)
            clean_pin = ''.join(converted_digits)
        
        print(f"🔧 Original PIN: '{pin}' → Cleaned PIN: '{clean_pin}'")
        
        if not clean_pin or len(clean_pin) < 4 or len(clean_pin) > 6:
            response = VoiceResponse()
            response.say("Invalid PIN format. Please enter a 4 to 6 digit PIN.", voice='Polly.Amy')
            response.redirect('/convonet_todo/twilio/call')
            return Response(str(response), mimetype='text/xml')
        
        # Verify PIN - use direct database query (fast, <100ms, avoids Twilio timeout)
        try:
            # Import here to avoid circular import
            from convonet.mcps.local_servers.db_todo import _init_database, SessionLocal
            from convonet.models.user_models import User as UserModel
            
            # Initialize database if needed
            _init_database()
            
            if SessionLocal is None:
                raise Exception("Database not initialized - DB_URI not configured")
            
            # Quick database lookup
            with SessionLocal() as session:
                user = session.query(UserModel).filter(
                    UserModel.voice_pin == clean_pin,
                    UserModel.is_active == True
                ).first()
            
            # Check if authentication succeeded
            if user:
                # Extract user info
                user_id = str(user.id)
                user_name = user.first_name
                
                # Store user ID in session (use call_sid as session key)
                # In production, use Redis or database for session storage
                
                response = VoiceResponse()
                gather = response.gather(
                    input='speech',
                    action=f'/convonet_todo/twilio/process_audio?user_id={user_id}',
                    method='POST',
                    speech_timeout='auto',
                    timeout=10,
                    barge_in=True,
                    speech_model='experimental_conversations',  # Better conversational recognition
                    enhanced=True,  # Use enhanced speech recognition
                    language='en-US'  # Explicitly set language
                )
                
                # Welcome message
                gather.say(f"Welcome back, {user_name}! How can I help you today?", voice='Polly.Amy')
                
                response.say("I didn't hear anything. Please try again.", voice='Polly.Amy')
                response.redirect(f'/convonet_todo/twilio/call?is_continuation=true&authenticated=true&user_id={user_id}')
                
                print(f"✅ PIN verified for user {user_id} ({user.email})")
                return Response(str(response), mimetype='text/xml')
            else:
                # Invalid PIN
                response = VoiceResponse()
                response.say("Invalid PIN. Please try again.", voice='Polly.Amy')
                response.redirect('/convonet_todo/twilio/call')
                print(f"❌ Invalid PIN: {clean_pin}")
                return Response(str(response), mimetype='text/xml')
        
        except Exception as e:
            print(f"Error in database query: {e}")
            import traceback
            traceback.print_exc()
            response = VoiceResponse()
            response.say("There was an error verifying your PIN. Please try again.", voice='Polly.Amy')
            response.redirect('/convonet_todo/twilio/call')
            return Response(str(response), mimetype='text/xml')
            
    except Exception as e:
        print(f"Error in verify_pin_webhook: {e}")
        import traceback
        traceback.print_exc()
        response = VoiceResponse()
        response.say("Sorry, there was a system error. Please try again.", voice='Polly.Amy')
        response.redirect('/convonet_todo/twilio/call')
        return Response(str(response), mimetype='text/xml')

@convonet_todo_bp.route('/twilio/transfer', methods=['POST'])
def transfer_to_agent():
    """
    Transfer the call to a FreePBX extension/queue.
    Expects POST parameters:
    - extension: The FreePBX extension or queue number to transfer to
    - call_sid: The Twilio Call SID
    """
    try:
        extension = request.form.get('extension') or request.args.get('extension', '2001')
        call_sid = request.form.get('CallSid', '')
        caller_number = request.form.get('From', '')
        
        logger.info(f"Transferring call {call_sid} from {caller_number} to extension {extension}")
        
        # Create TwiML response for transfer
        response = VoiceResponse()
        response.say("Transferring you to an agent. Please wait.", voice='Polly.Amy')
        
        # Get configuration
        freepbx_domain = os.getenv('FREEPBX_DOMAIN', '136.115.41.45')
        transfer_timeout = int(os.getenv('TRANSFER_TIMEOUT', '30'))
        sip_username = os.getenv('FREEPBX_SIP_USERNAME', '')
        sip_password = os.getenv('FREEPBX_SIP_PASSWORD', '')
        
        # Build SIP URI for FusionPBX extension
        # Use domain/IP for Twilio (Twilio needs resolvable domain/IP)
        # FusionPBX dialplan must be configured to route external calls to extensions
        sip_uri = f"sip:{extension}@{freepbx_domain};transport=udp"
        logger.info(f"Transferring to SIP URI: {sip_uri}")
        logger.info(f"FusionPBX Domain: {freepbx_domain}, Extension: {extension}")
        logger.info(f"NOTE: FusionPBX dialplan must route external calls from 'public' context to extension {extension}")
        logger.info(f"Transfer Timeout: {transfer_timeout} seconds")
        
        # Create Dial verb with transfer settings
        dial = response.dial(
            answer_on_bridge=True,  # Wait for agent to answer before connecting
            timeout=transfer_timeout,
            caller_id=caller_number,
            action=f'/convonet_todo/twilio/transfer_callback?extension={extension}'
        )
        
        # Add SIP destination
        if sip_username and sip_password:
            # Use SIP authentication if configured
            dial.sip(sip_uri, username=sip_username, password=sip_password)
            logger.info(f"Using SIP auth with username: {sip_username}")
        else:
            # Use IP-based authentication (requires FusionPBX to whitelist Twilio IPs)
            dial.sip(sip_uri)
            logger.info("Using IP-based SIP authentication (FusionPBX must whitelist Twilio IPs)")
            logger.warning("⚠️ If transfer fails, configure FusionPBX to accept SIP from Twilio IP ranges:")
            logger.warning("   54.172.60.0/23, 54.244.51.0/24, 177.71.206.192/26, 54.252.254.64/26, 54.169.127.128/26")
        
        # If dial fails, provide fallback message
        response.say("I'm sorry, the transfer failed. Please try again later.", voice='Polly.Amy')
        response.hangup()
        
        logger.info(f"Transfer TwiML generated for call {call_sid}")
        logger.info(f"TwiML content: {str(response)}")
        return Response(str(response), mimetype='text/xml')
        
    except Exception as e:
        logger.error(f"Error in transfer endpoint: {e}")
        import traceback
        traceback.print_exc()
        
        response = VoiceResponse()
        response.say("I'm sorry, there was an error transferring your call. Please try again.", voice='Polly.Amy')
        response.hangup()
        return Response(str(response), mimetype='text/xml')

@convonet_todo_bp.route('/twilio/transfer_callback', methods=['POST'])
def transfer_callback():
    """
    Callback handler for transfer status.
    Logs transfer results and handles failures.
    """
    try:
        dial_call_status = request.form.get('DialCallStatus', 'unknown')
        call_sid = request.form.get('CallSid', '')
        extension = request.args.get('extension', '2001')
        
        logger.info(f"Transfer callback for call {call_sid}: status={dial_call_status}, extension={extension}")
        
        response = VoiceResponse()
        
        if dial_call_status == 'completed':
            # Transfer succeeded - call is now connected to agent
            logger.info(f"✅ Transfer successful for call {call_sid} to extension {extension}")
            # Call will continue on the agent side
            
        elif dial_call_status == 'busy':
            response.say("The agent is currently busy. Please try again later.", voice='Polly.Amy')
            response.hangup()
            logger.warning(f"⚠️ Transfer failed - agent busy: call {call_sid}")
            
        elif dial_call_status == 'no-answer':
            response.say("The agent did not answer. Please try again later.", voice='Polly.Amy')
            response.hangup()
            logger.warning(f"⚠️ Transfer failed - no answer: call {call_sid}")
            
        elif dial_call_status == 'failed' or dial_call_status == 'canceled':
            # Get more details about the failure
            dial_call_sid = request.form.get('DialCallSid', 'N/A')
            dial_call_duration = request.form.get('DialCallDuration', 'N/A')
            error_message = request.form.get('ErrorMessage', 'No error details')
            
            logger.error(f"❌ Transfer failed: call {call_sid}, status={dial_call_status}")
            logger.error(f"   Dial Call SID: {dial_call_sid}")
            logger.error(f"   Dial Duration: {dial_call_duration} seconds")
            logger.error(f"   Error Message: {error_message}")
            logger.error(f"   Possible causes:")
            logger.error(f"   1. FusionPBX is not accepting SIP from Twilio IP ranges")
            logger.error(f"   2. Firewall blocking SIP traffic on port 5060")
            logger.error(f"   3. Extension {extension} does not exist or is not reachable")
            logger.error(f"   4. FusionPBX requires SIP authentication (set FREEPBX_SIP_USERNAME and FREEPBX_SIP_PASSWORD)")
            
            response.say("The transfer could not be completed. Please call back later.", voice='Polly.Amy')
            response.hangup()
            
        else:
            response.say("An unexpected error occurred. Please try again.", voice='Polly.Amy')
            response.hangup()
            logger.error(f"❌ Unknown transfer status: {dial_call_status} for call {call_sid}")
        
        return Response(str(response), mimetype='text/xml')
        
    except Exception as e:
        logger.error(f"Error in transfer callback: {e}")
        import traceback
        traceback.print_exc()
        
        response = VoiceResponse()
        response.say("An error occurred. Goodbye.", voice='Polly.Amy')
        response.hangup()
        return Response(str(response), mimetype='text/xml')


@convonet_todo_bp.route('/twilio/voice_assistant/transfer_bridge', methods=['GET', 'POST'])
def voice_assistant_transfer_bridge():
    """
    TwiML endpoint used by the WebRTC voice assistant to connect callers directly
    to a FusionPBX/SIP extension instead of a conference bridge.
    """
    # Handle GET requests for testing
    if request.method == 'GET':
        extension = request.args.get('extension', '2001')
        logger.info(f"[VoiceAssistantBridge] GET request received - testing endpoint with extension={extension}")
        return jsonify({
            'status': 'ok',
            'endpoint': 'transfer_bridge',
            'extension': extension,
            'message': 'Endpoint is accessible. Use POST for actual transfers.'
        }), 200
    
    try:
        extension = request.args.get('extension') or request.form.get('extension') or '2001'
        call_sid = request.form.get('CallSid', '')
        caller_number = request.form.get('From') or os.getenv('TWILIO_PHONE_NUMBER', '')
        
        logger.info(f"[VoiceAssistantBridge] ===== TRANSFER BRIDGE CALLED =====")
        logger.info(f"[VoiceAssistantBridge] Call SID: {call_sid}")
        logger.info(f"[VoiceAssistantBridge] Extension: {extension}")
        logger.info(f"[VoiceAssistantBridge] Caller: {caller_number}")
        logger.info(f"[VoiceAssistantBridge] Request method: {request.method}")
        logger.info(f"[VoiceAssistantBridge] Request URL: {request.url}")
        logger.info(f"[VoiceAssistantBridge] Request args: {dict(request.args)}")
        logger.info(f"[VoiceAssistantBridge] Request form: {dict(request.form)}")
        logger.info(f"[VoiceAssistantBridge] Request headers: {dict(request.headers)}")
        
        freepbx_domain = os.getenv('FREEPBX_DOMAIN', '136.115.41.45')
        transfer_timeout = int(os.getenv('TRANSFER_TIMEOUT', '30'))
        sip_username = os.getenv('FREEPBX_SIP_USERNAME', '')
        sip_password = os.getenv('FREEPBX_SIP_PASSWORD', '')
        
        # Use domain/IP for Twilio (Twilio needs resolvable domain/IP)
        # FusionPBX dialplan must be configured to route external calls to extensions
        # The dialplan should route: public context -> extension@internal
        sip_uri = f"sip:{extension}@{freepbx_domain};transport=udp"
        logger.info(f"[VoiceAssistantBridge] Using SIP URI: {sip_uri}")
        logger.info(f"[VoiceAssistantBridge] NOTE: FusionPBX dialplan must route external calls to extension {extension}")
        
        logger.info(f"[VoiceAssistantBridge] Dialing {sip_uri} for call {call_sid}")
        logger.info(f"[VoiceAssistantBridge] FusionPBX Domain: {freepbx_domain}")
        logger.info(f"[VoiceAssistantBridge] Transfer Timeout: {transfer_timeout} seconds")
        
        # Get base URL for absolute callback URL
        webhook_base_url = get_webhook_base_url()
        callback_url = f"{webhook_base_url}/convonet_todo/twilio/transfer_callback?extension={extension}"
        logger.info(f"[VoiceAssistantBridge] Callback URL: {callback_url}")
        
        response = VoiceResponse()
        dial = response.dial(
            answer_on_bridge=True,
            timeout=transfer_timeout,
            caller_id=caller_number,
            action=callback_url  # Use absolute URL
        )
        
        if sip_username and sip_password:
            dial.sip(sip_uri, username=sip_username, password=sip_password)
            logger.info(f"[VoiceAssistantBridge] Using SIP authentication (username: {sip_username})")
        else:
            dial.sip(sip_uri)
            logger.info("[VoiceAssistantBridge] Using IP-based SIP authentication")
            logger.warning("[VoiceAssistantBridge] ⚠️ FusionPBX must whitelist Twilio IP ranges for IP-based auth")
        
        # Fallback message if dial fails
        response.say("I'm sorry, the transfer failed. Please try again later.", voice='Polly.Amy')
        response.hangup()
        
        twiml_content = str(response)
        logger.info(f"[VoiceAssistantBridge] Generated TwiML for call {call_sid}")
        logger.info(f"[VoiceAssistantBridge] TwiML preview: {twiml_content[:200]}...")
        
        return Response(twiml_content, mimetype='text/xml')
    
    except Exception as e:
        logger.error(f"[VoiceAssistantBridge] ❌ Error connecting to agent: {e}")
        import traceback
        logger.error(f"[VoiceAssistantBridge] Traceback: {traceback.format_exc()}")
        
        response = VoiceResponse()
        response.say("I'm sorry, there was an error connecting you to an agent. Please try again later.", voice='Polly.Amy')
        response.hangup()
        return Response(str(response), mimetype='text/xml')

@convonet_todo_bp.route('/twilio/process_audio', methods=['POST'])
def process_audio_webhook():
    """
    Handles audio processing requests from Twilio.
    Processes the audio and returns TwiML with the agent's response.
    
    Features:
    - Barge-in capability: Users can interrupt the agent while it's speaking
    - Continuous conversation flow
    - Graceful error handling
    """
    # Start Sentry transaction for performance monitoring
    with sentry_sdk.start_transaction(op="voice_call", name="process_audio") as transaction:
        try:
            # Get the transcribed text from the request
            transcribed_text = request.form.get('SpeechResult', '')
            call_sid = request.form.get('CallSid', '')
            user_id = request.args.get('user_id')
            
            # Set Sentry context for this call
            sentry_sdk.set_context("voice_call", {
                "call_sid": call_sid,
                "user_id": user_id,
                "transcribed_text": transcribed_text[:100]  # First 100 chars
            })
            sentry_sdk.set_user({"id": user_id} if user_id else None)
        
            print(f"Processing audio for call {call_sid}: {transcribed_text}")
        
            if not transcribed_text or len(transcribed_text.strip()) < 2:
                response = VoiceResponse()
                
                # Get authenticated user_id for redirect
                user_param = f'&user_id={user_id}' if user_id else ''
                
                # Use Gather with barge-in for "didn't catch that" response
                gather = Gather(
                    input='speech',
                    action=f'/convonet_todo/twilio/process_audio?user_id={user_id}' if user_id else '/convonet_todo/twilio/process_audio',
                    method='POST',
                    speech_timeout='auto',
                    timeout=10,
                    barge_in=True,
                    speech_model='experimental_conversations',  # Better conversational recognition
                    enhanced=True,  # Use enhanced speech recognition
                    language='en-US'  # Explicitly set language
                )
                gather.say("I didn't catch that. Could you please repeat?", voice='Polly.Amy')
                response.append(gather)
                
                # Fallback
                response.say("I didn't hear anything. Please try again.", voice='Polly.Amy')
                response.redirect(f'/convonet_todo/twilio/call?is_continuation=true&authenticated=true{user_param}')
                return Response(str(response), mimetype='text/xml')
            
            transfer_requested = has_transfer_intent(transcribed_text)
            if transfer_requested:
                webhook_base_url = get_webhook_base_url()
                response = VoiceResponse()
                response.redirect(f'{webhook_base_url}/convonet_todo/twilio/transfer?extension=2001')
                logger.info(f"Redirecting call to transfer endpoint based on user request: {transcribed_text}")
                return Response(str(response), mimetype='text/xml')
            
            # Check if user wants to end the call
            exit_phrases = ['exit', 'goodbye', 'bye', 'that\'s it', 'that is it', 'thank you', 'thanks', 'done', 'finished', 'end call', 'hang up']
            if any(phrase in transcribed_text.lower() for phrase in exit_phrases):
                # End the call gracefully
                response = VoiceResponse()
                response.say("Thank you for using Convonet productivity assistant! Have a great day!", voice='Polly.Amy')
                response.hangup()
                return Response(str(response), mimetype='text/xml')
            
            # Check if this user's thread needs to be reset (after previous timeout/error)
            reset_thread = False
            if user_id and hasattr(_run_agent_async, '_reset_threads') and user_id in _run_agent_async._reset_threads:
                reset_thread = True
                _run_agent_async._reset_threads.remove(user_id)
                print(f"🔄 Resetting conversation thread for user {user_id} (previous timeout/error)")
                print(f"🔄 Reset threads set contains: {_run_agent_async._reset_threads}")
                
                # Track thread reset in Sentry
                sentry_sdk.capture_message(
                    "Conversation thread reset after timeout/error",
                    level="info",
                    extras={"user_id": user_id}
                )
        
            # Process with the agent (with timeout to prevent hanging)
            # Note: Twilio HTTP timeout is ~15 seconds, so we must respond faster
            start_time = time.time()
            transfer_marker = None
            
            with sentry_sdk.start_span(op="agent_processing", description="LangGraph agent execution"):
                try:
                    agent_result = asyncio.run(asyncio.wait_for(
                        _run_agent_async(
                            transcribed_text,
                            user_id=user_id,
                            reset_thread=reset_thread,
                            include_metadata=True
                        ),
                        timeout=12.0  # Reduced from 30 to 12 seconds to stay under Twilio's 15s timeout
                    ))
                    if isinstance(agent_result, dict):
                        agent_response = agent_result.get("response", "")
                        transfer_marker = agent_result.get("transfer_marker")
                    else:
                        agent_response = agent_result
                        transfer_marker = None
                    processing_time = time.time() - start_time
                    sentry_sdk.set_measurement("agent_processing_time", processing_time, "second")
                except asyncio.TimeoutError:
                    processing_time = time.time() - start_time
                    print(f"⏰ Outer timeout: Agent took more than 12 seconds (actual: {processing_time:.2f}s)")
                    
                    # Track timeout in Sentry
                    sentry_sdk.capture_message(
                        "Agent processing timeout",
                        level="warning",
                        extras={
                            "user_id": user_id,
                            "call_sid": call_sid,
                            "prompt": transcribed_text,
                            "timeout_duration": processing_time
                        }
                    )
                    
                    # Mark user for thread reset
                    if user_id:
                        if not hasattr(_run_agent_async, '_reset_threads'):
                            _run_agent_async._reset_threads = set()
                        _run_agent_async._reset_threads.add(user_id)
                        print(f"🔄 Marked user {user_id} for thread reset")
                    agent_response = "I'm sorry, that operation is taking too long. The task may still complete in the background. Please check your calendar or todo list."
                except Exception as e:
                    processing_time = time.time() - start_time
                    print(f"Error in agent processing (outer): {e}")
                    
                    # Capture exception in Sentry
                    sentry_sdk.capture_exception(e)
                    
                    agent_response = f"AGENT_ERROR:unexpected:{str(e)[:100]}"
        
            # Check if agent returned an error marker and handle accordingly
            if agent_response.startswith("AGENT_TIMEOUT:"):
                print(f"⏰ Agent timed out internally")
                if user_id:
                    if not hasattr(_run_agent_async, '_reset_threads'):
                        _run_agent_async._reset_threads = set()
                    _run_agent_async._reset_threads.add(user_id)
                    print(f"🔄 Marked user {user_id} for thread reset")
                agent_response = "I'm sorry, that operation is taking too long. Please try a simpler request."
                
            elif agent_response.startswith("AGENT_ERROR:"):
                # Parse error type and message
                parts = agent_response.split(":", 2)
                error_type = parts[1] if len(parts) > 1 else "unknown"
                error_msg = parts[2] if len(parts) > 2 else ""
                
                print(f"🔧 Agent returned error: type={error_type}, msg={error_msg}")
                
                # Track error in Sentry
                sentry_sdk.capture_message(
                    f"Agent error: {error_type}",
                    level="error",
                    extras={
                        "error_type": error_type,
                        "error_message": error_msg,
                        "user_id": user_id,
                        "call_sid": call_sid
                    }
                )
                
                # Mark user for thread reset on these error types
                if error_type in ["tool_call_incomplete", "broken_resource"]:
                    if user_id:
                        if not hasattr(_run_agent_async, '_reset_threads'):
                            _run_agent_async._reset_threads = set()
                        _run_agent_async._reset_threads.add(user_id)
                        print(f"🔄 Marked user {user_id} for thread reset due to {error_type}")
                
                # User-friendly messages
                if error_type == "tool_call_incomplete":
                    agent_response = "I had trouble with the previous operation. Please try your request again."
                elif error_type == "broken_resource":
                    agent_response = "I encountered a connection issue. The operation may have completed. Please check your calendar or todo list."
                else:
                    agent_response = "I'm sorry, I encountered an error. Please try again or rephrase your question."
            
            # Check if agent response indicates a transfer request
            transfer_marker_value = transfer_marker
            if not transfer_marker_value and isinstance(agent_response, str) and agent_response.startswith("TRANSFER_INITIATED:"):
                transfer_marker_value = agent_response
            
            if transfer_marker_value:
                if not transfer_requested:
                    logger.info("Transfer marker detected but caller did not request a human. Suppressing automatic transfer.")
                    transfer_marker_value = None
                else:
                    transfer_data = transfer_marker_value.replace("TRANSFER_INITIATED:", "")
                    parts = transfer_data.split("|")
                    target_extension = parts[0] if len(parts) > 0 else "2001"
                    
                    webhook_base_url = get_webhook_base_url()
                    response = VoiceResponse()
                    response.redirect(f'{webhook_base_url}/convonet_todo/twilio/transfer?extension={target_extension}')
                    logger.info(f"Agent initiated transfer to extension {target_extension}")
                    if user_id:
                        if not hasattr(_run_agent_async, '_reset_threads'):
                            _run_agent_async._reset_threads = set()
                        _run_agent_async._reset_threads.add(user_id)
                    return Response(str(response), mimetype='text/xml')
            
            # Return TwiML with the agent's response and barge-in capability
            response = VoiceResponse()
            
            # Preserve user_id in redirects
            user_param = f'?user_id={user_id}' if user_id else ''
            auth_param = f'&authenticated=true' if user_id else ''
            
            # Enhanced Twilio speech recognition configuration
            gather = Gather(
                input='speech',
                action=f'/convonet_todo/twilio/process_audio{user_param}',
                method='POST',
                speech_timeout='auto',
                timeout=15,  # Increased from 10s
                barge_in=True,
                speech_model='experimental_conversations',
                enhanced=True,
                language='en-US',
                # Add speech hints for better recognition
                speech_hints='create todo reminder calendar team member assign task complete delete update schedule meeting appointment'
            )
        
            # Add the agent's response to the gather
            gather.say(agent_response, voice='Polly.Amy')
            response.append(gather)
            
            # Fallback if no speech is detected after the response
            response.say("I didn't hear anything. Please try again.", voice='Polly.Amy')
            response.redirect(f'/convonet_todo/twilio/call?is_continuation=true{auth_param}{user_param}')
            
            print(f"Generated TwiML response: {str(response)}")
            return Response(str(response), mimetype='text/xml')
            
        except Exception as e:
            print(f"Error processing audio: {e}")
            sentry_sdk.capture_exception(e)
            response = VoiceResponse()
            
            # Preserve user_id in error redirects
            user_param = f'?user_id={user_id}' if user_id else ''
            auth_param = f'&authenticated=true' if user_id else ''
            
            # Use Gather with barge-in for error messages too
            gather = Gather(
                input='speech',
                action=f'/convonet_todo/twilio/process_audio{user_param}',
                method='POST',
                speech_timeout='auto',
                timeout=10,
                barge_in=True
            )
            gather.say("I'm sorry, I encountered an error processing your request. Please try again.", voice='Polly.Amy')
            response.append(gather)
            
            # Fallback
            response.say("I didn't hear anything. Please try again.", voice='Polly.Amy')
            response.redirect(f'/convonet_todo/twilio/call?is_continuation=true{auth_param}{user_param}')
            return Response(str(response), mimetype='text/xml')

# WebSocket server is now handled by a separate process
# See websocket_server.py for the Twilio voice streaming implementation

# --- Web/API Routes ---
@convonet_todo_bp.route('/')
def index():
    template_path = os.path.join(os.path.dirname(__file__), 'templates', 'convonet_todo_index.html')
    if os.path.exists(template_path):
        return render_template('convonet_todo_index.html')
    return "Convonet Todo: Convonet + MCP integration is ready. POST to /convonet_todo/run_agent with JSON {prompt: str}."


@convonet_todo_bp.route('/mortgage/dashboard')
def mortgage_dashboard():
    """Mortgage application dashboard for monitoring applications"""
    return render_template('mortgage_dashboard.html')


@convonet_todo_bp.route('/api/mortgage/applications', methods=['GET'])
def get_mortgage_applications():
    """Get all mortgage applications for dashboard"""
    try:
        from convonet.models.mortgage_models import MortgageApplication, MortgageDocument, MortgageDebt
        from sqlalchemy import create_engine, func
        from sqlalchemy.orm import sessionmaker
        from uuid import UUID
        
        # Create database session (same pattern as MCP tools)
        db_uri = os.getenv("DB_URI")
        if not db_uri:
            return jsonify({
                "success": False,
                "error": "DB_URI not configured"
            }), 500
        
        engine = create_engine(db_uri, pool_pre_ping=True)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        
        try:
            # Get all applications with document counts
            applications = db.query(
                MortgageApplication,
                func.count(MortgageDocument.id).label('documents_count')
            ).outerjoin(
                MortgageDocument, MortgageDocument.application_id == MortgageApplication.id
            ).group_by(MortgageApplication.id).order_by(
                MortgageApplication.created_at.desc()
            ).all()
            
            result = []
            for app, doc_count in applications:
                # Get debts count
                debts_count = db.query(MortgageDebt).filter(
                    MortgageDebt.application_id == app.id
                ).count()
                
                result.append({
                    "application_id": str(app.id),
                    "user_id": str(app.user_id),
                    "status": app.status.value if hasattr(app.status, 'value') else str(app.status),
                    "credit_score": app.credit_score,
                    "dti_ratio": float(app.dti_ratio) if app.dti_ratio else None,
                    "monthly_income": float(app.monthly_income) if app.monthly_income else None,
                    "monthly_debt": float(app.monthly_debt) if app.monthly_debt else None,
                    "down_payment_amount": float(app.down_payment_amount) if app.down_payment_amount else None,
                    "total_savings": float(app.total_savings) if app.total_savings else None,
                    "completion_percentage": app.get_completion_percentage(),
                    "documents_count": doc_count or 0,
                    "debts_count": debts_count,
                    "created_at": app.created_at.isoformat() if app.created_at else None
                })
            
            return jsonify({
                "success": True,
                "applications": result
            })
        finally:
            db.close()
    except Exception as e:
        logger.error(f"❌ Error getting mortgage applications: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


@convonet_todo_bp.route('/api/mortgage/applications/<application_id>', methods=['GET'])
def get_mortgage_application(application_id):
    """Get detailed information about a specific mortgage application"""
    try:
        from convonet.models.mortgage_models import MortgageApplication, MortgageDocument, MortgageDebt
        from sqlalchemy import create_engine
        from sqlalchemy.orm import sessionmaker
        from uuid import UUID
        
        # Create database session (same pattern as MCP tools)
        db_uri = os.getenv("DB_URI")
        if not db_uri:
            return jsonify({
                "success": False,
                "error": "DB_URI not configured"
            }), 500
        
        engine = create_engine(db_uri, pool_pre_ping=True)
        SessionLocal = sessionmaker(bind=engine)
        db = SessionLocal()
        
        try:
            # Get application
            application = db.query(MortgageApplication).filter(
                MortgageApplication.id == UUID(application_id)
            ).first()
            
            if not application:
                return jsonify({
                    "success": False,
                    "error": "Application not found"
                }), 404
            
            # Get documents
            documents = db.query(MortgageDocument).filter(
                MortgageDocument.application_id == application.id
            ).all()
            
            # Get debts
            debts = db.query(MortgageDebt).filter(
                MortgageDebt.application_id == application.id
            ).all()
            
            return jsonify({
                "success": True,
                "application": {
                    "application_id": str(application.id),
                    "user_id": str(application.user_id),
                    "status": application.status.value if hasattr(application.status, 'value') else str(application.status),
                    "credit_score": application.credit_score,
                    "dti_ratio": float(application.dti_ratio) if application.dti_ratio else None,
                    "monthly_income": float(application.monthly_income) if application.monthly_income else None,
                    "monthly_debt": float(application.monthly_debt) if application.monthly_debt else None,
                    "down_payment_amount": float(application.down_payment_amount) if application.down_payment_amount else None,
                    "closing_costs_estimate": float(application.closing_costs_estimate) if application.closing_costs_estimate else None,
                    "total_savings": float(application.total_savings) if application.total_savings else None,
                    "loan_type": application.loan_type,
                    "loan_amount": float(application.loan_amount) if application.loan_amount else None,
                    "property_value": float(application.property_value) if application.property_value else None,
                    "completion_percentage": application.get_completion_percentage(),
                    "financial_review_completed": application.financial_review_completed,
                    "document_collection_completed": application.document_collection_completed,
                    "document_verification_completed": application.document_verification_completed,
                    "documents_count": len(documents),
                    "debts_count": len(debts),
                    "documents": [
                        {
                            "document_id": str(doc.id),
                            "document_type": doc.document_type.value if hasattr(doc.document_type, 'value') else str(doc.document_type),
                            "document_name": doc.document_name,
                            "status": doc.status.value if hasattr(doc.status, 'value') else str(doc.status),
                            "uploaded_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                            "verified_at": doc.verified_at.isoformat() if doc.verified_at else None
                        }
                        for doc in documents
                    ],
                    "debts": [
                        {
                            "debt_id": str(debt.id),
                            "debt_type": debt.debt_type,
                            "creditor_name": debt.creditor_name,
                            "monthly_payment": float(debt.monthly_payment),
                            "outstanding_balance": float(debt.outstanding_balance) if debt.outstanding_balance else None,
                            "interest_rate": float(debt.interest_rate) if debt.interest_rate else None
                        }
                        for debt in debts
                    ],
                    "created_at": application.created_at.isoformat() if application.created_at else None,
                    "updated_at": application.updated_at.isoformat() if application.updated_at else None
                }
            })
        finally:
            db.close()
    except Exception as e:
        logger.error(f"❌ Error getting mortgage application: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500


async def _preload_mcp_tools():
    """Pre-load MCP tools at startup to cache them for all providers (including Gemini).
    
    This prevents hangs during requests by loading tools once at startup.
    Tools are cached globally and reused for all LLM providers.
    """
    global _mcp_tools_cache, _mcp_tools_loading
    
    # Skip if already cached or currently loading
    if _mcp_tools_cache is not None:
        print(f"✅ MCP tools already cached ({len(_mcp_tools_cache)} tools)")
        return _mcp_tools_cache
    
    if _mcp_tools_loading:
        print("⏳ MCP tools are currently being loaded, waiting...")
        # Wait a bit for the other thread to finish
        await asyncio.sleep(2)
        if _mcp_tools_cache is not None:
            return _mcp_tools_cache
        return []
    
    async with _mcp_tools_lock:
        # Check again after acquiring lock
        if _mcp_tools_cache is not None:
            return _mcp_tools_cache
        
        _mcp_tools_loading = True
        try:
            print("🔧 Pre-loading MCP tools at startup (for Gemini compatibility)...")
            
            config_path = os.path.join(os.path.dirname(__file__), 'mcps', 'mcp_config.json')
            if not os.path.exists(config_path):
                config_path = os.path.join('convonet', 'mcps', 'mcp_config.json')
            
            if not os.path.exists(config_path):
                print(f"⚠️ MCP config file not found, skipping tool pre-load")
                return []
            
            with open(config_path) as f:
                mcp_config = json.load(f)
            
            # Set working directory to project root for MCP servers
            project_root = os.path.dirname(os.path.dirname(__file__))
            original_cwd = os.getcwd()
            os.chdir(project_root)
            
            try:
                # Update the MCP config with absolute paths and environment variables
                for server_name, server_config in mcp_config["mcpServers"].items():
                    if "args" in server_config and len(server_config["args"]) > 0:
                        relative_path = server_config["args"][0]
                        if not os.path.isabs(relative_path):
                            absolute_path = os.path.join(project_root, relative_path)
                            server_config["args"][0] = absolute_path
                    
                    # Handle environment variable substitution
                    if "env" in server_config:
                        for env_key, env_value in server_config["env"].items():
                            if isinstance(env_value, str) and env_value.startswith("${") and env_value.endswith("}"):
                                env_var_name = env_value[2:-1]
                                env_var_value = os.getenv(env_var_name)
                                if env_var_value:
                                    server_config["env"][env_key] = env_var_value
                
                # Initialize MCP client
                print("🔧 Creating MCP client for pre-load...")
                client = MultiServerMCPClient(connections=mcp_config["mcpServers"])
                print("🔧 Getting tools from MCP client (this may take a moment)...")
                
                # Use a longer timeout for startup pre-load (30 seconds)
                tools = await asyncio.wait_for(client.get_tools(), timeout=30.0)
                _mcp_tools_cache = tools.copy()
                print(f"✅ MCP tools pre-loaded and cached: {len(_mcp_tools_cache)} tools")
                print(f"✅ These tools will be available for all LLM providers including Gemini")
                return _mcp_tools_cache
            except asyncio.TimeoutError:
                print(f"⏱️ MCP tools pre-load timed out after 30 seconds")
                print(f"⚠️ Tools will be loaded on first request instead")
                return []
            except Exception as e:
                print(f"⚠️ Error pre-loading MCP tools: {e}")
                import traceback
                print(f"⚠️ Traceback: {traceback.format_exc()}")
                print(f"⚠️ Tools will be loaded on first request instead")
                return []
            finally:
                os.chdir(original_cwd)
        finally:
            _mcp_tools_loading = False


def preload_mcp_tools_sync():
    """Synchronous wrapper to pre-load MCP tools at app startup."""
    try:
        # Try to run in existing event loop, or create new one
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # If loop is running, schedule as task
                asyncio.create_task(_preload_mcp_tools())
            else:
                # If loop exists but not running, run it
                loop.run_until_complete(_preload_mcp_tools())
        except RuntimeError:
            # No event loop, create one
            asyncio.run(_preload_mcp_tools())
    except Exception as e:
        print(f"⚠️ Could not pre-load MCP tools: {e}")
        print(f"⚠️ Tools will be loaded on first request instead")


async def _get_agent_graph(
    provider: Optional[LLMProvider] = None, 
    user_id: Optional[str] = None,
    agent_type: str = "todo",  # "todo" or "mortgage"
    model: Optional[str] = None  # Optional model override (e.g., "claude-3-5-haiku-20241022" for voice)
) -> StateGraph:
    """Helper to initialize the agent graph with tools (cached for performance).
    
    Args:
        provider: LLM provider to use (claude, gemini, openai). If None, gets from user preference or default.
        user_id: User ID to get provider preference from Redis
        agent_type: Type of agent to create ("todo" or "mortgage")
    """
    global _agent_graph_cache, _agent_graph_model, _agent_graph_provider
    
    # Add early logging
    print(f"🔧 _get_agent_graph() called with provider={provider}, user_id={user_id}", flush=True)
    import sys
    sys.stdout.flush()
    
    # Get provider preference
    if provider is None:
        print(f"🔍 Looking up LLM provider preference (user_id: {user_id})")
        if user_id:
            # Try to get per-user preference from Redis
            try:
                redis_key = f"user:{user_id}:llm_provider"
                user_pref = redis_manager.get(redis_key)
                print(f"🔍 Redis lookup '{redis_key}': {user_pref}")
                if user_pref and user_pref in ["claude", "gemini", "openai"]:
                    provider = user_pref
                    print(f"📋 Using user preference for LLM provider: {provider}")
                else:
                    print(f"⚠️ User preference not found or invalid: {user_pref}")
            except Exception as e:
                print(f"⚠️ Could not get user provider preference: {e}")
                import traceback
                print(f"⚠️ Traceback: {traceback.format_exc()}")

        # If no per-user preference, try global default ('default' user id used by homepage selector)
        if provider is None:
            try:
                global_pref = redis_manager.get("user:default:llm_provider")
                print(f"🔍 Redis lookup 'user:default:llm_provider': {global_pref}")
                if global_pref and global_pref in ["claude", "gemini", "openai"]:
                    provider = global_pref
                    print(f"📋 Using global default LLM provider from Redis: {provider}")
                else:
                    print(f"⚠️ Global default preference not found or invalid: {global_pref}")
            except Exception as e:
                print(f"⚠️ Could not get global provider preference: {e}")
                import traceback
                print(f"⚠️ Traceback: {traceback.format_exc()}")
        
        # Fallback to environment variable or default
        if provider is None:
            provider = os.getenv("LLM_PROVIDER", "claude").lower()
            if provider not in ["claude", "gemini", "openai"]:
                provider = "claude"
            print(f"📋 Using environment/default LLM provider: {provider}")
    
    # Get current model name based on provider (use override if provided)
    if model:
        current_model = model  # Use model override (e.g., for voice responses)
        print(f"🔧 Using model override: {current_model}")
    elif provider == "gemini":
        current_model = os.getenv("GOOGLE_MODEL", "gemini-2.5-flash")
        # Force clear cache if old default model (gemini-3-pro-preview) is cached
        if (_agent_graph_cache is not None and 
            _agent_graph_provider == "gemini" and 
            _agent_graph_model == "gemini-3-pro-preview" and
            current_model == "gemini-2.5-flash"):
            print(f"🔄 Clearing cache: default Gemini model changed from gemini-3-pro-preview to gemini-2.5-flash", flush=True)
            _agent_graph_cache = None
            _agent_graph_model = None
            _agent_graph_provider = None
    elif provider == "openai":
        current_model = os.getenv("OPENAI_MODEL", "gpt-4o")
    else:  # claude
        current_model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
    
    print(f"🔧 Selected provider: {provider}, model: {current_model}")
    
    # Cache key includes agent_type to separate todo and mortgage agent caches
    cache_key = f"{agent_type}:{provider}:{current_model}"
    global_cache_key = f"{_agent_graph_provider}:{_agent_graph_model}" if _agent_graph_provider else None
    
    # Return cached graph if available AND provider/model/agent_type hasn't changed
    if (_agent_graph_cache is not None and 
        _agent_graph_provider == provider and 
        _agent_graph_model == current_model and
        hasattr(_agent_graph_cache, '_agent_type') and 
        getattr(_agent_graph_cache, '_agent_type', None) == agent_type):
        print(f"♻️ Using cached {agent_type} agent graph (provider: {provider}, model: {current_model})")
        return _agent_graph_cache
    
    # Clear cache if provider, model, or agent_type changed
    if _agent_graph_cache is not None:
        cached_agent_type = getattr(_agent_graph_cache, '_agent_type', None)
        if (_agent_graph_provider != provider or 
            _agent_graph_model != current_model or
            cached_agent_type != agent_type):
            print(f"🔄 Provider/model/agent_type changed (provider: {_agent_graph_provider}→{provider}, model: {_agent_graph_model}→{current_model}, agent_type: {cached_agent_type}→{agent_type}), clearing cache")
            _agent_graph_cache = None
            _agent_graph_model = None
            _agent_graph_provider = None
    
    # Use lock to prevent multiple simultaneous initializations
    async with _agent_graph_lock:
        # Check again after acquiring lock (another thread might have initialized)
        if (_agent_graph_cache is not None and 
            _agent_graph_provider == provider and 
            _agent_graph_model == current_model and
            getattr(_agent_graph_cache, '_agent_type', None) == agent_type):
            return _agent_graph_cache
        
        print("🔧 Initializing agent graph (first time only)...")
        
        config_path = os.path.join(os.path.dirname(__file__), 'mcps', 'mcp_config.json')
        if not os.path.exists(config_path):
            # Fallback path for when running from the root directory
            config_path = os.path.join('convonet', 'mcps', 'mcp_config.json')
        
        if not os.path.exists(config_path):
            error_msg = f"❌ MCP config file not found. Tried: {os.path.join(os.path.dirname(__file__), 'mcps', 'mcp_config.json')} and {config_path}"
            print(error_msg)
            raise FileNotFoundError(error_msg)

        with open(config_path) as f:
            mcp_config = json.load(f)
        
        # Set working directory to project root for MCP servers
        # __file__ is: /Users/hj/Web Development Projects/1. Main/convonet/routes.py
        # We need: /Users/hj/Web Development Projects/1. Main
        project_root = os.path.dirname(os.path.dirname(__file__))
        original_cwd = os.getcwd()
        os.chdir(project_root)
        
        # Update the MCP config with absolute paths and environment variables
        for server_name, server_config in mcp_config["mcpServers"].items():
            if "args" in server_config and len(server_config["args"]) > 0:
                # Convert relative path to absolute path
                relative_path = server_config["args"][0]
                if not os.path.isabs(relative_path):
                    absolute_path = os.path.join(project_root, relative_path)
                    server_config["args"][0] = absolute_path
            
            # Handle environment variable substitution in env section
            if "env" in server_config:
                for env_key, env_value in server_config["env"].items():
                    if isinstance(env_value, str) and env_value.startswith("${") and env_value.endswith("}"):
                        # Extract environment variable name
                        env_var_name = env_value[2:-1]
                        env_var_value = os.getenv(env_var_name)
                        if env_var_value:
                            server_config["env"][env_key] = env_var_value
                            print(f"🔧 MCP config: Set {env_key}={env_var_name} from environment")
                        else:
                            print(f"⚠️  MCP config: Environment variable {env_var_name} not found")
        
        # Initialize tools list to avoid UnboundLocalError
        tools = []
        
        # Use cached MCP tools if available (pre-loaded at startup to avoid hangs)
        global _mcp_tools_cache
        if _mcp_tools_cache is not None:
            print(f"✅ Using cached MCP tools ({len(_mcp_tools_cache)} tools)")
            tools = _mcp_tools_cache.copy()  # Copy to avoid mutation
            # Note: Tool limiting for Gemini happens AFTER all tools are added (transfer + composio)
            # This ensures we limit the final tool count, not just MCP tools
        else:
            # Try to load MCP tools (with timeout to prevent hangs)
            print("🔧 Loading MCP tools (not cached yet)...")
            try:
                # Initialize MCP client (langchain-mcp-adapters 0.1.0+ does not support context manager)
                print("🔧 Creating MCP client...")
                client = MultiServerMCPClient(connections=mcp_config["mcpServers"])
                print("🔧 Getting tools from MCP client...")
                
                # Create a wrapper function to catch any exceptions from nested coroutines
                async def safe_get_tools():
                    try:
                        return await client.get_tools()
                    except (UnboundLocalError, NameError) as e:
                        # Re-raise as a different exception type so we can catch it
                        raise RuntimeError(f"MCP library UnboundLocalError: {e}") from e
                    except Exception as e:
                        error_str = str(e)
                        if "UnboundLocalError" in error_str or "cannot access local variable 'tools'" in error_str:
                            raise RuntimeError(f"MCP library UnboundLocalError (wrapped): {e}") from e
                        raise
                
                # Use timeout to prevent hangs (longer timeout since this is first load)
                # Increased timeout for MCP tools loading as it can take longer on first request
                timeout_seconds = 45.0  # 45 seconds for initial load (was 15s)
                try:
                    tools = await asyncio.wait_for(safe_get_tools(), timeout=timeout_seconds)
                    print(f"✅ MCP client initialized successfully with {len(tools)} tools")
                    
                    # Cache the tools for future use (including Gemini)
                    _mcp_tools_cache = tools.copy()
                    print(f"✅ MCP tools cached for future requests")
                except asyncio.TimeoutError:
                    print(f"⏱️ MCP get_tools() timed out after {timeout_seconds} seconds")
                    print(f"⚠️ Continuing with empty tools list - tool calls will not be available")
                    print(f"💡 MCP tools will be retried on next request")
                    tools = []
                except RuntimeError as e:
                    # Catch our wrapped UnboundLocalError
                    if "UnboundLocalError" in str(e):
                        print(f"⚠️ MCP library error (UnboundLocalError): {e}")
                        print("⚠️ Continuing with empty tools list")
                        tools = []  # Ensure tools is set to empty list
                    else:
                        raise
                except (UnboundLocalError, NameError) as e:
                    # Handle library bug where tools variable is referenced before assignment
                    print(f"⚠️ MCP library error (UnboundLocalError/NameError): {e}")
                    print("⚠️ Continuing with empty tools list")
                    tools = []  # Ensure tools is set to empty list
                except Exception as e:
                    # Check if the error message contains UnboundLocalError (might be wrapped)
                    error_str = str(e)
                    if "UnboundLocalError" in error_str or "cannot access local variable 'tools'" in error_str:
                        print(f"⚠️ MCP library error (wrapped UnboundLocalError): {e}")
                        print("⚠️ Continuing with empty tools list")
                        tools = []  # Ensure tools is set to empty list
                    else:
                        # Handle any other errors from get_tools()
                        print(f"⚠️ Error getting MCP tools: {e}")
                        print("⚠️ Continuing with empty tools list")
                        tools = []  # Ensure tools is set to empty list
                
                # Add call transfer tools (non-MCP tools) - optional
                try:
                    from .mcps.local_servers.call_transfer import get_transfer_tools
                    transfer_tools = get_transfer_tools()
                    tools.extend(transfer_tools)
                    print(f"✅ Added {len(transfer_tools)} call transfer tools")
                except ImportError as e:
                    print(f"⚠️ Call transfer tools not available: {e}")
                    print("⚠️ Continuing without call transfer tools")
                except Exception as e:
                    print(f"⚠️ Failed to load call transfer tools: {e}")
                    print("⚠️ Continuing without call transfer tools")
                
                # Add Composio integration tools (optional)
                try:
                    from .composio_tools import get_all_integration_tools, test_composio_connection
                    if test_composio_connection():
                        composio_tools = get_all_integration_tools()
                        tools.extend(composio_tools)
                        print(f"✅ Added {len(composio_tools)} Composio integration tools")
                    else:
                        print("⚠️ Composio connection test failed, skipping external integrations")
                except ImportError as e:
                    print(f"⚠️ Composio not available: {e}")
                    print("⚠️ Continuing without external integrations")
                except Exception as e:
                    print(f"⚠️ Failed to load Composio tools: {e}")
                    print("⚠️ Continuing without external integrations")
            except Exception as e:
                print(f"⚠️ Error in MCP tools loading: {e}")
                import traceback
                traceback.print_exc()
                tools = []
        
        # Build agent graph with whatever tools we have (even if empty)
        # This ensures we always try to build the graph, even if MCP tools failed
        try:
            import sys
            # For Gemini, optionally limit the number of tools to reduce memory usage
            if provider == "gemini":
                max_gemini_tools = int(os.getenv("GEMINI_MAX_TOOLS", "0"))  # 0 = no limit
                if max_gemini_tools > 0 and len(tools) > max_gemini_tools:
                    # Prioritize create_xxx tools (most commonly requested actions)
                    priority_tool_names = [
                        "create_calendar_event",
                        "create_todo",
                        "create_reminder",
                        "create_team",
                        "create_team_todo"
                    ]
                    
                    # Separate priority and other tools
                    priority_tools = [t for t in tools if hasattr(t, 'name') and t.name in priority_tool_names]
                    other_tools = [t for t in tools if not (hasattr(t, 'name') and t.name in priority_tool_names)]
                    
                    # Take priority tools first, then fill remaining slots with other tools
                    limited_tools = priority_tools[:max_gemini_tools]
                    remaining_slots = max_gemini_tools - len(limited_tools)
                    if remaining_slots > 0:
                        limited_tools.extend(other_tools[:remaining_slots])
                    
                    print(f"⚠️ Limited Gemini tools to {len(limited_tools)} (from {len(tools)}) to reduce memory usage", flush=True)
                    sys.stdout.flush()
                    tools = limited_tools
            
            # Filter tools based on agent type
            if agent_type == "mortgage":
                # Filter to only mortgage-related tools
                mortgage_tool_names = [
                    "create_mortgage_application",
                    "get_mortgage_application_status",
                    "update_mortgage_financial_info",
                    "calculate_dti_ratio",
                    "add_mortgage_debt",
                    "get_mortgage_debts",
                    "upload_mortgage_document",
                    "get_mortgage_documents",
                    "get_required_documents",
                    "get_missing_documents"
                ]
                # Debug: Check tool attributes
                if tools and len(tools) > 0:
                    sample_tool = tools[0]
                    print(f"🔍 Sample tool type: {type(sample_tool)}", flush=True)
                    print(f"🔍 Sample tool attributes: {[attr for attr in dir(sample_tool) if not attr.startswith('_')][:10]}", flush=True)
                    if hasattr(sample_tool, 'name'):
                        print(f"🔍 Sample tool name: {sample_tool.name}", flush=True)
                    # Try alternative attribute names
                    if hasattr(sample_tool, 'func') and hasattr(sample_tool.func, '__name__'):
                        print(f"🔍 Sample tool func name: {sample_tool.func.__name__}", flush=True)
                
                # Try multiple ways to get tool name
                filtered_tools = []
                for t in tools:
                    tool_name = None
                    if hasattr(t, 'name'):
                        tool_name = t.name
                    elif hasattr(t, 'func') and hasattr(t.func, '__name__'):
                        tool_name = t.func.__name__
                    elif hasattr(t, '__name__'):
                        tool_name = t.__name__
                    
                    if tool_name and tool_name in mortgage_tool_names:
                        filtered_tools.append(t)
                        print(f"✅ Found mortgage tool: {tool_name}", flush=True)
                
                tools = filtered_tools
                print(f"🏠 Filtered to {len(tools)} mortgage tools (from {len(_mcp_tools_cache) if _mcp_tools_cache else 0} total tools)", flush=True)
            else:
                # Filter to exclude mortgage tools (keep todo/team/calendar/transfer tools)
                mortgage_tool_names = [
                    "create_mortgage_application",
                    "get_mortgage_application_status",
                    "update_mortgage_financial_info",
                    "calculate_dti_ratio",
                    "add_mortgage_debt",
                    "get_mortgage_debts",
                    "upload_mortgage_document",
                    "get_mortgage_documents",
                    "get_required_documents",
                    "get_missing_documents"
                ]
                filtered_tools = [t for t in tools if not (hasattr(t, 'name') and t.name in mortgage_tool_names)]
                tools = filtered_tools
                print(f"📝 Filtered to {len(tools)} todo/team tools (excluded mortgage tools)", flush=True)
            
            print(f"🔧 Building {agent_type} agent graph with {len(tools)} tools...", flush=True)
            sys.stdout.flush()
            print(f"🔧 Using provider: {provider}, model: {current_model}", flush=True)
            sys.stdout.flush()
            if _mcp_tools_cache is not None:
                # Show actual tools being used, not cached count (may be limited for Gemini)
                print(f"✅ Using {len(tools)} tools for agent graph (cached: {len(_mcp_tools_cache)} tools)", flush=True)
                sys.stdout.flush()
            
            agent_class_name = "MortgageAgent" if agent_type == "mortgage" else "TodoAgent"
            print(f"⏱️ Starting {agent_class_name} initialization (this may take a few seconds)...", flush=True)
            sys.stdout.flush()
            
            # CRITICAL FIX: Agent.__init__ is synchronous and blocks the event loop
            # For Gemini, bind_tools() can hang indefinitely
            # Run agent creation in a separate thread with timeout to prevent blocking
            print(f"🚀 About to create {agent_class_name} instance in separate thread...", flush=True)
            sys.stdout.flush()
            
            import threading
            # Don't import time here - import it locally in create_agent() to avoid scoping conflicts
            
            agent_result = {'agent': None, 'error': None, 'done': False}
            
            def create_agent():
                """Create agent in separate thread to prevent blocking"""
                import sys
                import time as thread_time  # Import time locally to avoid scoping issues
                try:
                    print(f"🧵 Thread: Starting {agent_class_name} creation...", flush=True)
                    sys.stdout.flush()
                    start_time = thread_time.time()
                    
                    if agent_type == "mortgage":
                        agent_result['agent'] = MortgageAgent(tools=tools, provider=provider, model=current_model)
                    else:
                        agent_result['agent'] = TodoAgent(tools=tools, provider=provider, model=current_model)
                    
                    elapsed = thread_time.time() - start_time
                    print(f"🧵 Thread: {agent_class_name} created successfully in {elapsed:.2f}s", flush=True)
                    sys.stdout.flush()
                except Exception as e:
                    print(f"🧵 Thread: {agent_class_name} creation failed: {e}", flush=True)
                    sys.stdout.flush()
                    import traceback
                    traceback.print_exc()
                    agent_result['error'] = e
                finally:
                    agent_result['done'] = True
                    print(f"🧵 Thread: {agent_class_name} creation thread finished", flush=True)
                    sys.stdout.flush()
            
            # Use aggressive timeout for Gemini (8s) vs others (12s)
            timeout_seconds = 8.0 if provider == "gemini" else 12.0
            print(f"⏱️ Creating {agent_class_name} with {timeout_seconds}s timeout...", flush=True)
            sys.stdout.flush()
            
            agent_thread = threading.Thread(target=create_agent, daemon=True)
            agent_thread.start()
            agent_thread.join(timeout=timeout_seconds)
            
            if not agent_result['done']:
                print(f"⏱️ {agent_class_name} creation timed out after {timeout_seconds} seconds", flush=True)
                sys.stdout.flush()
                raise TimeoutError(f"{agent_class_name} initialization timed out after {timeout_seconds}s - likely Gemini bind_tools() hang")
            elif agent_result['error']:
                print(f"❌ {agent_class_name} creation failed: {agent_result['error']}", flush=True)
                sys.stdout.flush()
                raise agent_result['error']
            elif agent_result['agent']:
                agent = agent_result['agent']
                print(f"✅ {agent_class_name} created successfully, graph already built in __init__", flush=True)
                sys.stdout.flush()
            else:
                raise Exception(f"{agent_class_name} creation returned no result")
            
            # Graph is already built in Agent.__init__, just get it
            _agent_graph_cache = agent.graph
            # Store agent_type in graph for cache validation
            _agent_graph_cache._agent_type = agent_type
            _agent_graph_model = current_model  # Store the model used for this cache
            _agent_graph_provider = provider  # Store the provider used for this cache
            print(f"✅ {agent_type} agent graph cached for future requests (provider: {provider}, model: {current_model})")
            return _agent_graph_cache
        except Exception as e:
            print(f"❌ Error building agent graph: {e}")
            import traceback
            print(f"❌ Traceback: {traceback.format_exc()}")
            # Don't raise - try to build with empty tools as last resort
            print(f"⚠️ Attempting to build {agent_type} graph with empty tools list as fallback...")
            try:
                if agent_type == "mortgage":
                    fallback_agent = MortgageAgent(tools=[], provider=provider)
                else:
                    fallback_agent = TodoAgent(tools=[], provider=provider)
                _agent_graph_cache = fallback_agent.build_graph()
                _agent_graph_cache._agent_type = agent_type
                _agent_graph_model = current_model  # Store the model used for this cache
                _agent_graph_provider = provider  # Store the provider used for this cache
                print(f"✅ {agent_type} agent graph built with empty tools list (fallback, provider: {provider}, model: {current_model})")
                return _agent_graph_cache
            except Exception as fallback_error:
                print(f"❌ Even fallback graph building failed: {fallback_error}")
                raise Exception(f"Failed to build agent graph even with empty tools: {str(e)}")
        finally:
            os.chdir(original_cwd)


async def _run_agent_for_pin_verification(pin: str) -> str:
    """Run agent specifically for PIN verification."""
    try:
        agent_graph = await _get_agent_graph()
        
        # Create a state that will trigger verify_user_pin tool
        input_state = AgentState(
            messages=[HumanMessage(content=f"User is authenticating with PIN: {pin}. Please verify their PIN using the verify_user_pin tool.")],
            customer_id="",
            is_authenticated=False
        )
        config = {"configurable": {"thread_id": f"pin-verify-{pin}"}}
        
        # Stream through the graph
        stream = agent_graph.astream(input=input_state, stream_mode="values", config=config)
        
        async def process_stream():
            tool_result = None
            async for state in stream:
                # Look for tool messages which contain the actual verification result
                if "messages" in state:
                    for msg in state["messages"]:
                        # Check if this is a ToolMessage with our authentication result
                        if hasattr(msg, 'content') and 'AUTHENTICATED:' in str(msg.content):
                            tool_result = msg.content
                            break
            
            # Return the tool result if found, otherwise authentication failed
            if tool_result:
                return tool_result
            return "AUTHENTICATION_FAILED: Invalid PIN"
        
        return await asyncio.wait_for(process_stream(), timeout=20.0)
    except asyncio.TimeoutError:
        print(f"PIN verification timeout for PIN: {pin}")
        return "AUTHENTICATION_FAILED: Verification timeout"
    except Exception as e:
        print(f"Error in PIN verification: {e}")
        import traceback
        traceback.print_exc()
        return f"AUTHENTICATION_ERROR: {str(e)}"

async def _run_agent_async(
    prompt: str,
    user_id: Optional[str] = None,
    user_name: Optional[str] = None,
    reset_thread: bool = False,
    include_metadata: bool = False,
    socketio=None,
    session_id: Optional[str] = None,
    model: Optional[str] = None,  # Optional model override (e.g., "claude-3-5-haiku-20241022" for voice)
    text_chunk_callback: Optional[callable] = None,  # Optional callback for text chunks (for early TTS)
    tool_call_callback: Optional[callable] = None,   # Optional callback when tool calls are detected
) -> str | dict:
    """Runs the agent for a given prompt and returns the final response.
    
    Args:
        prompt: User's input text
        user_id: Authenticated user ID
        user_name: User's display name
        reset_thread: If True, starts a new conversation thread (used after timeouts/errors)
    """
    # Import agent monitor for tracking
    from .agent_monitor import get_agent_monitor, AgentInteractionStatus, ToolCallInfo
    
    # Declare global variables at the start of the function
    global _agent_graph_cache, _agent_graph_model, _agent_graph_provider
    
    request_id = str(uuid.uuid4())
    start_time = time.time()
    monitor = get_agent_monitor()
    
    # Detect mortgage intent from user prompt
    prompt_text = prompt.strip().lower() if prompt else ""
    has_mortgage_intent = detect_mortgage_intent(prompt)
    
    # Sticky mortgage context (keeps user in mortgage flow after initial intent)
    # Allow explicit todo intent to override sticky mortgage context.
    todo_keywords = [
        "todo", "reminder", "calendar", "meeting", "schedule",
        "task", "create a todo", "create a reminder", "create a meeting"
    ]
    has_todo_intent = any(keyword in prompt_text for keyword in todo_keywords)
    stored_agent_type = None
    if user_id and redis_manager.is_available():
        try:
            stored_agent_type = redis_manager.redis_client.get(f"agent_type:{user_id}")
            if isinstance(stored_agent_type, bytes):
                stored_agent_type = stored_agent_type.decode("utf-8")
        except Exception as e:
            print(f"⚠️ Error reading agent context from Redis: {e}", flush=True)
    
    if stored_agent_type == "mortgage" and not has_todo_intent:
        if not has_mortgage_intent:
            print(f"🏠 Using sticky mortgage context for user_id {user_id}", flush=True)
        has_mortgage_intent = True
    elif has_todo_intent and stored_agent_type == "mortgage":
        print(f"📝 Todo intent detected; clearing sticky mortgage context for user_id {user_id}", flush=True)
        if user_id and redis_manager.is_available():
            try:
                redis_manager.redis_client.delete(f"agent_type:{user_id}")
            except Exception as e:
                print(f"⚠️ Error clearing agent context from Redis: {e}", flush=True)
    
    agent_type = "mortgage" if has_mortgage_intent else "todo"
    if agent_type == "mortgage":
        print(f"🏠 Mortgage intent detected, using MortgageAgent", flush=True)
        print(f"🏠 Prompt was: '{prompt}'", flush=True)
    else:
        print(f"📝 Using TodoAgent (default)", flush=True)
        print(f"📝 Prompt was: '{prompt}'", flush=True)

    # Persist agent context so short replies (e.g., "Monthly income is 5,000") stay in mortgage flow
    if agent_type == "mortgage" and user_id and redis_manager.is_available():
        try:
            redis_manager.redis_client.setex(f"agent_type:{user_id}", 1800, "mortgage")  # 30 min TTL
        except Exception as e:
            print(f"⚠️ Error storing agent context in Redis: {e}", flush=True)
    
    # Add early logging to track provider selection
    print(f"🔧 Getting agent graph for user_id: {user_id}, agent_type: {agent_type}", flush=True)
    import sys
    sys.stdout.flush()
    
    try:
        # Add aggressive timeout to agent graph initialization to prevent hanging
        # Use shorter timeout for Gemini (8s) vs others (12s)
        try:
            # Check if we're using Gemini
            from .redis_manager import get_redis_manager
            redis_mgr = get_redis_manager()
            is_gemini = False
            try:
                user_pref = redis_mgr.get(f"user:{user_id}:llm_provider") if user_id else None
                if not user_pref:
                    user_pref = redis_mgr.get("user:default:llm_provider")
                is_gemini = (user_pref == "gemini")
                print(f"🔍 Provider preference check: user_pref={user_pref}, is_gemini={is_gemini}", flush=True)
                sys.stdout.flush()
            except Exception as e:
                print(f"⚠️ Error checking provider preference: {e}", flush=True)
                sys.stdout.flush()
            
            # Increased timeout to accommodate MCP tools loading (can take up to 45s)
            # Gemini needs longer timeout too since MCP tools loading is the same for all providers
            timeout_seconds = 50.0 if is_gemini else 50.0  # Same timeout for all providers (was 8s/12s)
            print(f"⏱️ Using {timeout_seconds}s timeout for agent graph initialization (Gemini: {is_gemini})", flush=True)
            sys.stdout.flush()
            
            print(f"🚀 About to call _get_agent_graph() with timeout...", flush=True)
            sys.stdout.flush()
            agent_graph = await asyncio.wait_for(
                _get_agent_graph(user_id=user_id, agent_type=agent_type, model=model),
                timeout=timeout_seconds
            )
            print(f"✅ Agent graph obtained successfully", flush=True)
            sys.stdout.flush()
        except asyncio.TimeoutError:
            print(f"⏱️ Agent graph initialization timed out after {timeout_seconds} seconds", flush=True)
            sys.stdout.flush()
            raise
        
        # Now execute the graph - this is where Gemini might hang
        print(f"🚀 About to execute agent graph with prompt...", flush=True)
        sys.stdout.flush()
    except asyncio.TimeoutError:
        print(f"⏱️ Agent graph initialization timed out after 10 seconds")
        error_msg = "Agent initialization timed out. Please try again."
        # Track timeout
        duration_ms = (time.time() - start_time) * 1000
        monitor.track_interaction(
            request_id=request_id,
            user_id=user_id,
            user_name=user_name,
            provider=None,
            model=None,
            user_prompt=prompt,
            agent_response=error_msg,
            tool_calls=[],
            status=AgentInteractionStatus.TIMEOUT,
            duration_ms=duration_ms,
            error="Agent graph initialization timeout"
        )
        return error_msg
    except Exception as e:
        print(f"❌ Failed to initialize agent: {e}")
        lower_prompt = prompt.lower()
        fallback_extension = os.getenv('VOICE_AGENT_FALLBACK_EXTENSION', '2001')
        fallback_department = os.getenv('VOICE_AGENT_FALLBACK_DEPARTMENT', 'support')
        transfer_keywords = [
            "transfer",
            "speak to a human",
            "human agent",
            "talk to an agent",
            "representative",
            "customer service",
            "live agent"
        ]
        if any(keyword in lower_prompt for keyword in transfer_keywords):
            reason = "Automated transfer triggered due to assistant service interruption"
            print(f"🔄 Fallback transfer to extension {fallback_extension} ({fallback_department}) because agent initialization failed.")
            return f"TRANSFER_INITIATED:{fallback_extension}|{fallback_department}|{reason}"
        return "I'm sorry, there's a temporary system issue. Please try again in a moment."

    # For mortgage agent, inject authenticated_user_id into the prompt context
    # so the LLM knows the actual UUID value to use
    if agent_type == "mortgage" and user_id:
        # Add context about authenticated_user_id to the prompt
        enhanced_prompt = f"""[SYSTEM CONTEXT: Your authenticated_user_id is {user_id}. Use this exact value for all mortgage tool calls that require user_id parameter. DO NOT ask the user for their user_id.]

{prompt}"""
    else:
        enhanced_prompt = prompt
    
    input_state = AgentState(
        messages=[HumanMessage(content=enhanced_prompt)],
        customer_id="",
        authenticated_user_id=user_id,
        authenticated_user_name=user_name,
        is_authenticated=bool(user_id),
        request_id=request_id  # Add request_id for tool execution tracking
    )
    
    # Use timestamped thread ID after errors to start fresh conversation
    # Use module-level time import to avoid scoping issues
    import time as time_for_thread
    thread_suffix = f"-{int(time_for_thread.time())}" if reset_thread else ""
    thread_id = f"user-{user_id}{thread_suffix}" if user_id else f"flask-thread-1{thread_suffix}"
    config = {"configurable": {"thread_id": thread_id}}
    
    # Debug logging
    if reset_thread:
        print(f"🆕 Using FRESH thread_id: {thread_id} (reset=True)")
    else:
        print(f"📝 Using existing thread_id: {thread_id} (reset=False)")

    # Stream through the graph to execute the agent logic with timeout
    try:
        # Get provider and model from cached graph (they're set when graph is created)
        current_provider = _agent_graph_provider
        current_model = _agent_graph_model
        
        print(f"🚀 Starting agent execution", flush=True)
        sys.stdout.flush()
        print(f"🚀 Provider: {current_provider}, Model: {current_model}", flush=True)
        sys.stdout.flush()
        
        # OPTIMIZED TIMEOUTS: Reduced for lower latency
        # Reduced execution timeout for faster response (was 15s, now 12s for Claude/OpenAI)
        # Gemini uses native SDK streaming (no timeout needed) or 60s fallback
        # Timeouts need to account for tool execution + response generation
        # Gemini: Tool execution (15s) + processing results (10-15s) + response (10-15s) = ~40-45s
        # Claude/OpenAI: Tool execution (6-10s) + processing results (5-10s) + response (5-10s) = ~20-30s
        # Add buffer for safety
        execution_timeout = 50.0 if is_gemini else 40.0  # 50s for Gemini, 40s for Claude/OpenAI (increased from 25s)
        print(f"⏱️ Using {execution_timeout}s timeout for graph execution (Gemini: {is_gemini})", flush=True)
        sys.stdout.flush()
        
        async def process_stream():
            # Import time module explicitly to avoid scoping issues
            # Use 'import time' at function start to ensure it's available
            import time as time_module
            import asyncio
            import uuid
            # Capture start_time from outer scope IMMEDIATELY
            # This must happen before any other code to avoid scoping issues
            process_start_time = start_time
            transfer_marker = None
            tool_calls_info = []
            
            # HYBRID STREAMING: Use native Gemini SDK for streaming, LangGraph for tool execution
            if is_gemini:
                print(f"🚀 Using native Gemini SDK for hybrid streaming...", flush=True)
                sys.stdout.flush()
                
                try:
                    # Try to use native Gemini SDK for streaming
                    from .gemini_streaming import stream_gemini_with_tools, GEMINI_SDK_AVAILABLE
                    from .llm_provider_manager import get_llm_provider_manager
                    
                    if GEMINI_SDK_AVAILABLE:
                        # Get Gemini API key and model
                        provider_mgr = get_llm_provider_manager()
                        gemini_api_key = os.getenv("GOOGLE_API_KEY")
                        gemini_model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
                        
                        # Get tools from MCP cache (same tools used to build agent graph)
                        tools = _mcp_tools_cache if _mcp_tools_cache is not None else []
                        print(f"🔧 Got {len(tools)} tools from MCP cache for Gemini streaming", flush=True)
                        sys.stdout.flush()
                        
                        # Get system prompt - use a simplified version for Gemini native SDK
                        # The full system prompt is in TodoAgent, but we use a concise version here
                        system_prompt = """You are a productivity assistant that helps users manage todos, reminders, and calendar events. You MUST use tools to perform actions - never just ask for more information.

CRITICAL RULES:
1. ALWAYS use tools when users mention creating, adding, or doing something
2. NEVER ask "what would you like to create?" - infer the intent and use the appropriate tool
3. Be proactive - if user says "create", "add", "todo", "reminder", "calendar event", immediately use the tool
4. Make reasonable assumptions when details are missing

Your messages are read aloud, so be brief and conversational."""
                        
                        # Get conversation history from AgentState
                        # AgentState is a Pydantic BaseModel, so we access it as an attribute
                        conversation_messages = input_state.messages if hasattr(input_state, 'messages') else []
                        
                        # Stream using native SDK
                        print(f"📡 Streaming Gemini response with native SDK...", flush=True)
                        sys.stdout.flush()
                        
                        final_response, tool_calls_list = await stream_gemini_with_tools(
                            prompt=prompt,
                            api_key=gemini_api_key,
                            model=gemini_model,
                            tools=tools,
                            system_prompt=system_prompt,
                            messages=conversation_messages,
                            socketio=socketio,
                            session_id=session_id,
                        )
                        
                        # Convert tool calls to ToolCallInfo (with results from Gemini streaming)
                        tool_calls_info = []
                        for tc in tool_calls_list:
                            tool_id = tc.get('id', str(uuid.uuid4()))
                            tool_call_info = ToolCallInfo(
                                tool_name=tc.get('name', 'unknown'),
                                tool_id=tool_id,
                                arguments=tc.get('args', {})
                            )
                            # Populate result, status, error, and duration if available
                            if 'result' in tc:
                                tool_call_info.result = tc.get('result')
                            if 'status' in tc:
                                tool_call_info.status = tc.get('status', 'pending')
                            if 'error' in tc:
                                tool_call_info.error = tc.get('error')
                            if 'duration_ms' in tc:
                                tool_call_info.duration_ms = tc.get('duration_ms')
                            tool_calls_info.append(tool_call_info)
                        
                        print(f"📊 Gemini tool calls info: {len(tool_calls_info)} tool call(s) with results", flush=True)
                        for tc_info in tool_calls_info:
                            print(f"   - {tc_info.tool_name}: status={tc_info.status}, result={'present' if tc_info.result else 'missing'}", flush=True)
                        
                        print(f"✅ Gemini native streaming completed", flush=True)
                        print(f"📝 Final response length: {len(final_response)} chars", flush=True)
                        print(f"📝 Final response preview: {final_response[:200]}...", flush=True)
                        sys.stdout.flush()
                        final_state = None  # Not using LangGraph state for native streaming
                        final_messages = []  # Initialize for native SDK path - tool calls already extracted
                    else:
                        raise ImportError("Native Gemini SDK not available")
                        
                except (ImportError, Exception) as e:
                    print(f"⚠️ Native Gemini SDK streaming failed: {e}, falling back to ainvoke()", flush=True)
                    sys.stdout.flush()
                    # Fallback to ainvoke() if native SDK fails
                    ainvoke_timeout = 60.0
                    try:
                        final_state = await asyncio.wait_for(
                            agent_graph.ainvoke(input=input_state, config=config),
                            timeout=ainvoke_timeout
                        )
                        print(f"✅ Gemini ainvoke() completed within {ainvoke_timeout}s", flush=True)
                        sys.stdout.flush()
                    except asyncio.TimeoutError:
                        print(f"⏱️ Gemini ainvoke() timed out after {ainvoke_timeout}s", flush=True)
                        sys.stdout.flush()
                        final_response = "I'm sorry, the request is taking too long. Please try again or use a different provider."
                        tool_calls_info = []
                        final_state = None
                        final_messages = []  # Initialize for timeout case
                    else:
                        final_messages = final_state.get("messages", [])
                        last_message = final_messages[-1] if final_messages else None
                        if last_message:
                            content = getattr(last_message, 'content', "")
                            # Handle case where content is a list (e.g., AIMessage with multiple content blocks)
                            if isinstance(content, list):
                                # Extract text from list of content blocks
                                text_parts = []
                                for item in content:
                                    if isinstance(item, dict) and 'text' in item:
                                        text_parts.append(item['text'])
                                    elif isinstance(item, str):
                                        text_parts.append(item)
                                final_response = ' '.join(text_parts) if text_parts else ""
                            else:
                                final_response = str(content) if content else ""
                        else:
                            final_response = ""
                        
                        tool_calls_info = []
                        for msg in final_messages:
                            if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                for tc in msg.tool_calls:
                                    tool_id = getattr(tc, 'id', getattr(tc, 'tool_call_id', str(uuid.uuid4())))
                                    tool_name = getattr(tc, 'name', getattr(tc, 'functionName', 'unknown'))
                                    args = getattr(tc, 'args', getattr(tc, 'arguments', {}))
                                    tool_calls_info.append(ToolCallInfo(
                                        tool_name=tool_name,
                                        tool_id=tool_id,
                                        arguments=args if isinstance(args, dict) else {}
                                    ))
                
                # Set final_response if empty
                # Convert final_response to string if it's not already (handles list content)
                if not isinstance(final_response, str):
                    if isinstance(final_response, list):
                        # Extract text from list of content blocks
                        text_parts = []
                        for item in final_response:
                            if isinstance(item, dict) and 'text' in item:
                                text_parts.append(item['text'])
                            elif isinstance(item, str):
                                text_parts.append(item)
                        final_response = ' '.join(text_parts) if text_parts else ""
                    else:
                        final_response = str(final_response) if final_response else ""
                
                if not final_response or (isinstance(final_response, str) and final_response.strip() == ""):
                    final_response = "I'm processing your request. Please wait a moment and try again if you don't see a response."
            else:
                # For non-Gemini providers, use astream() as normal
                # Create stream inside async function so it's in the right event loop context
                print(f"📡 Creating agent graph stream inside process_stream...", flush=True)
                sys.stdout.flush()
                stream = None
                stream_iter = None
                try:
                    stream = agent_graph.astream(input=input_state, stream_mode="values", config=config)
                    print(f"✅ Agent graph stream created, starting execution...", flush=True)
                    sys.stdout.flush()
                
                    print(f"🔄 Processing agent stream...", flush=True)
                    sys.stdout.flush()
                    # OPTIMIZED STREAMING: Timeouts must be longer than tool execution timeout
                    # Tool execution timeout is 6.0s, so stream timeout should be at least 10s
                    # This ensures we get tool results even if tool execution takes the full timeout
                    # Increased from 8s to 10s to give more time for tool execution
                    stream_timeout = 10.0  # Must be > tool_timeout (6.0s) to get tool results
                    stream_iter = stream.__aiter__()
                    states_processed = 0
                    max_states = 50  # Prevent infinite loops
                    
                    # Add watchdog timer - if we don't get a state update within this time, force exit
                    import time as watchdog_time
                    last_state_time = watchdog_time.time()
                    # Watchdog should be longer than stream timeout to allow tool execution
                    watchdog_timeout = 10.0  # Maximum time between state updates (allows tool execution + buffer)
                    
                    try:
                        while states_processed < max_states:
                            # Check watchdog - if too much time has passed since last state, force exit
                            current_time = watchdog_time.time()
                            time_since_last_state = current_time - last_state_time
                            if time_since_last_state > watchdog_timeout:
                                print(f"⚠️ Watchdog timeout: {time_since_last_state:.2f}s since last state update - forcing exit", flush=True)
                                sys.stdout.flush()
                                break
                        
                            try:
                                # Get next state with timeout - this prevents hanging on a single iteration
                                # Use asyncio.wait_for with a shorter timeout to catch hangs early
                                print(f"⏳ Waiting for next state update (timeout: {stream_timeout}s, watchdog: {watchdog_timeout - time_since_last_state:.1f}s remaining)...", flush=True)
                                sys.stdout.flush()
                                state = await asyncio.wait_for(stream_iter.__anext__(), timeout=stream_timeout)
                                print(f"✅ Received state update in time", flush=True)
                                sys.stdout.flush()
                                states_processed += 1
                                last_state_time = watchdog_time.time()  # Update watchdog timer
                                print(f"📊 Received state update #{states_processed} from agent graph", flush=True)
                                sys.stdout.flush()
                                
                                # STREAMING OPTIMIZATION: Emit text chunks as they arrive
                                if "messages" in state and socketio and session_id:
                                    for msg in state.get("messages", []):
                                        # Emit text chunks incrementally for lower latency
                                        if hasattr(msg, 'content') and isinstance(msg.content, str) and msg.content:
                                            # Only emit if this is a new AI message (not already emitted)
                                            if isinstance(msg, type) and hasattr(msg, '__class__'):
                                                from langchain_core.messages import AIMessage
                                                if isinstance(msg, AIMessage):
                                                    # Emit to client via Socket.IO
                                                    socketio.emit(
                                                        'agent_stream_chunk',
                                                        {'text': msg.content, 'type': 'text'},
                                                        namespace='/voice',
                                                        room=session_id
                                                    )
                                                    # Call text_chunk_callback for early TTS (server-side)
                                                    if text_chunk_callback:
                                                        try:
                                                            text_chunk_callback(msg.content)
                                                        except Exception as callback_error:
                                                            print(f"⚠️ Error in text_chunk_callback: {callback_error}", flush=True)
                                
                                if "messages" in state:
                                    for msg in state["messages"]:
                                        # Check for TRANSFER_INITIATED in tool message content
                                        if hasattr(msg, 'content') and isinstance(msg.content, str):
                                            if 'TRANSFER_INITIATED:' in msg.content:
                                                transfer_marker = msg.content
                                                print(f"🔄 Transfer marker detected in tool result: {transfer_marker}")
                                        
                                        # Track tool calls
                                        if hasattr(msg, 'tool_calls') and msg.tool_calls:
                                            print(f"🔧 Detected {len(msg.tool_calls)} tool call(s) in state update #{states_processed}", flush=True)
                                            sys.stdout.flush()
                                            for tc in msg.tool_calls:
                                                tool_id = getattr(tc, 'id', getattr(tc, 'tool_call_id', str(uuid.uuid4())))
                                                tool_name = getattr(tc, 'name', getattr(tc, 'functionName', 'unknown'))
                                                args = getattr(tc, 'args', getattr(tc, 'arguments', {}))
                                                
                                                print(f"  → Tool: {tool_name} (id: {tool_id[:20]}...)", flush=True)
                                                sys.stdout.flush()
                                                
                                                if tool_call_callback:
                                                    try:
                                                        tool_call_callback(tool_name)
                                                    except Exception as callback_error:
                                                        print(f"⚠️ Error in tool_call_callback: {callback_error}", flush=True)
                                                
                                                tool_calls_info.append(ToolCallInfo(
                                                    tool_name=tool_name,
                                                    tool_id=tool_id,
                                                    arguments=args if isinstance(args, dict) else {}
                                                ))
                                        
                                        # Track tool results
                                        if hasattr(msg, 'tool_call_id') and hasattr(msg, 'content'):
                                            tool_call_id = msg.tool_call_id
                                            # Find matching tool call and update it
                                            for tc_info in tool_calls_info:
                                                if tc_info.tool_id == tool_call_id:
                                                    tc_info.result = msg.content
                                                    tc_info.status = "success"
                                                    break
                            except asyncio.TimeoutError:
                                print(f"⏱️ Stream iteration timed out after {stream_timeout}s waiting for next state - Gemini may be hanging", flush=True)
                                sys.stdout.flush()
                                # Break out of loop and try to get final state
                                break
                            except StopAsyncIteration:
                                # Stream is complete
                                print(f"✅ Stream completed after {states_processed} state updates", flush=True)
                                sys.stdout.flush()
                                break
                    except Exception as e:
                        print(f"❌ Error processing stream: {e}", flush=True)
                        sys.stdout.flush()
                        import traceback
                        traceback.print_exc()
                    finally:
                        # Cleanup: Close stream iterator to free resources
                        if stream_iter is not None:
                            try:
                                # Close async iterator if it has a close method
                                if hasattr(stream_iter, 'aclose'):
                                    await stream_iter.aclose()
                            except:
                                pass
                            stream_iter = None
                        if stream is not None:
                            stream = None
                
                    # Get final state and last message (only if we used astream, not invoke)
                    if not is_gemini:  # Only get final state if we used astream
                        try:
                            final_state = agent_graph.get_state(config=config)
                            final_messages = final_state.values.get("messages", [])
                            last_message = final_messages[-1] if final_messages else None
                            if last_message:
                                content = getattr(last_message, 'content', "")
                                # Handle case where content is a list (e.g., AIMessage with multiple content blocks)
                                if isinstance(content, list):
                                    # Extract text from list of content blocks
                                    text_parts = []
                                    for item in content:
                                        if isinstance(item, dict) and 'text' in item:
                                            text_parts.append(item['text'])
                                        elif isinstance(item, str):
                                            text_parts.append(item)
                                    final_response = ' '.join(text_parts) if text_parts else ""
                                else:
                                    final_response = str(content) if content else ""
                            else:
                                final_response = ""
                            # Clear state reference after extracting needed data
                            final_state = None
                        except Exception as e:
                            print(f"⚠️ Error getting final state: {e}", flush=True)
                            final_messages = []
                            final_response = ""
                finally:
                    # Additional cleanup: Clear stream references
                    if stream_iter is not None:
                        stream_iter = None
                    if stream is not None:
                        stream = None
            
            # Ensure we have a response - if empty, provide a fallback message
            # Convert final_response to string if it's not already (handles list content)
            if not isinstance(final_response, str):
                if isinstance(final_response, list):
                    # Extract text from list of content blocks
                    text_parts = []
                    for item in final_response:
                        if isinstance(item, dict) and 'text' in item:
                            text_parts.append(item['text'])
                        elif isinstance(item, str):
                            text_parts.append(item)
                    final_response = ' '.join(text_parts) if text_parts else ""
                else:
                    final_response = str(final_response) if final_response else ""
            
            if not final_response or (isinstance(final_response, str) and final_response.strip() == ""):
                print(f"⚠️ Final response is empty, using fallback message", flush=True)
                sys.stdout.flush()
                final_response = "I'm processing your request. Please wait a moment and try again if you don't see a response."
            
            # Extract all tool calls from final state messages
            # For native Gemini SDK, tool_calls_info is already populated, so skip if empty
            if not final_messages:
                final_messages = []  # Ensure it's initialized
            
            # Process messages and then clear to free memory
            try:
                for msg in final_messages:
                    # Track tool calls (AIMessage with tool_calls)
                    if hasattr(msg, 'tool_calls') and msg.tool_calls:
                        for tc in msg.tool_calls:
                            tool_id = getattr(tc, 'id', getattr(tc, 'tool_call_id', str(uuid.uuid4())))
                            tool_name = getattr(tc, 'name', getattr(tc, 'functionName', 'unknown'))
                            args = getattr(tc, 'args', getattr(tc, 'arguments', {}))
                            
                            # Check if we already have this tool call
                            existing = next((t for t in tool_calls_info if t.tool_id == tool_id), None)
                            if not existing:
                                tool_calls_info.append(ToolCallInfo(
                                    tool_name=tool_name,
                                    tool_id=tool_id,
                                    arguments=args if isinstance(args, dict) else {}
                                ))
                    
                    # Track tool results (ToolMessage)
                    if hasattr(msg, 'tool_call_id') and hasattr(msg, 'content'):
                        tool_call_id = msg.tool_call_id
                        # Find matching tool call and update it
                        for tc_info in tool_calls_info:
                            if tc_info.tool_id == tool_call_id:
                                tc_info.result = msg.content
                                tc_info.status = "success"
                                # Try to extract duration if available
                                if hasattr(msg, 'additional_kwargs') and 'duration' in msg.additional_kwargs:
                                    tc_info.duration_ms = msg.additional_kwargs['duration'] * 1000
                                break
            finally:
                # Clear final_messages to free memory after processing
                if 'final_messages' in locals() and final_messages is not None:
                    final_messages.clear()
                    final_messages = None
            
            # Calculate duration using captured start_time
            # Use time_module to avoid scoping conflicts
            duration_ms = (time_module.time() - process_start_time) * 1000
            
            # Track the interaction
            monitor.track_interaction(
                request_id=request_id,
                user_id=user_id,
                user_name=user_name,
                provider=_agent_graph_provider,
                model=_agent_graph_model,
                user_prompt=prompt,
                agent_response=final_response if isinstance(final_response, str) else str(final_response),
                tool_calls=tool_calls_info,
                status=AgentInteractionStatus.SUCCESS,
                duration_ms=duration_ms
            )
            
            # Cleanup: Clear large objects to help with memory management
            # Note: tool_calls_info is used by monitor, so don't clear it yet
            # But we can clear other temporary variables after they're used
            # (transfer_marker is already used above, so it can be cleared)
            
            # If transfer marker was found, return it (for WebRTC transfer detection)
            # Otherwise return the final response
            if include_metadata:
                return {
                    "response": final_response,
                    "transfer_marker": transfer_marker,
                    "provider_used": _agent_graph_provider,
                    "model_used": _agent_graph_model,
                }
            if transfer_marker:
                return transfer_marker
            return final_response
        
        # For all providers (including Gemini), use async directly with eventlet
        # Eventlet handles async I/O efficiently without thread conflicts when using await
        # No need for separate threads - eventlet's greenlets handle concurrency
        timeout_seconds = execution_timeout
        print(f"⏱️ Starting agent execution with {timeout_seconds}-second timeout (provider: {current_provider})...", flush=True)
        sys.stdout.flush()
        print(f"🔄 Using async/await directly - eventlet will handle I/O efficiently", flush=True)
        sys.stdout.flush()
        result = await asyncio.wait_for(process_stream(), timeout=timeout_seconds)
        print(f"✅ Agent execution completed successfully", flush=True)
        sys.stdout.flush()
        return result
    except asyncio.TimeoutError:
        # Track timeout
        duration_ms = (time.time() - start_time) * 1000
        current_provider = _agent_graph_provider
        timeout_seconds = 25.0 if current_provider == "gemini" else 20.0
        print(f"⏱️ Agent execution timed out after {timeout_seconds} seconds")
        print(f"⏱️ Provider: {current_provider}, This likely means Gemini LLM is hanging during tool calling or response generation")
        monitor.track_interaction(
            request_id=request_id,
            user_id=user_id,
            user_name=user_name,
            provider=_agent_graph_provider,
            model=_agent_graph_model,
            user_prompt=prompt,
            agent_response="AGENT_TIMEOUT: Taking too long to process. Please try a simpler request.",
            tool_calls=[],
            status=AgentInteractionStatus.TIMEOUT,
            duration_ms=duration_ms,
            error=f"Agent execution timed out after {timeout_seconds} seconds (provider: {current_provider})"
        )
        # Return a special marker for timeout
        return "AGENT_TIMEOUT: Taking too long to process. Please try a simpler request."
    except Exception as e:
        import traceback
        error_str = str(e)
        error_traceback = traceback.format_exc()
        print(f"❌ Error in agent execution: {e}")
        print(f"❌ Full traceback:\n{error_traceback}", flush=True)
        sys.stdout.flush()
        
        # Track error
        duration_ms = (time.time() - start_time) * 1000
        monitor.track_interaction(
            request_id=request_id,
            user_id=user_id,
            user_name=user_name,
            provider=_agent_graph_provider,
            model=_agent_graph_model,
            user_prompt=prompt,
            agent_response=None,
            tool_calls=[],
            status=AgentInteractionStatus.FAILED,
            duration_ms=duration_ms,
            error=error_str
        )
        
        # Check if it's a model 404 error - if so, clear cache and retry once
        # Check for various 404 error patterns
        is_model_404 = (
            "MODEL_404_ERROR" in error_str or 
            ("404" in error_str and ("model" in error_str.lower() or "not_found_error" in error_str)) or
            ("not_found_error" in error_str and "model:" in error_str.lower())
        )
        if is_model_404:
            print("🔄 Model 404 error detected, clearing cache and retrying...")
            print(f"🔄 Error details: {error_str[:200]}")
            
            # Extract the failed model name from error
            failed_model = None
            if "model:" in error_str.lower():
                # Try to extract model name from error message
                import re
                model_match = re.search(r"model:\s*([^\s,}']+)", error_str, re.IGNORECASE)
                if model_match:
                    failed_model = model_match.group(1).strip()
                    print(f"🔄 Detected failed model: {failed_model}")
            
            _agent_graph_cache = None
            _agent_graph_model = None
            
            # Force a different model by setting env var to a known working model
            # Skip the failed model and try others (using actual model IDs from API)
            original_model = os.getenv("ANTHROPIC_MODEL")
            fallback_models = [
                "claude-sonnet-4-20250514",  # Claude Sonnet 4 (confirmed available)
                "claude-3-7-sonnet-20250219",  # Claude Sonnet 3.7 (confirmed available)
                "claude-3-opus-20240229",  # Claude Opus 3 (confirmed available)
                "claude-sonnet-4-5-20250929",  # Claude Sonnet 4.5 (newer)
            ]
            
            # Find a model that's different from the failed one and the original
            next_model = None
            for model in fallback_models:
                if model != failed_model and model != original_model:
                    next_model = model
                    break
            
            if not next_model:
                # Last resort - use the first available model that's different
                next_model = "claude-sonnet-4-20250514"  # Most reliable
            
            print(f"🔄 Temporarily setting ANTHROPIC_MODEL to {next_model} to try different model...")
            print(f"🔄 Original model was: {original_model}, failed model was: {failed_model}")
            os.environ["ANTHROPIC_MODEL"] = next_model
            
            # Retry once with fresh graph
            try:
                print(f"🔄 Retrying with fresh agent graph (will try model: {next_model})...")
                print(f"🔄 Current ANTHROPIC_MODEL env var: {os.getenv('ANTHROPIC_MODEL')}")
                agent_graph = await _get_agent_graph()
                stream = agent_graph.astream(input=input_state, stream_mode="values", config=config)
                
                async def process_stream_retry():
                    transfer_marker = None
                    async for state in stream:
                        if "messages" in state:
                            for msg in state["messages"]:
                                if hasattr(msg, 'content') and isinstance(msg.content, str):
                                    if 'TRANSFER_INITIATED:' in msg.content:
                                        transfer_marker = msg.content
                    final_state = agent_graph.get_state(config=config)
                    last_message = final_state.values.get("messages")[-1]
                    final_response = getattr(last_message, 'content', "")
                    if include_metadata:
                        return {
                            "response": final_response,
                            "transfer_marker": transfer_marker,
                            "provider_used": _agent_graph_provider,
                            "model_used": _agent_graph_model,
                        }
                    if transfer_marker:
                        return transfer_marker
                    return final_response
                
                result = await asyncio.wait_for(process_stream_retry(), timeout=20.0)
                # Restore original env var (after successful retry)
                if original_model:
                    os.environ["ANTHROPIC_MODEL"] = original_model
                elif "ANTHROPIC_MODEL" in os.environ:
                    os.environ.pop("ANTHROPIC_MODEL", None)
                return result
            except Exception as retry_error:
                # Restore original env var even if retry fails
                if original_model:
                    os.environ["ANTHROPIC_MODEL"] = original_model
                elif "ANTHROPIC_MODEL" in os.environ:
                    os.environ.pop("ANTHROPIC_MODEL", None)
                print(f"❌ Retry also failed: {retry_error}")
                retry_error_str = str(retry_error)
                if "404" in retry_error_str or "not_found_error" in retry_error_str:
                    return f"AGENT_ERROR:model_not_found:All Anthropic models returned 404. Please verify your API key has access to Anthropic models. Error: {retry_error_str[:150]}"
                return f"AGENT_ERROR:model_not_found:Unable to find a working Anthropic model. Please check your API key and model configuration."
        
        # Return special markers for specific errors so they can be detected upstream
        if "tool_call" in error_str.lower():
            return f"AGENT_ERROR:tool_call_incomplete:{error_str[:100]}"
        elif "BrokenResourceError" in error_str:
            return "AGENT_ERROR:broken_resource:Connection issue with database"
        else:
            return f"AGENT_ERROR:general:{error_str[:100]}"


# ElevenLabs Voice API Routes
@convonet_todo_bp.route('/api/voice/clone', methods=['POST'])
def clone_voice():
    """Clone a voice from audio samples"""
    try:
        from convonet.elevenlabs_service import get_elevenlabs_service
        from convonet.voice_preferences import get_voice_preferences
        import base64
        
        data = request.get_json(silent=True) or {}
        user_id = data.get('user_id') or request.headers.get('X-User-ID')
        voice_name = data.get('voice_name', 'My Voice')
        audio_samples_b64 = data.get('audio_samples', [])
        
        if not audio_samples_b64:
            return jsonify({
                'success': False,
                'error': 'No audio samples provided'
            }), 400
        
        # Decode base64 audio samples
        audio_samples = []
        for sample_b64 in audio_samples_b64:
            try:
                audio_bytes = base64.b64decode(sample_b64)
                audio_samples.append(audio_bytes)
            except Exception as e:
                logger.error(f"Error decoding audio sample: {e}")
                return jsonify({
                    'success': False,
                    'error': f'Invalid audio sample format: {e}'
                }), 400
        
        # Clone voice
        elevenlabs = get_elevenlabs_service()
        if not elevenlabs.is_available():
            return jsonify({
                'success': False,
                'error': 'ElevenLabs service not available. Check ELEVENLABS_API_KEY.'
            }), 503
        
        voice_id = elevenlabs.clone_voice(
            audio_samples=audio_samples,
            voice_name=voice_name,
            description=f"Cloned voice for user {user_id}"
        )
        
        if not voice_id:
            return jsonify({
                'success': False,
                'error': 'Voice cloning failed'
            }), 500
        
        # Save voice preference
        if user_id:
            voice_prefs = get_voice_preferences()
            voice_prefs.update_user_preferences(user_id, {
                'voice_id': voice_id,
                'voice_name': voice_name
            })
        
        return jsonify({
            'success': True,
            'voice_id': voice_id,
            'voice_name': voice_name,
            'message': 'Voice cloned successfully'
        })
        
    except Exception as e:
        logger.error(f"Error cloning voice: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@convonet_todo_bp.route('/api/voice/preferences', methods=['GET', 'POST'])
def voice_preferences():
    """Get or update user voice preferences"""
    try:
        from convonet.voice_preferences import get_voice_preferences
        
        user_id = request.args.get('user_id') or request.headers.get('X-User-ID')
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'user_id required'
            }), 400
        
        voice_prefs = get_voice_preferences()
        
        if request.method == 'GET':
            prefs = voice_prefs.get_user_preferences(user_id)
            return jsonify({
                'success': True,
                'preferences': prefs
            })
        else:  # POST
            data = request.get_json(silent=True) or {}
            success = voice_prefs.update_user_preferences(user_id, data)
            return jsonify({
                'success': success,
                'preferences': voice_prefs.get_user_preferences(user_id)
            })
            
    except Exception as e:
        logger.error(f"Error with voice preferences: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@convonet_todo_bp.route('/api/voice/list', methods=['GET'])
def list_voices():
    """List available ElevenLabs voices"""
    try:
        from convonet.elevenlabs_service import get_elevenlabs_service
        
        elevenlabs = get_elevenlabs_service()
        if not elevenlabs.is_available():
            return jsonify({
                'success': False,
                'error': 'ElevenLabs service not available'
            }), 503
        
        voices = elevenlabs.list_voices()
        return jsonify({
            'success': True,
            'voices': voices
        })
        
    except Exception as e:
        logger.error(f"Error listing voices: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@convonet_todo_bp.route('/run_agent', methods=['POST'])
def run_agent():
    data = request.get_json(silent=True) or {}
    prompt = data.get('prompt')
    if not prompt:
        return jsonify({"error": "Missing 'prompt' in JSON body"}), 400

    try:
        result = asyncio.run(_run_agent_async(prompt))
        return jsonify({"result": result})
    except Exception as e:
        # Log the full error for debugging
        print(f"Error in /run_agent: {e}")
        return jsonify({"error": str(e)}), 500


# WebSocket event handlers for Flask-SocketIO
@convonet_todo_bp.route('/ws')
def websocket_route():
    """WebSocket route for Twilio Media Streams."""
    return "WebSocket endpoint available via Socket.IO", 200


def register_socketio_events(socketio):
    """Register Socket.IO events for Convonet WebSocket functionality."""
    
    @socketio.on('connect', namespace='/convonet_todo')
    def handle_connect():
        """Handle WebSocket connection from Twilio."""
        logger.info(f"WebSocket connection established from {request.remote_addr}")
        emit('status', {'msg': 'Connected to Convonet WebSocket'})
    
    @socketio.on('disconnect', namespace='/convonet_todo')
    def handle_disconnect():
        """Handle WebSocket disconnection."""
        logger.info(f"WebSocket connection closed from {request.remote_addr}")
    
    @socketio.on('media', namespace='/convonet_todo')
    def handle_media(data):
        """Handle media data from Twilio."""
        try:
            # Process media data from Twilio Media Streams
            logger.info(f"Received media data: {len(data)} bytes")
            
            # Here you would integrate with the twilio_handler logic
            # For now, just acknowledge receipt
            emit('ack', {'msg': 'Media received'})
            
        except Exception as e:
            logger.error(f"Error handling media: {str(e)}", exc_info=True)
            emit('error', {'msg': str(e)})
    
    @socketio.on('start', namespace='/convonet_todo')
    def handle_start(data):
        """Handle start event from Twilio."""
        try:
            logger.info(f"Start event received: {data}")
            
            # Initialize conversation with Convonet agent
            emit('started', {'msg': 'Conversation started with Convonet agent'})
            
        except Exception as e:
            logger.error(f"Error handling start: {str(e)}", exc_info=True)
            emit('error', {'msg': str(e)})
    
    @socketio.on('stop', namespace='/convonet_todo')
    def handle_stop(data):
        """Handle stop event from Twilio."""
        try:
            logger.info(f"Stop event received: {data}")
            
            # Clean up conversation
            emit('stopped', {'msg': 'Conversation ended'})
            
        except Exception as e:
            logger.error(f"Error handling stop: {str(e)}", exc_info=True)
            emit('error', {'msg': str(e)})


# LLM Provider Management API
@convonet_todo_bp.route('/api/llm-providers', methods=['GET'])
def get_llm_providers():
    """Get list of available LLM providers."""
    try:
        provider_manager = get_llm_provider_manager()
        providers = provider_manager.get_available_providers()
        return jsonify({
            'success': True,
            'providers': providers
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@convonet_todo_bp.route('/api/llm-provider', methods=['GET'])
def get_user_llm_provider():
    """Get user's current LLM provider preference."""
    try:
        user_id = request.args.get('user_id', 'default')  # Default to 'default' if not provided
        
        # Get from Redis
        try:
            provider = redis_manager.get(f"user:{user_id}:llm_provider")
        except Exception as redis_error:
            print(f"⚠️ Redis error getting provider: {redis_error}")
            provider = None
        
        if not provider:
            # Default to Claude
            provider = "claude"
        
        provider_manager = get_llm_provider_manager()
        return jsonify({
            'success': True,
            'provider': provider,
            'user_id': user_id,
            'available': provider_manager.is_provider_available(provider)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@convonet_todo_bp.route('/api/llm-provider', methods=['POST'])
def set_user_llm_provider():
    """Set user's LLM provider preference."""
    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({
                'success': False,
                'error': 'Invalid JSON body'
            }), 400

        user_id = data.get('user_id')
        provider = data.get('provider', 'claude').lower()
        
        if not user_id:
            return jsonify({
                'success': False,
                'error': 'user_id required in request body'
            }), 400
        
        if provider not in ['claude', 'gemini', 'openai']:
            return jsonify({
                'success': False,
                'error': 'provider must be one of: claude, gemini, openai'
            }), 400
        
        # Validate provider is available
        provider_manager = get_llm_provider_manager()
        if not provider_manager.is_provider_available(provider):
            return jsonify({
                'success': False,
                'error': f'Provider {provider} is not available. Please configure the API key.'
            }), 400

        # Store in Redis (expires in 30 days) - best-effort only
        try:
            redis_manager.set(
                f"user:{user_id}:llm_provider",
                provider,
                expire=30 * 24 * 60 * 60  # 30 days
            )
        except Exception as redis_error:
            print(f"⚠️ Failed to store LLM provider in Redis: {redis_error}")
        
        # Clear agent graph cache to force reinitialization with new provider (best-effort)
        try:
            global _agent_graph_cache, _agent_graph_provider
            if _agent_graph_provider != provider:
                _agent_graph_cache = None
                _agent_graph_provider = None
                print(f"🔄 Cleared agent graph cache due to provider change to {provider}")
        except Exception as cache_error:
            print(f"⚠️ Failed to clear agent graph cache: {cache_error}")
        
        return jsonify({
            'success': True,
            'provider': provider,
            'message': f'LLM provider set to {provider}'
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Route to check Deepgram status
@convonet_todo_bp.route('/webrtc/pending-response', methods=['GET'])
def get_pending_response():
    """HTTP endpoint for client to fetch pending responses (avoids WebSocket large payload issues)"""
    try:
        user_id = request.args.get('user_id')
        if not user_id:
            return jsonify({'success': False, 'error': 'user_id required'}), 400
        
        from .redis_manager import redis_manager
        import json
        
        if not redis_manager.is_available():
            return jsonify({'success': False, 'error': 'Redis unavailable'}), 503
        
        redis_key = f"pending_response:{user_id}"
        pending_data = redis_manager.redis_client.get(redis_key)
        
        if not pending_data:
            return jsonify({'success': False, 'pending': False}), 200
        
        pending_response = json.loads(pending_data)
        
        # Delete from Redis after fetching (one-time delivery)
        redis_manager.redis_client.delete(redis_key)
        
        logger.info(f"✅ Pending response fetched via HTTP for user {user_id}")
        
        return jsonify({
            'success': True,
            'pending': True,
            'text': pending_response.get('text'),
            'audio': pending_response.get('audio'),
            'created_at': pending_response.get('created_at')
        }), 200
        
    except Exception as e:
        logger.error(f"Error fetching pending response: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@convonet_todo_bp.route('/webrtc/whisper-status')
def whisper_status():
    """Check Deepgram status"""
    try:
        from deepgram_webrtc_integration import get_deepgram_webrtc_info
        info = get_deepgram_webrtc_info()
        return jsonify({
            'success': True,
            'transcriber': info.get('transcriber', 'deepgram'),
            'model': info.get('model', 'nova-2'),
            'accuracy': info.get('accuracy', '95%+'),
            'cost_per_minute': info.get('cost_per_minute', 0.0043),
            'privacy': info.get('privacy', 'processed on Deepgram servers'),
            'latency': info.get('latency', '200-500ms'),
            'webrtc_ready': info.get('webrtc_ready', True),
            'production_ready': info.get('production_ready', True),
            'api_key_configured': bool(os.getenv('DEEPGRAM_API_KEY')),
            'streaming_optimized': info.get('streaming_optimized', True),
            'webRTC_native': info.get('webRTC_native', True)
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Failed to check Deepgram status',
            'transcriber': 'deepgram',
            'status': 'error'
        })
