"""
Call Center Routes - ACD and SIP Phone Handling
"""

from flask import render_template, request, jsonify, session
from datetime import datetime
from . import call_center_bp
from .models import Agent, Call, AgentActivity, AgentState, CallState
from extensions import db
import uuid
import json

try:
    from convonet.redis_manager import redis_manager
except ImportError:
    redis_manager = None

@call_center_bp.route('/')
def index():
    """Render the call center UI"""
    # Import config to pass to template
    try:
        from .config import SIP_CONFIG
    except ImportError:
        # Fallback if config not created yet
        SIP_CONFIG = {
            'domain': 'sip.example.com',
            'wss_port': 7443
        }
    
    return render_template('call_center.html', sip_config=SIP_CONFIG)

@call_center_bp.route('/api/agent/login', methods=['POST'])
def agent_login():
    """Agent login - set agent to logged in state"""
    data = request.json
    agent_id = data.get('agent_id')
    sip_username = data.get('sip_username')
    sip_password = data.get('sip_password')
    sip_domain = data.get('sip_domain', 'sip.example.com')
    
    # Find or create agent
    agent = Agent.query.filter_by(agent_id=agent_id).first()
    
    if not agent:
        agent = Agent(
            agent_id=agent_id,
            name=data.get('name', agent_id),
            email=data.get('email'),
            sip_extension=data.get('sip_extension', agent_id),
            sip_username=sip_username,
            sip_password=sip_password,
            sip_domain=sip_domain
        )
        db.session.add(agent)
    
    # Update agent state
    old_state = agent.state
    agent.state = AgentState.LOGGED_IN.value
    agent.state_changed_at = datetime.utcnow()
    agent.last_login = datetime.utcnow()
    
    # Log activity
    activity = AgentActivity(
        agent_id=agent.id,
        activity_type='login',
        from_state=old_state,
        to_state=AgentState.LOGGED_IN.value,
        metadata={'sip_domain': sip_domain}
    )
    db.session.add(activity)
    db.session.commit()
    
    # Store in session
    session['agent_id'] = agent.id
    session['agent_username'] = agent.agent_id
    
    return jsonify({
        'success': True,
        'agent': {
            'id': agent.id,
            'agent_id': agent.agent_id,
            'name': agent.name,
            'state': agent.state,
            'sip_username': agent.sip_username,
            'sip_domain': agent.sip_domain,
            'sip_extension': agent.sip_extension
        }
    })

@call_center_bp.route('/api/agent/logout', methods=['POST'])
def agent_logout():
    """Agent logout - set agent to logged out state"""
    agent_id = session.get('agent_id')
    
    if not agent_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    agent = Agent.query.get(agent_id)
    if not agent:
        return jsonify({'success': False, 'error': 'Agent not found'}), 404
    
    old_state = agent.state
    agent.state = AgentState.LOGGED_OUT.value
    agent.state_changed_at = datetime.utcnow()
    
    # Log activity
    activity = AgentActivity(
        agent_id=agent.id,
        activity_type='logout',
        from_state=old_state,
        to_state=AgentState.LOGGED_OUT.value
    )
    db.session.add(activity)
    db.session.commit()
    
    # Clear session
    session.pop('agent_id', None)
    session.pop('agent_username', None)
    
    return jsonify({'success': True})

@call_center_bp.route('/api/agent/ready', methods=['POST'])
def agent_ready():
    """Set agent to ready state - available for calls"""
    agent_id = session.get('agent_id')
    
    if not agent_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    agent = Agent.query.get(agent_id)
    if not agent:
        return jsonify({'success': False, 'error': 'Agent not found'}), 404
    
    old_state = agent.state
    agent.state = AgentState.READY.value
    agent.state_changed_at = datetime.utcnow()
    
    # Log activity
    activity = AgentActivity(
        agent_id=agent.id,
        activity_type='state_change',
        from_state=old_state,
        to_state=AgentState.READY.value
    )
    db.session.add(activity)
    db.session.commit()
    
    return jsonify({'success': True, 'state': agent.state})

@call_center_bp.route('/api/agent/not-ready', methods=['POST'])
def agent_not_ready():
    """Set agent to not ready state - unavailable for calls"""
    agent_id = session.get('agent_id')
    data = request.json or {}
    reason = data.get('reason', 'Break')
    
    if not agent_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    agent = Agent.query.get(agent_id)
    if not agent:
        return jsonify({'success': False, 'error': 'Agent not found'}), 404
    
    old_state = agent.state
    agent.state = AgentState.NOT_READY.value
    agent.state_changed_at = datetime.utcnow()
    
    # Log activity
    activity = AgentActivity(
        agent_id=agent.id,
        activity_type='state_change',
        from_state=old_state,
        to_state=AgentState.NOT_READY.value,
        metadata={'reason': reason}
    )
    db.session.add(activity)
    db.session.commit()
    
    return jsonify({'success': True, 'state': agent.state})

@call_center_bp.route('/api/call/ringing', methods=['POST'])
def call_ringing():
    """Handle incoming call ringing event"""
    data = request.json
    agent_id = session.get('agent_id')
    
    if not agent_id:
        return jsonify({'success': False, 'error': 'Not logged in'}), 401
    
    # Create call record
    call = Call(
        call_id=data.get('call_id', str(uuid.uuid4())),
        agent_id=agent_id,
        caller_number=data.get('caller_number'),
        caller_name=data.get('caller_name'),
        called_number=data.get('called_number'),
        customer_id=data.get('customer_id'),
        customer_data=data.get('customer_data', {}),
        state=CallState.RINGING.value,
        direction=data.get('direction', 'inbound'),
        started_at=datetime.utcnow()
    )
    db.session.add(call)
    
    # Update agent state
    agent = Agent.query.get(agent_id)
    agent.state = AgentState.ON_CALL.value
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'call': call.to_dict()
    })

@call_center_bp.route('/api/call/answer', methods=['POST'])
def call_answer():
    """Handle call answer request"""
    data = request.json
    call_id = data.get('call_id')
    
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return jsonify({'success': False, 'error': 'Call not found'}), 404
    
    call.state = CallState.CONNECTED.value
    call.answered_at = datetime.utcnow()
    db.session.commit()
    
    return jsonify({
        'success': True,
        'call': call.to_dict()
    })

@call_center_bp.route('/api/call/drop', methods=['POST'])
def call_drop():
    """Handle call drop/hangup request"""
    data = request.json
    call_id = data.get('call_id')
    
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return jsonify({'success': False, 'error': 'Call not found'}), 404
    
    call.state = CallState.ENDED.value
    call.ended_at = datetime.utcnow()
    
    # Calculate duration
    if call.answered_at:
        duration = (call.ended_at - call.answered_at).total_seconds()
        call.duration = int(duration)
    
    # Update agent state back to ready
    agent = Agent.query.get(call.agent_id)
    if agent:
        agent.state = AgentState.READY.value
    
    db.session.commit()
    
    return jsonify({
        'success': True,
        'call': call.to_dict()
    })

@call_center_bp.route('/api/call/transfer', methods=['POST'])
def call_transfer():
    """Handle call transfer request"""
    data = request.json
    call_id = data.get('call_id')
    transfer_to = data.get('transfer_to')
    transfer_type = data.get('transfer_type', 'blind')  # blind or attended
    
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return jsonify({'success': False, 'error': 'Call not found'}), 404
    
    call.state = CallState.TRANSFERRING.value
    db.session.commit()
    
    return jsonify({
        'success': True,
        'call': call.to_dict(),
        'transfer_to': transfer_to,
        'transfer_type': transfer_type
    })

@call_center_bp.route('/api/call/hold', methods=['POST'])
def call_hold():
    """Hold a call"""
    data = request.json
    call_id = data.get('call_id')
    
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return jsonify({'success': False, 'error': 'Call not found'}), 404
    
    call.state = CallState.HELD.value
    db.session.commit()
    
    return jsonify({
        'success': True,
        'call': call.to_dict()
    })

@call_center_bp.route('/api/call/unhold', methods=['POST'])
def call_unhold():
    """Unhold a call"""
    data = request.json
    call_id = data.get('call_id')
    
    call = Call.query.filter_by(call_id=call_id).first()
    if not call:
        return jsonify({'success': False, 'error': 'Call not found'}), 404
    
    call.state = CallState.CONNECTED.value
    db.session.commit()
    
    return jsonify({
        'success': True,
        'call': call.to_dict()
    })

@call_center_bp.route('/api/agent/status', methods=['GET'])
def agent_status():
    """Get current agent status"""
    agent_id = session.get('agent_id')
    
    if not agent_id:
        return jsonify({'logged_in': False})
    
    agent = Agent.query.get(agent_id)
    if not agent:
        return jsonify({'logged_in': False})
    
    # Get active calls
    active_calls = Call.query.filter_by(
        agent_id=agent.id
    ).filter(
        Call.state.in_([CallState.RINGING.value, CallState.CONNECTED.value, CallState.HELD.value])
    ).all()
    
    return jsonify({
        'logged_in': True,
        'agent': {
            'id': agent.id,
            'agent_id': agent.agent_id,
            'name': agent.name,
            'state': agent.state,
            'sip_username': agent.sip_username,
            'sip_domain': agent.sip_domain,
            'sip_extension': agent.sip_extension
        },
        'active_calls': [call.to_dict() for call in active_calls]
    })

@call_center_bp.route('/api/customer/<customer_id>', methods=['GET'])
def get_customer_by_id(customer_id):
    """
    Get customer/transfer context by customer_id (used by showCustomerPopup).
    Same logic as get_customer_data - looks up Redis cache by extension + call_sid/call_id.
    """
    extension = request.args.get('extension')
    call_sid = request.args.get('call_sid')
    call_id = request.args.get('call_id')
    return _fetch_customer_profile(extension=extension, call_sid=call_sid, call_id=call_id, customer_id=customer_id)


def _fetch_customer_profile(extension=None, call_sid=None, call_id=None, customer_id=None):
    """Shared logic to fetch customer profile from Redis cache."""
    agent_extension = None
    if not extension:
        agent_id = session.get('agent_id')
        if agent_id:
            agent = Agent.query.get(agent_id)
            if agent:
                agent_extension = agent.sip_extension or agent.agent_id
                extension = agent_extension

    profile = None
    if redis_manager and redis_manager.is_available():
        try:
            keys_to_try = []
            if extension and call_sid:
                keys_to_try.append(f"callcenter:customer:{extension}:{call_sid}")
            if extension and call_id:
                keys_to_try.append(f"callcenter:customer:{extension}:{call_id}")
            if extension:
                keys_to_try.append(f"callcenter:customer:{extension}")
            if customer_id:
                keys_to_try.append(f"callcenter:customer:{customer_id}")
            if agent_extension and agent_extension != customer_id:
                keys_to_try.append(f"callcenter:customer:{agent_extension}")

            hit_key = None
            for cache_key in keys_to_try:
                cached = redis_manager.redis_client.get(cache_key)
                if cached:
                    profile = json.loads(cached)
                    hit_key = cache_key
                    break
            if hit_key:
                sc = (profile or {}).get('suitecrm_context') or {}
                print(f"📋 Call center profile HIT: key={hit_key}, has_suitecrm_context={bool(sc)}, keys={list(sc.keys()) if sc else []}")
            elif extension or customer_id:
                print(f"📋 Call center profile MISS: tried extension={extension}, call_sid={call_sid}, call_id={call_id}, customer_id={customer_id}")
        except Exception as e:
            print(f"⚠️ Failed to read customer cache: {e}")

    if profile:
        # Optionally enrich with SuiteCRM data by phone (if we have phone but no suitecrm_context)
        phone = profile.get('phone') or customer_id
        if phone and (not profile.get('suitecrm_context') or not profile.get('suitecrm_context').get('patient_id')):
            _enrich_profile_from_suitecrm(profile, phone)
        return jsonify(profile)
    
    # No cached profile - try SuiteCRM lookup by caller number (customer_id)
    fallback = {
        'customer_id': customer_id or 'unknown',
        'name': 'Unknown Caller',
        'email': 'N/A',
        'phone': customer_id or 'N/A',
        'account_status': 'N/A',
        'tier': 'N/A',
        'last_contact': 'N/A',
        'open_tickets': 0,
        'lifetime_value': 'N/A',
        'notes': 'No context from voice assistant. Call may not be from transfer.',
        'conversation_history': [],
        'activities': []
    }
    if customer_id and len(str(customer_id).replace('+', '').replace('-', '').replace(' ', '')) >= 10:
        _enrich_profile_from_suitecrm(fallback, customer_id)
    return jsonify(fallback)


def _enrich_profile_from_suitecrm(profile, phone):
    """Look up SuiteCRM contact by phone and add patient_id to profile."""
    try:
        from convonet.services.suitecrm_client import SuiteCRMClient
        client = SuiteCRMClient()
        if client.authenticate():
            clean_phone = str(phone).replace('+', '').replace('-', '').replace(' ', '')
            if len(clean_phone) >= 10:
                result = client.search_patient(clean_phone)
                if result.get('found') and result.get('patient_id'):
                    if not profile.get('suitecrm_context'):
                        profile['suitecrm_context'] = {}
                    profile['suitecrm_context']['patient_id'] = result['patient_id']
                    profile['suitecrm_context']['from_lookup'] = True
                    attrs = result.get('attributes', {})
                    if attrs and not profile.get('name') or profile.get('name') == 'Unknown Caller':
                        profile['name'] = f"{attrs.get('first_name', '')} {attrs.get('last_name', '')}".strip() or profile.get('name')
    except Exception:
        pass  # SuiteCRM lookup is best-effort


@call_center_bp.route('/api/customer/data', methods=['GET'])
def get_customer_data():
    """
    Get customer data for popup (prefer real data cached by the voice assistant).
    Accepts: extension, call_sid, call_id, customer_id (all optional query params).
    """
    extension = request.args.get('extension')
    call_sid = request.args.get('call_sid')
    call_id = request.args.get('call_id')
    customer_id = request.args.get('customer_id')
    return _fetch_customer_profile(extension=extension, call_sid=call_sid, call_id=call_id, customer_id=customer_id)

