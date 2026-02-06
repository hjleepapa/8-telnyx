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
        
        // Customer popup
        this.customerPopup = document.getElementById('customerPopup');
        this.customerData = document.getElementById('customerData');
        this.closeCustomerPopup = document.getElementById('closeCustomerPopup');
        this.acceptCallFromPopup = document.getElementById('acceptCallFromPopup');
        
        // Customer info window (read-only, persistent during call)
        this.customerInfoWindow = document.getElementById('customerInfoWindow');
        this.customerInfoData = document.getElementById('customerInfoData');
        this.closeCustomerInfoWindow = document.getElementById('closeCustomerInfoWindow');
        
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
        
        // Customer popup
        this.closeCustomerPopup.addEventListener('click', () => this.hideCustomerPopup());
        this.acceptCallFromPopup.addEventListener('click', () => {
            this.hideCustomerPopup();
            this.showCustomerInfoWindow(); // Open info window when accepting call
            this.answerCall();
        });
        
        // Customer info window
        if (this.closeCustomerInfoWindow) {
            this.closeCustomerInfoWindow.addEventListener('click', () => this.hideCustomerInfoWindow());
        }
        
        // Initialize drag and resize functionality
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
            console.log('New RTC session');
            const session = event.session;
            
            if (session.direction === 'incoming') {
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
        const remoteIdentity = session.remote_identity;
        const callerNumber = remoteIdentity && remoteIdentity.uri ? remoteIdentity.uri.user : null;
        const callerName = remoteIdentity && remoteIdentity.display_name ? remoteIdentity.display_name : callerNumber;
        const hasActiveCall = this.activeCallSessionId && this.activeCallSessionId !== session.id;
        
        if (hasActiveCall && this.isReinviteForActiveCall(incomingIdentity)) {
            this.handleReinviteSession(session, incomingIdentity);
            return;
        }
        
        // Check if this is a transfer call (has Twilio Call SID and we have an active call)
        if (hasActiveCall && this.isTransferCall(session, incomingIdentity)) {
            console.log('Detected transfer call during active call. Handling transfer...', {
                activeSession: this.activeCallSessionId,
                transferSession: session.id,
                transferIdentity: incomingIdentity
            });
            this.handleTransferCall(session, incomingIdentity, callerName, callerNumber);
            return;
        }
        
        if (hasActiveCall && this.shouldReplacePendingSession(session, incomingIdentity, callerNumber)) {
            this.replacePendingSession(session, incomingIdentity, callerName, callerNumber);
            return;
        }
        
        if (hasActiveCall) {
            this.handleParallelInviteDuringActiveCall(session, incomingIdentity);
            return;
        }
        
        // Extract call identifiers from session
        const identity = this.extractSessionIdentity(session);
        const callId = identity.callId || session.id; // Use identity.callId if available, fallback to session.id
        const callSid = identity.twilioCallSid;
        
        // Mock customer data (in production, fetch from CRM)
        const customerId = callerNumber;
        
        this.currentSession = session;
        this.activeCallSessionId = session.id;
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
        
        // Show customer popup with call identifiers
        this.showCustomerPopup(customerId, callSid, callId);
        
        // Play ringtone
        this.ringTone.play();
        
        this.attachSessionEventHandlers(session, 'inbound');
    }

    extractSessionIdentity(session) {
        if (!session) {
            return { callId: null, twilioCallSid: null, fromTag: null };
        }
        const request = session.request || {};
        const getHeader = typeof request.getHeader === 'function'
            ? request.getHeader.bind(request)
            : () => null;
        return {
            callId: request.call_id || request.callId || session.id,
            twilioCallSid: getHeader('X-Twilio-CallSid'),
            fromTag: request.from_tag || null
        };
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

    isTransferCall(session, identity) {
        // A transfer call is identified by:
        // 1. We have an active call (required for transfer)
        // 2. Has X-Twilio-CallSid header (from Twilio)
        // 3. It's a new call (different Call-ID than active call)
        
        // Must have an active call for it to be a transfer
        if (!this.activeCallSessionId || !this.activeCallIdentity) {
            return false;
        }
        
        // Must have Twilio Call SID (from extractSessionIdentity)
        if (!identity || !identity.twilioCallSid) {
            return false;
        }
        
        // Must be a different call than the active one
        if (identity.callId && this.activeCallIdentity.callId && 
            identity.callId === this.activeCallIdentity.callId) {
            return false; // Same call, not a transfer
        }
        
        // If we have an active call and this is a Twilio call with different Call-ID, it's likely a transfer
        return true;
    }

    async handleTransferCall(session, identity, callerName, callerNumber) {
        console.log('Handling transfer call (manual answer):', {
            transferSession: session.id,
            transferIdentity: identity,
            activeSession: this.activeCallSessionId
        });
        
        // Store the original call session before replacing it
        const originalSession = this.currentSession;
        const originalCallSessionId = this.activeCallSessionId;
        
        const callId = identity.callId || session.id;
        const callSid = identity.twilioCallSid;
        
        // Update current session and call to transfer call
        this.currentSession = session;
        this.currentCall = {
            call_id: callId,
            caller_number: callerNumber,
            caller_name: callerName,
            customer_id: callerNumber,
            direction: 'transfer'
        };
        this.pendingTransferIdentity = identity;
        this.activeCallSessionId = session.id;
        this.activeCallIdentity = identity;
        
        // Notify backend
        fetch('/call-center/api/call/ringing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(this.currentCall)
        });
        
        // Show customer popup for transfer call
        const customerId = callerNumber;
        this.showCustomerPopup(customerId, callSid, callId);
        
        // End the original call first (if it exists)
        if (originalSession && originalCallSessionId) {
            console.log('Ending original call before handling transfer call', {
                originalSessionId: originalSession.id
            });
            try {
                if (typeof originalSession.terminate === 'function') {
                    originalSession.terminate();
                }
            } catch (endError) {
                console.warn('Error ending original call:', endError);
            }
        }
        
        // Update UI and allow manual answer
        this.showIncomingCall(callerName, callerNumber);
        
        // Play ringtone
        this.ringTone.play();
        
        // Attach event handlers
        this.attachSessionEventHandlers(session, 'transfer');
    }

    handleParallelInviteDuringActiveCall(session, identity) {
        console.warn('Already handling an active call. Ignoring parallel incoming session.', {
            activeSession: this.activeCallSessionId,
            incomingSession: session.id,
            incomingIdentity: identity,
            activeIdentity: this.activeCallIdentity
        });
        session.on('failed', () => console.log('Ignored parallel session failed', session.id));
        session.on('ended', () => console.log('Ignored parallel session ended', session.id));
    }

    isSessionPending(session) {
        if (!session) {
            return false;
        }
        if (typeof session.isEstablished === 'function' && session.isEstablished()) {
            return false;
        }
        if (typeof session.isEnded === 'function' && session.isEnded()) {
            return false;
        }
        const status = session.status;
        if (typeof JsSIP !== 'undefined' && JsSIP.RTCSession && JsSIP.RTCSession.C) {
            const C = JsSIP.RTCSession.C;
            if (status === C.STATUS_CONFIRMED || status === C.STATUS_TERMINATED) {
                return false;
            }
        }
        return true;
    }

    shouldReplacePendingSession(session, identity, callerNumber) {
        if (!this.currentSession || !this.currentCall) {
            return false;
        }
        if (!this.isSessionPending(this.currentSession)) {
            return false;
        }
        if (!callerNumber || !this.currentCall.caller_number) {
            return false;
        }
        if (callerNumber !== this.currentCall.caller_number) {
            return false;
        }
        if (this.activeCallIdentity && identity && identity.callId && this.activeCallIdentity.callId === identity.callId) {
            return false;
        }
        return true;
    }

    replacePendingSession(session, identity, callerName, callerNumber) {
        console.warn('Replacing pending incoming session with newer INVITE', {
            oldSession: this.currentSession ? this.currentSession.id : null,
            newSession: session.id
        });
        try {
            if (this.currentSession && typeof this.currentSession.terminate === 'function') {
                this.currentSession.terminate({
                    status_code: 487,
                    reason_phrase: 'Replaced by newer INVITE'
                });
            }
        } catch (error) {
            console.warn('Unable to terminate pending session', error);
        }
        
        const callId = identity.callId || session.id;
        const callSid = identity.twilioCallSid;
        this.currentSession = session;
        this.activeCallSessionId = session.id;
        this.activeCallIdentity = identity;
        this.currentCall = {
            call_id: callId,
            caller_number: callerNumber,
            caller_name: callerName,
            customer_id: callerNumber,
            direction: 'inbound'
        };
        
        // Notify backend
        fetch('/call-center/api/call/ringing', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(this.currentCall)
        });
        
        this.showIncomingCall(callerName, callerNumber);
        this.showCustomerPopup(callerNumber, callSid, callId);
        this.ringTone.play();
        this.attachSessionEventHandlers(session, 'inbound');
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
            
            // If this is a transfer call and we have an original call, end it first
            if (this.originalCallBeforeTransfer && this.originalCallBeforeTransfer.session) {
                const originalSession = this.originalCallBeforeTransfer.session;
                console.log('Ending original call before answering transfer call', {
                    originalSessionId: originalSession.id,
                    transferSessionId: session.id
                });
                try {
                    if (typeof originalSession.terminate === 'function') {
                        originalSession.terminate();
                    }
                } catch (endError) {
                    console.warn('Error ending original call:', endError);
                }
                // Clear the original call reference
                this.originalCallBeforeTransfer = null;
            }
            
            const stream = await this.ensureLocalAudioStream();
            
            await session.answer(this.buildSessionOptions(stream));
            console.log('Answer sent for session', session.id);
            
            // Update active session to the answered call
            this.activeCallSessionId = session.id;
            this.activeCallIdentity = this.pendingTransferIdentity || this.extractSessionIdentity(session);
            
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
    
    async showCustomerPopup(customerId, callSid = null, callId = null) {
        this.customerPopup.classList.add('active');
        this.customerData.innerHTML = '<div class="customer-info loading"><i class="fas fa-spinner fa-spin"></i> Loading customer data...</div>';
        
        try {
            // Build query parameters
            const params = new URLSearchParams();
            if (this.agent && this.agent.sip_extension) {
                params.append('extension', this.agent.sip_extension);
            }
            if (callSid) {
                params.append('call_sid', callSid);
            }
            if (callId) {
                params.append('call_id', callId);
            }
            if (customerId) {
                params.append('customer_id', customerId);
            }
            
            const response = await fetch(`/call-center/api/customer/data?${params.toString()}`);
            const customer = await response.json();
            
            this.displayCustomerData(customer, this.customerData);
            
            // Store customer data for info window
            this.currentCustomerData = customer;
            this.currentCallSid = callSid;
            this.currentCallId = callId;
        } catch (error) {
            console.error('Fetch customer data error:', error);
            this.customerData.innerHTML = '<div class="customer-info"><p>Failed to load customer data</p></div>';
        }
    }
    
    displayCustomerData(customer, containerElement) {
        // Build conversation history HTML
        let conversationHtml = '';
        if (customer.conversation_history && customer.conversation_history.length > 0) {
            conversationHtml = '<div class="conversation-section"><h4>Conversation History</h4><div class="conversation-list">';
            customer.conversation_history.forEach(msg => {
                const roleClass = msg.role === 'user' ? 'user-message' : 'assistant-message';
                conversationHtml += `<div class="conversation-item ${roleClass}">
                    <span class="role-badge">${msg.role === 'user' ? '👤 User' : '🤖 Assistant'}</span>
                    <div class="message-content">${this.escapeHtml(msg.content)}</div>
                </div>`;
            });
            conversationHtml += '</div></div>';
        }
        
        // Build activities HTML
        let activitiesHtml = '';
        if (customer.activities && customer.activities.length > 0) {
            activitiesHtml = '<div class="activities-section"><h4>Recent Activities</h4><div class="activities-list">';
            customer.activities.forEach(activity => {
                const activityIcon = activity.activity_type === 'calendar_event' ? '📅' : 
                                   activity.activity_type === 'todo' ? '✅' : 
                                   activity.activity_type === 'mortgage' ? '🏠' : '🔧';
                activitiesHtml += `<div class="activity-item">
                    <span class="activity-icon">${activityIcon}</span>
                    <div class="activity-content">
                        <div class="activity-title">${this.escapeHtml(activity.title || activity.tool || 'Activity')}</div>
                        <div class="activity-details">${this.escapeHtml(activity.result || '')}</div>
                    </div>
                </div>`;
            });
            activitiesHtml += '</div></div>';
        }
        
        const html = `
            <div class="customer-info">
                <div class="customer-field">
                    <label>Customer ID:</label>
                    <span>${this.escapeHtml(customer.customer_id || 'N/A')}</span>
                </div>
                <div class="customer-field">
                    <label>Name:</label>
                    <span>${this.escapeHtml(customer.name || 'N/A')}</span>
                </div>
                <div class="customer-field">
                    <label>Email:</label>
                    <span>${this.escapeHtml(customer.email || 'N/A')}</span>
                </div>
                <div class="customer-field">
                    <label>Phone:</label>
                    <span>${this.escapeHtml(customer.phone || 'N/A')}</span>
                </div>
                <div class="customer-field">
                    <label>Account Status:</label>
                    <span>${this.escapeHtml(customer.account_status || 'N/A')}</span>
                </div>
                <div class="customer-field">
                    <label>Tier:</label>
                    <span>${this.escapeHtml(customer.tier || 'N/A')}</span>
                </div>
                ${customer.last_contact ? `<div class="customer-field">
                    <label>Last Contact:</label>
                    <span>${this.escapeHtml(customer.last_contact)}</span>
                </div>` : ''}
                ${customer.open_tickets ? `<div class="customer-field">
                    <label>Open Tickets:</label>
                    <span>${customer.open_tickets}</span>
                </div>` : ''}
                ${customer.lifetime_value ? `<div class="customer-field">
                    <label>Lifetime Value:</label>
                    <span>${this.escapeHtml(customer.lifetime_value)}</span>
                </div>` : ''}
                ${customer.notes ? `<div class="customer-field">
                    <label>Notes:</label>
                    <span>${this.escapeHtml(customer.notes)}</span>
                </div>` : ''}
                ${conversationHtml}
                ${activitiesHtml}
            </div>
        `;
        
        containerElement.innerHTML = html;
    }
    
    escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    hideCustomerPopup() {
        this.customerPopup.classList.remove('active');
    }
    
    showIncomingCall(callerName, callerNumber) {
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
        console.log('Call established');
        
        this.callStartTime = Date.now();
        this.startCallDurationTimer();
        
        this.answerBtn.disabled = true;
        this.holdBtn.disabled = false;
        this.transferBtn.disabled = false;
        this.hangupBtn.disabled = false;
        
        this.updateAgentStatus('on-call');
        
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
        
        // Open customer info window if not already open
        if (!this.customerInfoWindow || !this.customerInfoWindow.classList.contains('active')) {
            this.showCustomerInfoWindow();
        }
    }
    
    onCallEnded() {
        console.log('Call ended');
        
        this.stopCallDurationTimer();
        
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
        
        // Close customer info window when call ends
        this.hideCustomerInfoWindow();
        
        this.currentCall = null;
        this.currentSession = null;
        this.pendingDialNumber = null;
        this.answerInProgress = false;
        this.activeCallSessionId = null;
        this.activeCallIdentity = null;
        this.pendingTransferIdentity = null;
        this.pendingTransferSession = null;
        this.pendingTransferCall = null;
        this.currentCustomerData = null;
        this.currentCallSid = null;
        this.currentCallId = null;
        
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
    
    async showCustomerInfoWindow() {
        if (!this.customerInfoWindow) {
            console.warn('Customer info window element not found');
            return;
        }
        
        this.customerInfoWindow.classList.add('active');
        
        // If we have stored customer data, display it
        if (this.currentCustomerData) {
            this.displayCustomerData(this.currentCustomerData, this.customerInfoData);
        } else {
            // Otherwise, fetch it
            this.customerInfoData.innerHTML = '<div class="customer-info loading"><i class="fas fa-spinner fa-spin"></i> Loading customer data...</div>';
            
            try {
                const params = new URLSearchParams();
                if (this.agent && this.agent.sip_extension) {
                    params.append('extension', this.agent.sip_extension);
                }
                if (this.currentCallSid) {
                    params.append('call_sid', this.currentCallSid);
                }
                if (this.currentCallId) {
                    params.append('call_id', this.currentCallId);
                }
                
                const response = await fetch(`/call-center/api/customer/data?${params.toString()}`);
                const customer = await response.json();
                
                this.displayCustomerData(customer, this.customerInfoData);
                this.currentCustomerData = customer;
            } catch (error) {
                console.error('Fetch customer data error:', error);
                this.customerInfoData.innerHTML = '<div class="customer-info"><p>Failed to load customer data</p></div>';
            }
        }
    }
    
    hideCustomerInfoWindow() {
        if (this.customerInfoWindow) {
            this.customerInfoWindow.classList.remove('active');
        }
    }
    
    initModalDragAndResize() {
        // Make modals draggable
        const modals = [this.customerPopup, this.customerInfoWindow].filter(m => m);
        
        modals.forEach(modal => {
            const header = modal.querySelector('.modal-header');
            if (!header) return;
            
            let isDragging = false;
            let currentX = 0;
            let currentY = 0;
            let initialX = 0;
            let initialY = 0;
            
            header.addEventListener('mousedown', (e) => {
                if (e.target.classList.contains('close-modal')) return;
                
                isDragging = true;
                initialX = e.clientX - currentX;
                initialY = e.clientY - currentY;
                
                const rect = modal.getBoundingClientRect();
                currentX = rect.left;
                currentY = rect.top;
            });
            
            document.addEventListener('mousemove', (e) => {
                if (!isDragging) return;
                
                e.preventDefault();
                currentX = e.clientX - initialX;
                currentY = e.clientY - initialY;
                
                modal.style.left = currentX + 'px';
                modal.style.top = currentY + 'px';
                modal.style.right = 'auto';
                modal.style.bottom = 'auto';
            });
            
            document.addEventListener('mouseup', () => {
                isDragging = false;
            });
        });
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

