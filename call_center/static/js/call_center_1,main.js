/**
 * Call Center Agent Dashboard - SIP Phone Integration
 * Supports ACD features and SIP-based call handling
 */

class CallCenterAgent {
    constructor() {
        this.agent = null;
        this.sipUser = null;
        this.currentCall = null;
        this.currentSession = null;
        this.pendingDialNumber = null;
        this.localStream = null;
        this.audioAccessDenied = false;
        this.answerInProgress = false;
        this.iceServers = [
            { urls: 'stun:136.115.41.45:3478' },
            {
                urls: [
                    'turn:136.115.41.45:3478?transport=udp'
                ],
                username: 'agent',
                credential: 'P@ssw0rd'
            }
        ];
        this.rtcConfiguration = {
            iceServers: this.iceServers,
            iceTransportPolicy: 'all'
        };
        this.sessionDescriptionHandlerFactoryOptions = {
            peerConnectionOptions: {
                rtcConfiguration: this.rtcConfiguration,
                disableTrickleIce: true,
                iceGatheringTimeout: 8000
            },
            peerConnectionConfiguration: this.rtcConfiguration,
            disableTrickleIce: true,
            iceGatheringTimeout: 8000
        };
        this.statusTimer = null;
        this.statusStartTime = null;
        this.callDurationTimer = null;
        this.callStartTime = null;
        this.activeCallSessionId = null;
        this.activeCallIdentity = null;
        
        this.init();
    }
    
    init() {
        // Initialize UI elements
        this.initElements();
        this.attachEventListeners();
        this.checkLoginStatus();
    }
    
    initElements() {
        // Screens
        this.loginScreen = document.getElementById('loginScreen');
        this.dashboardScreen = document.getElementById('dashboardScreen');
        
        // Login form
        this.loginForm = document.getElementById('loginForm');
        
        // Agent info displays
        this.agentNameDisplay = document.getElementById('agentNameDisplay');
        this.agentExtension = document.getElementById('agentExtension');
        this.currentStatus = document.getElementById('currentStatus');
        this.statusTimerDisplay = document.getElementById('statusTimer');
        this.sipStatus = document.getElementById('sipStatus');
        
        // Buttons
        this.logoutBtn = document.getElementById('logoutBtn');
        this.readyBtn = document.getElementById('readyBtn');
        this.notReadyBtn = document.getElementById('notReadyBtn');
        this.answerBtn = document.getElementById('answerBtn');
        this.holdBtn = document.getElementById('holdBtn');
        this.unholdBtn = document.getElementById('unholdBtn');
        this.hangupBtn = document.getElementById('hangupBtn');
        this.transferBtn = document.getElementById('transferBtn');
        this.dialCallBtn = document.getElementById('dialCallBtn');
        this.dialClearBtn = document.getElementById('dialClearBtn');
        
        // Call info
        this.callInfo = document.getElementById('callInfo');
        
        // Dialpad
        this.dialInput = document.getElementById('dialInput');
        this.dialButtons = document.querySelectorAll('.dial-btn');
        
        // Transfer panel
        this.transferPanel = document.getElementById('transferPanel');
        this.transferNumber = document.getElementById('transferNumber');
        this.blindTransferBtn = document.getElementById('blindTransferBtn');
        this.attendedTransferBtn = document.getElementById('attendedTransferBtn');
        this.cancelTransferBtn = document.getElementById('cancelTransferBtn');
        
        // Customer popup (with Accept Call button)
        this.customerPopup = document.getElementById('customerPopup');
        this.customerData = document.getElementById('customerData');
        this.closeCustomerPopup = document.getElementById('closeCustomerPopup');
        this.acceptCallFromPopup = document.getElementById('acceptCallFromPopup');
        this.customerPopupContent = this.customerPopup ? this.customerPopup.querySelector('.modal-content') : null;
        
        // Customer info window (read-only, no Accept button)
        this.customerInfoWindow = document.getElementById('customerInfoWindow');
        this.customerInfoData = document.getElementById('customerInfoData');
        this.closeCustomerInfoWindow = document.getElementById('closeCustomerInfoWindow');
        this.customerInfoWindowContent = this.customerInfoWindow ? this.customerInfoWindow.querySelector('.modal-content') : null;
        
        // Dragging state
        this.isDragging = false;
        this.dragOffset = { x: 0, y: 0 };
        this.currentDraggedElement = null;
        
        // Audio
        this.ringTone = document.getElementById('ringTone');
        this.remoteAudio = document.getElementById('remoteAudio');
    }
    
    attachEventListeners() {
        // Login
        this.loginForm.addEventListener('submit', (e) => this.handleLogin(e));
        
        // Logout
        this.logoutBtn.addEventListener('click', () => this.handleLogout());
        
        // Agent status
        this.readyBtn.addEventListener('click', () => this.setReady());
        this.notReadyBtn.addEventListener('click', () => this.setNotReady());
        
        // Call controls
        this.answerBtn.addEventListener('click', () => this.answerCall());
        this.holdBtn.addEventListener('click', () => this.holdCall());
        this.unholdBtn.addEventListener('click', () => this.unholdCall());
        this.hangupBtn.addEventListener('click', () => this.hangupCall());
        this.transferBtn.addEventListener('click', () => this.showTransferPanel());
        
        // Transfer
        this.blindTransferBtn.addEventListener('click', () => this.transferCall('blind'));
        this.attendedTransferBtn.addEventListener('click', () => this.transferCall('attended'));
        this.cancelTransferBtn.addEventListener('click', () => this.hideTransferPanel());
        
        // Dialpad
        this.dialButtons.forEach(btn => {
            btn.addEventListener('click', () => {
                const digit = btn.getAttribute('data-digit');
                this.dialInput.value += digit;
                this.updateDialCallButton();
            });
        });
        
        this.dialClearBtn.addEventListener('click', () => {
            this.dialInput.value = '';
            this.updateDialCallButton();
        });
        
        this.dialCallBtn.addEventListener('click', () => this.makeCall());
        
        // Customer popup (with Accept Call button)
        this.closeCustomerPopup.addEventListener('click', () => this.hideCustomerPopup());
        this.acceptCallFromPopup.addEventListener('click', () => {
            // Close popup and open read-only info window when accepting call
            const customerData = this.customerData.innerHTML;
            this.hideCustomerPopup();
            this.showCustomerInfoWindow(customerData);
            this.answerCall();
        });
        
        // Customer info window (read-only)
        this.closeCustomerInfoWindow.addEventListener('click', () => this.hideCustomerInfoWindow());
        
        // Make modals draggable and resizable
        this.initModalDragAndResize();
    }
    
    async handleLogin(e) {
        e.preventDefault();
        
        const formData = new FormData(this.loginForm);
        const data = Object.fromEntries(formData.entries());
        
        try {
            const response = await fetch('/call-center/api/agent/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(data)
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.agent = result.agent;
                this.initSIPClient(data.sip_username, data.sip_password, data.sip_domain);
                this.showDashboard();
            } else {
                alert('Login failed: ' + result.error);
            }
        } catch (error) {
            console.error('Login error:', error);
            alert('Login failed. Please try again.');
        }
    }
    
    async handleLogout() {
        if (!confirm('Are you sure you want to logout?')) {
            return;
        }
        
        try {
            // Disconnect SIP
            if (this.sipUser) {
                await this.sipUser.stop();
            }
            
            // Logout from backend
            await fetch('/call-center/api/agent/logout', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            this.agent = null;
            this.showLogin();
            this.stopStatusTimer();
        } catch (error) {
            console.error('Logout error:', error);
        }
    }
    
    async setReady() {
        try {
            const response = await fetch('/call-center/api/agent/ready', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' }
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.updateAgentStatus('ready');
            }
        } catch (error) {
            console.error('Set ready error:', error);
        }
    }
    
    async setNotReady() {
        const reason = prompt('Reason for not ready:', 'Break');
        if (!reason) return;
        
        try {
            const response = await fetch('/call-center/api/agent/not-ready', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ reason })
            });
            
            const result = await response.json();
            
            if (result.success) {
                this.updateAgentStatus('not-ready');
            }
        } catch (error) {
            console.error('Set not ready error:', error);
        }
    }
    
    sanitizeDomain(domain) {
        let cleanDomain = (domain || '').trim();
        cleanDomain = cleanDomain.replace(/^wss?:\/\//, '');
        cleanDomain = cleanDomain.replace(/^s(?=\d)/, '');
        cleanDomain = cleanDomain.split(':')[0];
        cleanDomain = cleanDomain.replace(/^[^0-9a-zA-Z.]+/, '');
        return cleanDomain;
    }
    
    initSIPClient(username, password, domain) {
        console.log(`Initializing SIP client for ${username}@${domain}`);
        
        const cleanDomain = this.sanitizeDomain(domain);
        
        // Get port from config (default to 7443 if not set)
        const wssPort = window.SIP_CONFIG ? window.SIP_CONFIG.wss_port : 7443;
        const wsUrl = `wss://${cleanDomain}:${wssPort}`;
        console.log(`Connecting to WebSocket: ${wsUrl}`);
        
        const socket = new JsSIP.WebSocketInterface(wsUrl);

        if (JsSIP && JsSIP.debug && typeof JsSIP.debug.enable === 'function') {
            JsSIP.debug.enable('JsSIP:*');
        }
        
        const configuration = {
            sockets: [socket],
            uri: `sip:${username}@${cleanDomain}`,
            password: password,
            display_name: username,
            register: true,
            sessionDescriptionHandlerFactoryOptions: this.sessionDescriptionHandlerFactoryOptions
        };
        
        console.log('Using ICE servers:', this.iceServers);
        console.log('SIP UA configuration rtcConfiguration:', configuration.sessionDescriptionHandlerFactoryOptions);
        
        this.sipUser = new JsSIP.UA(configuration);
        
        // Event handlers
        this.sipUser.on('connected', (e) => {
            console.log('✓ SIP connected');
            this.updateSIPStatus(true);
        });
        
        this.sipUser.on('disconnected', (e) => {
            console.log('✗ SIP disconnected');
            this.updateSIPStatus(false);
        });
        
        this.sipUser.on('registered', (e) => {
            console.log('✓ SIP registered');
            this.updateSIPStatus(true);
        });
        
        this.sipUser.on('unregistered', (e) => {
            console.log('SIP unregistered');
        });
        
        this.sipUser.on('registrationFailed', (e) => {
            console.error('✗ SIP registration failed:', e);
            this.updateSIPStatus(false);
            alert('Failed to register with SIP server. Please check your credentials.');
        });
        
        this.sipUser.on('newRTCSession', (event) => {
            console.log('🔔 New RTC session event received');
            console.log('Event details:', event);
            const session = event.session;
            console.log('Session details:', {
                id: session.id,
                direction: session.direction,
                local_identity: session.local_identity,
                remote_identity: session.remote_identity
            });
            
            if (session.direction === 'incoming') {
                console.log('📞 Incoming call detected, handling...');
                this.handleIncomingCall(session);
            } else {
                const dialedNumber = this.pendingDialNumber || (session.remote_identity && session.remote_identity.uri ? session.remote_identity.uri.user : null);
                if (dialedNumber) {
                    this.currentCall = {
                        call_id: session.id,
                        caller_number: dialedNumber,
                        caller_name: dialedNumber,
                        direction: 'outbound'
                    };
                }
                this.attachSessionEventHandlers(session, 'outbound', dialedNumber);
                this.pendingDialNumber = null;
            }
        });
        
        // Start the User Agent
        try {
            this.sipUser.start();
            console.log('SIP User Agent started');
        } catch (error) {
            console.error('Failed to start SIP User Agent:', error);
            this.updateSIPStatus(false);
            alert('Failed to connect to SIP server: ' + error.message);
        }
    }
    
    attachSessionEventHandlers(session, direction = 'inbound', dialedNumber = null) {
        this.currentSession = session;
        this.activeCallSessionId = session ? session.id : null;

        this.enforceNonTrickle(session);

        this.setupRemoteAudio(session);
        this.observePeerConnection(session.connection);
        
        session.on('progress', () => {
            console.log('Call progressing...');
            if (direction === 'outbound' && dialedNumber) {
                this.showOutgoingCall(dialedNumber);
            }
        });
        
        session.on('accepted', () => {
            console.log('Call accepted');
            this.ringTone.pause();
            this.ringTone.currentTime = 0;
            this.answerInProgress = false;
        });
        
        session.on('confirmed', () => {
            console.log('Call confirmed');
            this.answerInProgress = false;
            this.onCallEstablished();
        });
        
        session.on('ended', () => {
            console.log('Call ended');
            this.ringTone.pause();
            this.ringTone.currentTime = 0;
            this.answerInProgress = false;
            this.onCallEnded();
        });
        
        session.on('failed', (e) => {
            console.error('Call failed:', e);
            this.ringTone.pause();
            this.ringTone.currentTime = 0;
            alert('Call failed: ' + (e && e.cause ? e.cause : 'Unknown error'));
            this.answerInProgress = false;
            this.onCallEnded();
        });
        
        session.on('peerconnection', (data) => {
            this.setupRemoteAudio(session, data.peerconnection);
            this.observePeerConnection(data.peerconnection);
        });
    }
    
    enforceNonTrickle(session) {
        if (!session) {
            return;
        }

        const applyHandlerPreferences = () => {
            const handler = session.sessionDescriptionHandler;
            if (!handler) {
                return;
            }

            handler.options = Object.assign({}, handler.options, {
                disableTrickleIce: true,
                iceGatheringTimeout: 8000,
                peerConnectionConfiguration: this.rtcConfiguration
            });

            if (handler.peerConnection) {
                try {
                    const currentConfig = handler.peerConnection.getConfiguration?.() || {};
                    const mergedConfig = Object.assign({}, currentConfig, {
                        iceServers: this.iceServers,
                        iceTransportPolicy: currentConfig.iceTransportPolicy || this.rtcConfiguration.iceTransportPolicy
                    });
                    handler.peerConnection.setConfiguration(mergedConfig);
                } catch (error) {
                    console.warn('Unable to apply ICE configuration to session peerConnection', error);
                }
            }
        };

        applyHandlerPreferences();
        session.on('peerconnection', () => applyHandlerPreferences());

        session.sessionDescriptionHandlerOptions = Object.assign(
            {},
            session.sessionDescriptionHandlerOptions,
            {
                disableTrickleIce: true,
                trickle: false,
                iceGatheringTimeout: 8000,
                peerConnectionConfiguration: this.rtcConfiguration
            }
        );

        session.on('sdp', (event) => {
            if (!event || event.originator !== 'local' || typeof event.sdp !== 'string') {
                return;
            }

            const sanitized = event.sdp.replace(/^a=ice-options:trickle\r?\n/gim, '');
            if (sanitized !== event.sdp) {
                console.log('Removed ice-options:trickle from local SDP answer');
                event.sdp = sanitized;
            }
        });
    }

    setupRemoteAudio(session, peerConnectionOverride = null) {
        const applyRemoteTracks = (pc) => {
            if (!pc) return;
            
            const remoteStream = new MediaStream();
            
            pc.addEventListener('track', (event) => {
                event.streams.forEach((stream) => {
                    stream.getTracks().forEach((track) => {
                        const alreadyAdded = remoteStream.getTracks().some((existingTrack) => existingTrack.id === track.id);
                        if (!alreadyAdded) {
                            remoteStream.addTrack(track);
                        }
                    });
                });
                this.remoteAudio.srcObject = remoteStream;
                this.remoteAudio.play().catch(() => {});
            });
            
            pc.getReceivers().forEach((receiver) => {
                if (receiver.track) {
                    const alreadyAdded = remoteStream.getTracks().some((existingTrack) => existingTrack.id === receiver.track.id);
                    if (!alreadyAdded) {
                        remoteStream.addTrack(receiver.track);
                    }
                }
            });
            
            this.remoteAudio.srcObject = remoteStream;
            this.remoteAudio.play().catch(() => {});
        };
        
        const pc = peerConnectionOverride || session.connection;
        if (pc) {
            applyRemoteTracks(pc);
        }
    }

    buildSessionOptions(stream) {
        return {
            mediaStream: stream,
            pcConfig: this.rtcConfiguration,
            trickle: false,
            sessionDescriptionHandlerOptions: {
                constraints: {
                    audio: true,
                    video: false
                },
                peerConnectionConfiguration: this.rtcConfiguration,
                trickle: false,
                disableTrickleIce: true
            },
            sessionDescriptionHandlerFactoryOptions: this.sessionDescriptionHandlerFactoryOptions
        };
    }

    observePeerConnection(pc) {
        if (!pc) return;

        let currentConfig = {};
        if (typeof pc.getConfiguration === 'function') {
            try {
                currentConfig = pc.getConfiguration() || {};
                console.log('RTCPeerConnection configuration:', currentConfig);
            } catch (error) {
                console.warn('Unable to read RTCPeerConnection configuration:', error);
            }
        }

        if (!currentConfig.iceServers || currentConfig.iceServers.length === 0) {
            const updatedConfig = Object.assign({}, currentConfig, { iceServers: this.iceServers });
            try {
                pc.setConfiguration(updatedConfig);
                console.log('Applied ICE servers to RTCPeerConnection:', updatedConfig);
            } catch (error) {
                console.error('Failed to apply ICE servers to RTCPeerConnection:', error);
            }
        }

        pc.addEventListener('icecandidateerror', (event) => {
            console.warn('ICE candidate error:', {
                errorCode: event.errorCode,
                errorText: event.errorText,
                hostCandidate: event.hostCandidate,
                url: event.url,
                address: event.address,
                port: event.port
            });
        });

        pc.addEventListener('iceconnectionstatechange', () => {
            console.log('ICE connection state changed:', pc.iceConnectionState);
        });

        pc.addEventListener('connectionstatechange', () => {
            console.log('Peer connection state changed:', pc.connectionState);
        });
    }
    
    async ensureLocalAudioStream() {
        if (this.audioAccessDenied) {
            throw new Error('Microphone access previously denied');
        }
        
        if (this.localStream && this.localStream.active) {
            return this.localStream;
        }
        
        try {
            this.localStream = await navigator.mediaDevices.getUserMedia({
                audio: true,
                video: false
            });
            this.audioAccessDenied = false;
            return this.localStream;
        } catch (error) {
            this.audioAccessDenied = true;
            console.error('Microphone access denied:', error);
            throw error;
        }
    }
    
    handleIncomingCall(session) {
        console.log('Incoming call:', session);
        
        const incomingIdentity = this.extractSessionIdentity(session);
        const hasActiveCall = this.activeCallSessionId && this.activeCallSessionId !== session.id;
        
        if (hasActiveCall && this.isReinviteForActiveCall(incomingIdentity)) {
            this.handleReinviteSession(session, incomingIdentity);
            return;
        }
        
        if (hasActiveCall) {
            this.handleParallelInviteDuringActiveCall(session, incomingIdentity);
            return;
        }
        
        const remoteIdentity = session.remote_identity;
        const callerNumber = remoteIdentity.uri.user;
        const callerName = remoteIdentity.display_name || callerNumber;
        
        // Generate call ID
        const callId = session.id;
        
        // Mock customer data (in production, fetch from CRM)
        const customerId = callerNumber;
        
        this.currentSession = session;
        this.activeCallSessionId = session.id;
        this.firstCallTimestamp = Date.now(); // Track when first call arrives for Dial leg detection
        this.currentCall = {
            call_id: callId,
            caller_number: callerNumber,
            caller_name: callerName,
            customer_id: customerId,
            direction: 'inbound'
        };
        this.activeCallIdentity = incomingIdentity;
        
        // Notify backend
        fetch('/call-center/api/call/ringing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(this.currentCall)
        });
        
        // Update UI
        this.showIncomingCall(callerName, callerNumber);
        
        // Show customer popup
        this.showCustomerPopup(customerId);
        
        // Play ringtone (with error handling for autoplay restrictions)
        if (this.ringTone) {
            // Ensure ringtone is loaded and ready
            if (this.ringTone.readyState >= 2) { // HAVE_CURRENT_DATA or higher
                this.ringTone.play().catch(error => {
                    console.warn('Ringtone autoplay prevented:', error);
                    // Try to play when user interacts (e.g., clicks accept button)
                });
            } else {
                // Wait for ringtone to load
                this.ringTone.addEventListener('canplay', () => {
                    this.ringTone.play().catch(error => {
                        console.warn('Ringtone autoplay prevented after load:', error);
                    });
                }, { once: true });
                this.ringTone.load(); // Force load
            }
        } else {
            console.warn('Ringtone audio element not found');
        }
        
        this.attachSessionEventHandlers(session, 'inbound');
    }

    extractSessionIdentity(session) {
        if (!session) {
            return { callId: null, twilioCallSid: null, fromTag: null };
        }
        const request = session.request || {};
        
        // Try multiple ways to get headers (JsSIP may store them differently)
        const getHeader = typeof request.getHeader === 'function'
            ? (name) => request.getHeader(name)
            : (name) => {
                // Try accessing headers directly
                if (request.headers) {
                    const header = Array.isArray(request.headers)
                        ? request.headers.find(h => h.name && h.name.toLowerCase() === name.toLowerCase())
                        : request.headers[name] || request.headers[name.toLowerCase()];
                    return header ? (header.value || header) : null;
                }
                // Try case-insensitive property access
                const lowerName = name.toLowerCase();
                for (const key in request) {
                    if (key.toLowerCase() === lowerName) {
                        return request[key];
                    }
                }
                return null;
            };
        
        const twilioCallSid = getHeader('X-Twilio-CallSid') || getHeader('x-twilio-callsid');
        const callId = request.call_id || request.callId || session.id;
        const fromTag = request.from_tag || null;
        
        const identity = {
            callId: callId,
            twilioCallSid: twilioCallSid,
            fromTag: fromTag
        };
        
        console.log('Extracted session identity:', {
            sessionId: session.id,
            callId: identity.callId,
            twilioCallSid: identity.twilioCallSid,
            fromTag: identity.fromTag,
            hasRequest: !!request,
            requestKeys: request ? Object.keys(request).slice(0, 10) : []
        });
        
        return identity;
    }

    isReinviteForActiveCall(identity) {
        if (!identity || !this.activeCallIdentity) {
            return false;
        }
        if (identity.callId && this.activeCallIdentity.callId && identity.callId === this.activeCallIdentity.callId) {
            return true;
        }
        if (identity.twilioCallSid && this.activeCallIdentity.twilioCallSid &&
            identity.twilioCallSid === this.activeCallIdentity.twilioCallSid) {
            return true;
        }
        return false;
    }

    handleReinviteSession(session, identity) {
        console.warn('Detected SIP re-INVITE for active call. Auto-processing session update.', {
            reinviteSession: session.id,
            reinviteIdentity: identity,
            activeSession: this.activeCallSessionId,
            activeIdentity: this.activeCallIdentity
        });
        this.answerReinviteSession(session);
    }

    async answerReinviteSession(session) {
        try {
            const stream = (this.localStream && this.localStream.active)
                ? this.localStream
                : await this.ensureLocalAudioStream();
            const options = this.buildSessionOptions(stream);
            await session.answer(options);
            session.on('accepted', () => console.log('Re-INVITE leg accepted', session.id));
            session.on('confirmed', () => console.log('Re-INVITE leg confirmed', session.id));
            session.on('ended', () => console.log('Re-INVITE leg ended', session.id));
        } catch (error) {
            console.error('Failed to auto-answer re-INVITE session', error);
            try {
                session.terminate({
                    status_code: 488,
                    reason_phrase: 'Unable to process re-INVITE'
                });
            } catch (terminateError) {
                console.warn('Failed to terminate re-INVITE session cleanly', terminateError);
            }
        }
    }

    handleParallelInviteDuringActiveCall(session, identity) {
        // Check if this is a Dial leg from Twilio (second INVITE with different Call SID)
        // In Twilio transfers, the Dial leg has a different Call SID but is part of the same transfer
        // We should answer this call and replace the current session
        
        // Criteria for Dial leg detection (works even if Twilio Call SIDs are not available):
        // 1. Different Call-IDs (different SIP sessions) - REQUIRED
        // 2. Arrives when there's already an active call - REQUIRED (we're in this function)
        // 3. Arrives within 60 seconds of the first call - REQUIRED
        // 4. If Twilio Call SIDs are available, they should be different (optional check)
        // 5. Both are inbound calls to extension 2001 (implicit - we're receiving them)
        
        const differentCallIds = identity.callId && this.activeCallIdentity.callId && 
                                 identity.callId !== this.activeCallIdentity.callId;
        const timeSinceFirstCall = Date.now() - (this.firstCallTimestamp || Date.now());
        const withinTimeWindow = timeSinceFirstCall < 60000; // 60 seconds (increased from 10)
        
        // Optional: Check Twilio Call SIDs if available
        const hasTwilioCallSids = identity.twilioCallSid && this.activeCallIdentity.twilioCallSid;
        const differentCallSids = hasTwilioCallSids && 
                                  identity.twilioCallSid !== this.activeCallIdentity.twilioCallSid;
        
        // Dial leg detection: Different Call-IDs + within time window
        // If Twilio Call SIDs are available, they should also be different
        const isDialLeg = differentCallIds && withinTimeWindow && 
                         (!hasTwilioCallSids || differentCallSids);
        
        console.log('🔍 Dial leg detection check:', {
            hasTwilioCallSids,
            differentCallSids,
            differentCallIds,
            withinTimeWindow,
            timeSinceFirstCall: `${timeSinceFirstCall}ms`,
            isDialLeg,
            incomingIdentity: identity,
            activeIdentity: this.activeCallIdentity,
            firstCallTimestamp: this.firstCallTimestamp,
            currentTime: Date.now(),
            currentAnswerBtnDisabled: this.answerBtn.disabled,
            currentPopupBtnDisabled: this.acceptCallFromPopup ? this.acceptCallFromPopup.disabled : 'N/A'
        });
        
        if (isDialLeg) {
            console.log('✅ Detected Dial leg from Twilio transfer. Switching to Dial leg session.', {
                activeSession: this.activeCallSessionId,
                activeCallSid: this.activeCallIdentity.twilioCallSid,
                activeCallId: this.activeCallIdentity.callId,
                incomingSession: session.id,
                incomingCallSid: identity.twilioCallSid,
                incomingCallId: identity.callId,
                timeSinceFirstCall: `${timeSinceFirstCall}ms`
            });
            
            // CRITICAL: Do NOT terminate the old session!
            // Terminating the first call causes FusionPBX/Twilio to cancel the Dial leg.
            // Instead, we keep the old session alive and just switch to the Dial leg.
            // The old session will naturally end when the Dial leg is answered and established.
            const oldSession = this.currentSession;
            if (oldSession && oldSession.id !== session.id) {
                console.log('Keeping old session alive (not terminating) to prevent Dial leg cancellation', {
                    oldSessionId: oldSession.id,
                    oldSessionStatus: oldSession.status
                });
                // Store reference to old session but don't terminate it
                // It will be cleaned up when the Dial leg is established
            }
            
            // Replace the current session with the Dial leg BEFORE attaching handlers
            // This ensures onCallEstablished checks the correct session
            this.currentSession = session;
            this.activeCallSessionId = session.id;
            this.activeCallIdentity = identity;
            
            // Reset call state to ensure Answer button can be enabled
            this.answerInProgress = false;
            
            // Update UI to show incoming call for the Dial leg FIRST
            // This enables the Answer button immediately
            const remoteIdentity = session.remote_identity;
            const callerNumber = remoteIdentity.uri.user;
            const callerName = remoteIdentity.display_name || callerNumber;
            this.showIncomingCall(callerName, callerNumber);
            
            // Force enable Answer button (showIncomingCall should do this, but be explicit)
            this.answerBtn.disabled = false;
            if (this.acceptCallFromPopup) {
                this.acceptCallFromPopup.disabled = false;
            }
            console.log('✅ Answer button enabled for Dial leg', {
                sessionId: session.id,
                sessionStatus: session.status,
                answerBtnDisabled: this.answerBtn.disabled,
                popupBtnDisabled: this.acceptCallFromPopup ? this.acceptCallFromPopup.disabled : 'N/A'
            });
            
            // CRITICAL: Attach event handlers IMMEDIATELY to ensure 180 Ringing is sent
            // This prevents FusionPBX from canceling the Dial leg due to timeout
            this.attachSessionEventHandlers(session, 'inbound');
            
            // CRITICAL: Auto-answer the Dial leg immediately to prevent FusionPBX from canceling it
            // FusionPBX will cancel the Dial leg if it's not answered quickly, especially if
            // the first call is already established. By auto-answering, we ensure the Dial leg
            // is connected before FusionPBX has a chance to cancel it.
            console.log('Auto-answering Dial leg to prevent cancellation', {
                sessionId: session.id,
                sessionStatus: session.status
            });
            
            // If popup is open, copy customer data to info window before auto-answering
            if (this.customerPopup.classList.contains('active') && this.customerData.innerHTML && !this.customerData.innerHTML.includes('loading')) {
                const customerDataHtml = this.customerData.innerHTML;
                this.hideCustomerPopup();
                this.showCustomerInfoWindow(customerDataHtml);
            }
            
            // Auto-answer the Dial leg immediately
            this.answerCall().catch(error => {
                console.error('Failed to auto-answer Dial leg', error);
                // If auto-answer fails, at least the Answer button is enabled for manual answer
            });
            
            // Show popup again if it's not already shown (in case it was closed)
            if (this.currentCall && this.currentCall.customer_id) {
                this.showCustomerPopup(this.currentCall.customer_id);
            }
            
            return;
        }
        
        console.warn('Already handling an active call. Ignoring parallel incoming session.', {
            activeSession: this.activeCallSessionId,
            incomingSession: session.id,
            incomingIdentity: identity,
            activeIdentity: this.activeCallIdentity,
            isDialLeg: false,
            reasons: {
                hasTwilioCallSids,
                differentCallSids,
                differentCallIds,
                withinTimeWindow
            }
        });
        session.on('failed', () => console.log('Ignored parallel session failed', session.id));
        session.on('ended', () => console.log('Ignored parallel session ended', session.id));
    }

    async answerCall() {
        const session = this.currentSession;
        if (!session) {
            console.warn('No active session to answer');
            return;
        }

        if (typeof session.isEnded === 'function' && session.isEnded()) {
            console.warn('Cannot answer: session already ended');
            return;
        }

        const status = session.status;
        const allowedStatuses = [];
        if (typeof JsSIP !== 'undefined' && JsSIP.RTCSession && JsSIP.RTCSession.C) {
            const C = JsSIP.RTCSession.C;
            allowedStatuses.push(
                C.STATUS_NULL,
                C.STATUS_INVITE_RECEIVED,
                C.STATUS_1XX_RECEIVED,
                C.STATUS_WAITING_FOR_ANSWER
            );
        }

        if (allowedStatuses.length && !allowedStatuses.includes(status)) {
            console.warn('Skipping answer; session status not answerable', { status });
            return;
        }

        if (this.answerInProgress) {
            console.warn('Answer already in progress for current session');
            return;
        }

        this.answerInProgress = true;
        this.answerBtn.disabled = true;
        if (this.acceptCallFromPopup) {
            this.acceptCallFromPopup.disabled = true;
        }

        try {
            console.log('Attempting to answer call', { sessionId: session.id, status });
            const stream = await this.ensureLocalAudioStream();
            
            await session.answer(this.buildSessionOptions(stream));
            console.log('Answer sent for session', session.id);
            
            // Notify backend
            await fetch('/call-center/api/call/answer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ call_id: this.currentCall.call_id })
            });
            
            this.ringTone.pause();
            this.ringTone.currentTime = 0;
        } catch (error) {
            if (error && error.name === 'NotAllowedError') {
                alert('Microphone access is required to answer calls. Please allow microphone permissions in your browser.');
            }
            console.error('Answer call error:', error);
            this.answerInProgress = false;
            this.answerBtn.disabled = false;
            if (this.acceptCallFromPopup) {
                this.acceptCallFromPopup.disabled = false;
            }
        }
    }
    
    async hangupCall() {
        if (!this.currentSession) return;
        
        try {
            this.currentSession.terminate();
            
            // Notify backend if we still have call metadata
            if (this.currentCall && this.currentCall.call_id) {
                await fetch('/call-center/api/call/drop', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ call_id: this.currentCall.call_id })
                });
            }
        } catch (error) {
            console.error('Hangup call error:', error);
        }
    }
    
    async holdCall() {
        if (!this.currentSession) return;
        
        try {
            this.currentSession.hold();
            
            // Notify backend
            await fetch('/call-center/api/call/hold', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ call_id: this.currentCall.call_id })
            });
            
            this.holdBtn.style.display = 'none';
            this.unholdBtn.style.display = 'block';
        } catch (error) {
            console.error('Hold call error:', error);
        }
    }
    
    async unholdCall() {
        if (!this.currentSession) return;
        
        try {
            this.currentSession.unhold();
            
            // Notify backend
            await fetch('/call-center/api/call/unhold', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ call_id: this.currentCall.call_id })
            });
            
            this.holdBtn.style.display = 'block';
            this.unholdBtn.style.display = 'none';
        } catch (error) {
            console.error('Unhold call error:', error);
        }
    }
    
    async makeCall() {
        const number = this.dialInput.value;
        if (!number || !this.sipUser) {
            console.error('Cannot make call: number missing or SIP user not initialized');
            return;
        }
        
        try {
            const cleanDomain = this.sanitizeDomain(this.agent.sip_domain);
            const target = `sip:${number}@${cleanDomain}`;
            const stream = await this.ensureLocalAudioStream();
            const options = this.buildSessionOptions(stream);
            this.pendingDialNumber = number;
            
            const session = this.sipUser.call(target, options);
            this.currentSession = session;
            
            this.dialInput.value = '';
            this.updateDialCallButton();
        } catch (error) {
            let message = 'Failed to make call: ' + (error && error.message ? error.message : 'Unknown error');
            if (error && error.name === 'NotAllowedError') {
                message = 'Microphone access is required to make calls. Please allow microphone permissions in your browser.';
            }
            console.error('Make call error:', error);
            alert(message);
        }
    }
    
    showTransferPanel() {
        this.transferPanel.style.display = 'block';
    }
    
    hideTransferPanel() {
        this.transferPanel.style.display = 'none';
        this.transferNumber.value = '';
    }
    
    async transferCall(type) {
        const transferTo = this.transferNumber.value;
        if (!transferTo) {
            alert('Please enter transfer destination');
            return;
        }
        
        try {
            // Notify backend
            await fetch('/call-center/api/call/transfer', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    call_id: this.currentCall.call_id,
                    transfer_to: transferTo,
                    transfer_type: type
                })
            });
            
            // Perform SIP REFER for blind transfer
            if (type === 'blind' && this.currentSession) {
                const cleanDomain = this.sanitizeDomain(this.agent.sip_domain);
                const referTo = `sip:${transferTo}@${cleanDomain}`;
                this.currentSession.refer(referTo);
            }
            
            this.hideTransferPanel();
            alert(`Call ${type} transferred to ${transferTo}`);
        } catch (error) {
            console.error('Transfer call error:', error);
            alert('Transfer failed');
        }
    }
    
    async showCustomerPopup(customerId) {
        this.customerPopup.classList.add('active');
        this.customerData.innerHTML = '<div class="customer-info loading"><i class="fas fa-spinner fa-spin"></i> Loading customer data...</div>';
        
        // Reset position to center if not already positioned
        if (this.customerPopupContent) {
            const rect = this.customerPopupContent.getBoundingClientRect();
            if (rect.left === 0 && rect.top === 0) {
                this.customerPopupContent.style.left = '50%';
                this.customerPopupContent.style.top = '50%';
                this.customerPopupContent.style.transform = 'translate(-50%, -50%)';
            }
        }
        
        // Ensure Answer button in popup is enabled when popup is shown
        // This is critical because the popup may be shown before showIncomingCall completes,
        // or showIncomingCall may have been called but the button state needs to be refreshed
        if (this.acceptCallFromPopup && this.currentSession) {
            // Only enable if we have an active session that can be answered
            const status = this.currentSession.status;
            const answerableStatuses = [];
            if (typeof JsSIP !== 'undefined' && JsSIP.RTCSession && JsSIP.RTCSession.C) {
                const C = JsSIP.RTCSession.C;
                answerableStatuses.push(
                    C.STATUS_NULL,
                    C.STATUS_INVITE_RECEIVED,
                    C.STATUS_1XX_RECEIVED,
                    C.STATUS_WAITING_FOR_ANSWER
                );
            }
            if (answerableStatuses.length === 0 || answerableStatuses.includes(status)) {
                this.acceptCallFromPopup.disabled = false;
                console.log('Enabled popup Answer button', { sessionId: this.currentSession.id, status });
            } else {
                console.log('Popup Answer button remains disabled - session not answerable', { sessionId: this.currentSession.id, status });
            }
        }
        
        try {
            // Extract Call SID or Call-ID from current session for unique lookup
            let url = `/call-center/api/customer/${customerId}`;
            const identity = this.currentSession ? this.extractSessionIdentity(this.currentSession) : null;
            const params = new URLSearchParams();
            
            if (identity) {
                if (identity.twilioCallSid) {
                    params.append('call_sid', identity.twilioCallSid);
                } else if (identity.callId) {
                    params.append('call_id', identity.callId);
                }
            }
            
            if (params.toString()) {
                url += '?' + params.toString();
            }
            
            console.log('Fetching customer data with unique identifier', { url, identity });
            
            const response = await fetch(url);
            const customer = await response.json();
            
            this.displayCustomerData(customer);
        } catch (error) {
            console.error('Fetch customer data error:', error);
            this.customerData.innerHTML = '<div class="customer-info"><p>Failed to load customer data</p></div>';
        }
    }
    
    displayCustomerData(customer) {
        const html = this.getCustomerDataHtml(customer);
        this.customerData.innerHTML = html;
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    initModalDragAndResize() {
        // Initialize dragging for customer popup
        if (this.customerPopupContent) {
            const header = this.customerPopupContent.querySelector('.modal-header');
            if (header) {
                header.addEventListener('mousedown', (e) => this.startDrag(e, this.customerPopupContent));
            }
        }
        
        // Initialize dragging for customer info window
        if (this.customerInfoWindowContent) {
            const header = this.customerInfoWindowContent.querySelector('.modal-header');
            if (header) {
                header.addEventListener('mousedown', (e) => this.startDrag(e, this.customerInfoWindowContent));
            }
        }
        
        // Global mouse move and up handlers
        document.addEventListener('mousemove', (e) => this.onDrag(e));
        document.addEventListener('mouseup', () => this.stopDrag());
    }
    
    startDrag(e, element) {
        // Don't start drag if clicking on close button or other interactive elements
        if (e.target.classList.contains('close-modal') || 
            e.target.closest('button') || 
            e.target.closest('input') ||
            e.target.closest('select') ||
            e.target.closest('textarea')) {
            return;
        }
        
        this.isDragging = true;
        this.currentDraggedElement = element;
        
        const rect = element.getBoundingClientRect();
        this.dragOffset.x = e.clientX - rect.left;
        this.dragOffset.y = e.clientY - rect.top;
        
        element.style.cursor = 'move';
        e.preventDefault();
    }
    
    onDrag(e) {
        if (!this.isDragging || !this.currentDraggedElement) {
            return;
        }
        
        const x = e.clientX - this.dragOffset.x;
        const y = e.clientY - this.dragOffset.y;
        
        // Constrain to viewport
        const maxX = window.innerWidth - this.currentDraggedElement.offsetWidth;
        const maxY = window.innerHeight - this.currentDraggedElement.offsetHeight;
        
        const constrainedX = Math.max(0, Math.min(x, maxX));
        const constrainedY = Math.max(0, Math.min(y, maxY));
        
        this.currentDraggedElement.style.left = `${constrainedX}px`;
        this.currentDraggedElement.style.top = `${constrainedY}px`;
        this.currentDraggedElement.style.transform = 'none';
        this.currentDraggedElement.style.margin = '0';
        
        e.preventDefault();
    }
    
    stopDrag() {
        if (this.isDragging && this.currentDraggedElement) {
            this.currentDraggedElement.style.cursor = 'default';
        }
        this.isDragging = false;
        this.currentDraggedElement = null;
    }
    
    hideCustomerPopup() {
        this.customerPopup.classList.remove('active');
    }
    
    showCustomerInfoWindow(customerDataHtml = null) {
        // Copy customer data from popup if provided, otherwise fetch fresh
        if (customerDataHtml) {
            this.customerInfoData.innerHTML = customerDataHtml;
        }
        
        this.customerInfoWindow.classList.add('active');
        
        // Reset position to center if not already positioned
        if (this.customerInfoWindowContent) {
            const rect = this.customerInfoWindowContent.getBoundingClientRect();
            if (rect.left === 0 && rect.top === 0) {
                this.customerInfoWindowContent.style.left = '50%';
                this.customerInfoWindowContent.style.top = '50%';
                this.customerInfoWindowContent.style.transform = 'translate(-50%, -50%)';
            }
        }
        
        console.log('📋 Customer info window opened (read-only)');
    }
    
    hideCustomerInfoWindow() {
        this.customerInfoWindow.classList.remove('active');
        console.log('📋 Customer info window closed');
    }
    
    async updateCustomerInfoWindow(customerId) {
        // Update the info window with fresh customer data
        try {
            // Extract Call SID or Call-ID from current session for unique lookup
            let url = `/call-center/api/customer/${customerId}`;
            const identity = this.currentSession ? this.extractSessionIdentity(this.currentSession) : null;
            const params = new URLSearchParams();
            
            if (identity) {
                if (identity.twilioCallSid) {
                    params.append('call_sid', identity.twilioCallSid);
                } else if (identity.callId) {
                    params.append('call_id', identity.callId);
                }
            }
            
            if (params.toString()) {
                url += '?' + params.toString();
            }
            
            const response = await fetch(url);
            const customer = await response.json();
            
            this.displayCustomerDataInWindow(customer);
        } catch (error) {
            console.error('Fetch customer data error:', error);
            this.customerInfoData.innerHTML = '<div class="customer-info"><p>Failed to load customer data</p></div>';
        }
    }
    
    displayCustomerDataInWindow(customer) {
        // Same display logic but without Accept Call button section
        const html = this.getCustomerDataHtml(customer);
        this.customerInfoData.innerHTML = html;
    }
    
    getCustomerDataHtml(customer) {
        // Build customer info section
        let customerInfoHtml = `
            <div class="customer-info">
                <div class="customer-field">
                    <label>Customer ID:</label>
                    <span>${customer.customer_id || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Name:</label>
                    <span>${customer.name || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Email:</label>
                    <span>${customer.email || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Phone:</label>
                    <span>${customer.phone || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Account Status:</label>
                    <span>${customer.account_status || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Tier:</label>
                    <span>${customer.tier || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Last Contact:</label>
                    <span>${customer.last_contact || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Open Tickets:</label>
                    <span>${customer.open_tickets || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Lifetime Value:</label>
                    <span>${customer.lifetime_value || 'N/A'}</span>
                </div>
                <div class="customer-field">
                    <label>Notes:</label>
                    <span>${customer.notes || 'N/A'}</span>
                </div>
            </div>
        `;
        
        // Build activities section (calendar events, todos, etc.)
        let activitiesHtml = '';
        if (customer.activities && customer.activities.length > 0) {
            activitiesHtml = `
                <div class="customer-section">
                    <h4><i class="fas fa-tasks"></i> Activities During Session</h4>
                    <div class="activities-list">
            `;
            
            customer.activities.forEach((activity, index) => {
                let activityIcon = 'fa-check-circle';
                let activityColor = 'info';
                let activityText = '';
                
                if (activity.type === 'calendar_event') {
                    activityIcon = 'fa-calendar';
                    activityColor = 'primary';
                    if (activity.title) {
                        activityText = `Created calendar event: "${activity.title}"`;
                        if (activity.start && activity.end) {
                            activityText += ` (${activity.start} - ${activity.end})`;
                        }
                    } else {
                        activityText = `Created calendar event: ${activity.raw || 'Event created'}`;
                    }
                } else if (activity.type === 'todo') {
                    activityIcon = 'fa-list-check';
                    if (activity.action === 'created') {
                        activityColor = 'success';
                        activityText = `Created todo: "${activity.title || 'Todo'}"`;
                        if (activity.priority) {
                            activityText += ` (Priority: ${activity.priority})`;
                        }
                        if (activity.due_date) {
                            activityText += ` (Due: ${activity.due_date})`;
                        }
                    } else if (activity.action === 'completed') {
                        activityColor = 'success';
                        activityText = `Completed todo: ${activity.raw || 'Todo completed'}`;
                    } else if (activity.action === 'updated') {
                        activityColor = 'warning';
                        activityText = `Updated todo: ${activity.raw || 'Todo updated'}`;
                    } else if (activity.action === 'deleted') {
                        activityColor = 'danger';
                        activityText = `Deleted todo: ${activity.raw || 'Todo deleted'}`;
                    } else {
                        activityText = `Todo ${activity.action}: ${activity.raw || 'Todo action'}`;
                    }
                } else {
                    activityText = `${activity.type}: ${activity.raw || 'Activity'}`;
                }
                
                activitiesHtml += `
                    <div class="activity-item activity-${activityColor}">
                        <i class="fas ${activityIcon}"></i>
                        <span>${activityText}</span>
                    </div>
                `;
            });
            
            activitiesHtml += `
                    </div>
                </div>
            `;
        }
        
        // Build conversation history section
        let conversationHtml = '';
        if (customer.conversation_history && customer.conversation_history.length > 0) {
            conversationHtml = `
                <div class="customer-section">
                    <h4><i class="fas fa-comments"></i> Conversation History</h4>
                    <div class="conversation-list">
            `;
            
            // Show last 10 messages (most recent first)
            const recentMessages = customer.conversation_history.slice(-10).reverse();
            
            recentMessages.forEach((msg, index) => {
                const messageClass = msg.type === 'user' ? 'user-message' : 'assistant-message';
                const messageIcon = msg.type === 'user' ? 'fa-user' : 'fa-robot';
                const messageLabel = msg.type === 'user' ? 'User' : 'Assistant';
                
                conversationHtml += `
                    <div class="conversation-item ${messageClass}">
                        <div class="message-header">
                            <i class="fas ${messageIcon}"></i>
                            <strong>${messageLabel}</strong>
                        </div>
                        <div class="message-content">${this.escapeHtml(msg.content || '')}</div>
                    </div>
                `;
            });
            
            conversationHtml += `
                    </div>
                </div>
            `;
        }
        
        // Combine all sections
        return customerInfoHtml + activitiesHtml + conversationHtml;
    }
    
    showIncomingCall(callerName, callerNumber) {
        console.log('📞 showIncomingCall called', {
            callerName,
            callerNumber,
            currentSessionId: this.currentSession?.id,
            answerBtnDisabled: this.answerBtn.disabled,
            popupBtnDisabled: this.acceptCallFromPopup ? this.acceptCallFromPopup.disabled : 'N/A'
        });
        
        this.callInfo.innerHTML = `
            <div class="active-call ringing">
                <div class="caller-info">
                    <div class="caller-avatar">
                        <i class="fas fa-user"></i>
                    </div>
                    <div class="caller-details">
                        <h4>${callerName}</h4>
                        <p>${callerNumber}</p>
                    </div>
                </div>
                <div class="call-status">
                    <i class="fas fa-phone-volume"></i> Incoming Call
                </div>
            </div>
        `;
        
        this.answerBtn.disabled = false;
        this.hangupBtn.disabled = false;
        this.answerInProgress = false;
        if (this.acceptCallFromPopup) {
            this.acceptCallFromPopup.disabled = false;
        }
        
        console.log('✅ Answer button enabled in showIncomingCall', {
            answerBtnDisabled: this.answerBtn.disabled,
            popupBtnDisabled: this.acceptCallFromPopup ? this.acceptCallFromPopup.disabled : 'N/A'
        });
    }
    
    showOutgoingCall(number) {
        this.callInfo.innerHTML = `
            <div class="active-call">
                <div class="caller-info">
                    <div class="caller-avatar">
                        <i class="fas fa-user"></i>
                    </div>
                    <div class="caller-details">
                        <h4>Calling...</h4>
                        <p>${number}</p>
                    </div>
                </div>
            </div>
        `;
        
        this.hangupBtn.disabled = false;
    }
    
    onCallEstablished() {
        // Only process if this is for the current active session
        // This prevents old sessions (e.g., parent call) from disabling the Answer button
        // when we're handling a Dial leg replacement
        const establishedSessionId = this.currentSession ? this.currentSession.id : null;
        const activeSessionId = this.activeCallSessionId;
        
        if (establishedSessionId !== activeSessionId) {
            console.log('⚠️ onCallEstablished called for non-active session, ignoring', {
                establishedSessionId,
                activeSessionId,
                currentSessionId: this.currentSession ? this.currentSession.id : null
            });
            return;
        }
        
        console.log('Call established', { sessionId: establishedSessionId });
        
        this.callStartTime = Date.now();
        this.startCallDurationTimer();
        
        this.answerBtn.disabled = true;
        this.holdBtn.disabled = false;
        this.transferBtn.disabled = false;
        this.hangupBtn.disabled = false;
        
        this.updateAgentStatus('on-call');
        
        // If popup is still open, close it and open info window instead
        if (this.customerPopup.classList.contains('active')) {
            const customerDataHtml = this.customerData.innerHTML;
            this.hideCustomerPopup();
            // Only show info window if we have actual customer data (not loading state)
            if (customerDataHtml && !customerDataHtml.includes('loading') && !customerDataHtml.includes('Loading customer data')) {
                this.showCustomerInfoWindow(customerDataHtml);
            } else {
                // If popup data is still loading, fetch fresh data for info window
                const callerNumber = this.currentCall.caller_number || '2001';
                this.updateCustomerInfoWindow(callerNumber);
            }
        } else if (!this.customerInfoWindow.classList.contains('active')) {
            // If popup is closed and info window is not open, open info window with customer data
            const callerNumber = this.currentCall.caller_number || '2001';
            this.updateCustomerInfoWindow(callerNumber);
        }
        
        // Update call info
        const callerName = this.currentCall.caller_name || this.currentCall.caller_number;
        this.callInfo.innerHTML = `
            <div class="active-call">
                <div class="caller-info">
                    <div class="caller-avatar">
                        <i class="fas fa-user"></i>
                    </div>
                    <div class="caller-details">
                        <h4>${callerName}</h4>
                        <p>${this.currentCall.caller_number}</p>
                    </div>
                </div>
                <div class="call-duration" id="callDuration">00:00:00</div>
            </div>
        `;
    }
    
    onCallEnded() {
        console.log('Call ended');
        
        this.stopCallDurationTimer();
        
        // Close customer popup and info window when call ends
        this.hideCustomerPopup();
        this.hideCustomerInfoWindow();
        
        this.callInfo.innerHTML = `
            <div class="no-call">
                <i class="fas fa-phone-slash"></i>
                <p>No active call</p>
            </div>
        `;
        
        this.answerBtn.disabled = true;
        this.holdBtn.disabled = true;
        this.unholdBtn.disabled = true;
        this.transferBtn.disabled = true;
        this.hangupBtn.disabled = true;
        if (this.acceptCallFromPopup) {
            this.acceptCallFromPopup.disabled = false;
        }
        
        this.currentCall = null;
        this.currentSession = null;
        this.pendingDialNumber = null;
        this.answerInProgress = false;
        this.activeCallSessionId = null;
        this.activeCallIdentity = null;
        
        this.setReady();
    }
    
    startCallDurationTimer() {
        this.callDurationTimer = setInterval(() => {
            const duration = Math.floor((Date.now() - this.callStartTime) / 1000);
            const hours = Math.floor(duration / 3600).toString().padStart(2, '0');
            const minutes = Math.floor((duration % 3600) / 60).toString().padStart(2, '0');
            const seconds = (duration % 60).toString().padStart(2, '0');
            
            const durationEl = document.getElementById('callDuration');
            if (durationEl) {
                durationEl.textContent = `${hours}:${minutes}:${seconds}`;
            }
        }, 1000);
    }
    
    stopCallDurationTimer() {
        if (this.callDurationTimer) {
            clearInterval(this.callDurationTimer);
            this.callDurationTimer = null;
        }
    }
    
    updateAgentStatus(status) {
        const statusMap = {
            'logged-out': 'Logged Out',
            'logged-in': 'Logged In',
            'ready': 'Ready',
            'not-ready': 'Not Ready',
            'on-call': 'On Call'
        };
        
        this.currentStatus.textContent = statusMap[status] || status;
        this.currentStatus.className = 'status-badge ' + status;
        
        // Enable/disable buttons based on status
        if (status === 'ready') {
            this.readyBtn.disabled = true;
            this.notReadyBtn.disabled = false;
            this.dialCallBtn.disabled = false;
        } else if (status === 'not-ready') {
            this.readyBtn.disabled = false;
            this.notReadyBtn.disabled = true;
            this.dialCallBtn.disabled = true;
        } else {
            this.readyBtn.disabled = false;
            this.notReadyBtn.disabled = false;
        }
        
        // Restart status timer
        this.statusStartTime = Date.now();
        this.startStatusTimer();
    }
    
    startStatusTimer() {
        this.stopStatusTimer();
        
        this.statusTimer = setInterval(() => {
            const duration = Math.floor((Date.now() - this.statusStartTime) / 1000);
            const hours = Math.floor(duration / 3600).toString().padStart(2, '0');
            const minutes = Math.floor((duration % 3600) / 60).toString().padStart(2, '0');
            const seconds = (duration % 60).toString().padStart(2, '0');
            
            this.statusTimerDisplay.textContent = `${hours}:${minutes}:${seconds}`;
        }, 1000);
    }
    
    stopStatusTimer() {
        if (this.statusTimer) {
            clearInterval(this.statusTimer);
            this.statusTimer = null;
        }
    }
    
    updateSIPStatus(connected) {
        if (connected) {
            this.sipStatus.innerHTML = '<i class="fas fa-circle"></i><span>Connected</span>';
            this.sipStatus.classList.add('connected');
        } else {
            this.sipStatus.innerHTML = '<i class="fas fa-circle"></i><span>Disconnected</span>';
            this.sipStatus.classList.remove('connected');
        }
    }
    
    updateDialCallButton() {
        this.dialCallBtn.disabled = !this.dialInput.value || this.currentCall !== null;
    }
    
    showDashboard() {
        this.loginScreen.classList.remove('active');
        this.dashboardScreen.classList.add('active');
        
        this.agentNameDisplay.textContent = this.agent.name;
        this.agentExtension.textContent = `Ext: ${this.agent.sip_extension}`;
        
        this.updateAgentStatus('logged-in');
        
        this.readyBtn.disabled = false;
        this.notReadyBtn.disabled = false;
    }
    
    showLogin() {
        this.dashboardScreen.classList.remove('active');
        this.loginScreen.classList.add('active');
        this.loginForm.reset();
    }
    
    async checkLoginStatus() {
        try {
            const response = await fetch('/call-center/api/agent/status');
            const result = await response.json();
            
            if (result.logged_in) {
                this.agent = result.agent;
                this.showDashboard();
                this.initSIPClient(
                    this.agent.sip_username,
                    '', // Password not returned
                    this.agent.sip_domain
                );
            }
        } catch (error) {
            console.error('Check login status error:', error);
        }
    }
}

// Initialize the call center agent dashboard
document.addEventListener('DOMContentLoaded', () => {
    window.callCenterAgent = new CallCenterAgent();
});

