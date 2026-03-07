# Call Center Features Documentation

## Overview

This call center module provides a complete browser-based SIP phone client with ACD (Automatic Call Distribution) capabilities, similar to enterprise solutions like Linphone, but with integrated call center management features.

## Core Features

### 1. Agent Management

#### Login/Logout
- ✅ Secure agent authentication
- ✅ SIP credential storage
- ✅ Session management
- ✅ Activity logging
- ✅ Automatic reconnection on disconnect

#### Agent States
The system supports standard ACD agent states:

| State | Description | Accepts Calls |
|-------|-------------|---------------|
| **Logged Out** | Agent not active | ❌ |
| **Logged In** | Agent logged in but not ready | ❌ |
| **Ready** | Available for calls | ✅ |
| **Not Ready** | On break or unavailable | ❌ |
| **On Call** | Currently on a call | ❌ |
| **Wrap Up** | After-call work | ❌ |

#### State Tracking
- Real-time status updates
- Time-in-state tracking
- State transition history
- Reason codes for Not Ready state

### 2. Call Handling

#### Incoming Calls

**Event: Ringing**
```javascript
// When a call arrives
- Visual and audio notification (ringtone)
- Caller ID display
- Customer data popup
- Accept/Reject options
```

**Features:**
- ✅ Caller number display
- ✅ Caller name display
- ✅ Ring tone playback
- ✅ Customer data retrieval
- ✅ Call queue position (when integrated)
- ✅ Priority handling

#### Outbound Calls

**Dialpad Interface**
- Standard 12-key dialpad (0-9, *, #)
- Click-to-dial functionality
- Number formatting
- Dial history

**Features:**
- ✅ Manual dialing
- ✅ Click-to-call from customer records
- ✅ Speed dial support (extensible)
- ✅ International number support

#### Active Call Controls

**Request Answer**
```
Function: answerCall()
Accepts incoming ringing call
Establishes media stream
Updates agent state to "On Call"
```

**Request Drop Call**
```
Function: hangupCall()
Terminates active call
Updates call record
Returns agent to Ready state
Records call duration
```

**Call Hold**
```
Function: holdCall()
Puts call on hold (music on hold)
Allows agent to handle other tasks
Maintains call session
```

**Call Unhold**
```
Function: unholdCall()
Resumes held call
Restores audio stream
```

**Call Transfer**

*Blind Transfer*
- Transfers call without consultation
- Immediate handoff to destination
- Agent returns to Ready state

*Attended Transfer*
- Consult with transfer destination first
- Complete transfer after confirmation
- Can return call if destination unavailable

### 3. Customer Data Integration

#### Customer Popup

**Triggered When:**
- Incoming call rings
- Before agent answers

**Information Displayed:**
- Customer ID
- Full name
- Contact information (email, phone)
- Account status
- Customer tier/segment
- Last contact date
- Open tickets/cases
- Lifetime value
- Agent notes

**CRM Integration Points:**
```python
GET /call-center/api/customer/<customer_id>
```

**Voice assistant → extension 2001 (SuiteCRM context):**  
When a caller’s request is handled by the voice assistant and SuiteCRM creates cases, contacts, meetings (schedule), or notes, that context is shown in the call-center UI after transfer to extension 2001:

1. **Caching on transfer:** The voice/WebRTC backend builds a customer profile (including `suitecrm_context`: Contact ID, Case ID, Appointment/Meeting ID, Note ID) and stores it in Redis under `callcenter:customer:{extension}` and, when available, `callcenter:customer:{extension}:{call_sid}` or `callcenter:customer:{extension}:{call_id}`.
2. **Lookup when the call arrives:** The agent’s UI requests customer data with `extension` (e.g. 2001) and, when present, `call_sid` or `call_id`. The backend returns the cached profile (or falls back to SuiteCRM lookup by caller phone).
3. **Display:** The customer popup shows a **“Voice Assistant Session (SuiteCRM)”** section with Contact ID, Appointment ID, Case ID, and Note ID so the agent can see what was created and open the records in SuiteCRM if needed.

**Extensible Fields:**
- Custom CRM fields
- Product ownership
- Service history
- Preferences
- Special requirements

### 4. User Interface

#### Dashboard Layout

**Header Section**
- Agent name and extension
- SIP connection status
- Current time
- Logout button

**Agent Status Panel**
- Ready/Not Ready buttons
- Current state display
- Time in state counter
- Visual status indicators

**Call Control Panel**
- Active call information
- Caller details
- Call duration timer
- Control buttons (Answer, Hold, Transfer, Hangup)

**Dialpad Panel**
- Number input display
- 12-key dialpad
- Call and Clear buttons

**Call History Panel**
- Recent calls list
- Call direction indicators
- Duration and outcome
- Click to view details

#### Responsive Design
- ✅ Desktop optimized (1400px+)
- ✅ Tablet support (768px - 1400px)
- ✅ Mobile friendly (< 768px)
- ✅ Modern UI with gradients and shadows
- ✅ Color-coded status indicators

### 5. SIP Integration

#### SIP.js Library
- Browser-based SIP client
- WebRTC audio support
- WebSocket transport (WSS)
- RFC 3261 compliant

#### Supported Codecs
- G.711 (PCMU, PCMA)
- Opus
- G.722
- Other WebRTC-compatible codecs

#### Features
- ✅ SIP REGISTER
- ✅ SIP INVITE/CANCEL
- ✅ SIP BYE
- ✅ SIP REFER (for transfers)
- ✅ SIP INFO (DTMF)
- ✅ Session timers
- ✅ NAT traversal (STUN/TURN)

### 6. Database & Persistence

#### Tables

**cc_agents**
- Agent profile
- SIP credentials
- Current state
- Activity timestamps

**cc_calls**
- Call records
- Caller information
- Customer data snapshot
- Call states and timestamps
- Duration and disposition

**cc_agent_activities**
- State transitions
- Login/logout events
- Activity timeline
- Metadata and reasons

### 7. API Architecture

#### RESTful Endpoints

**Agent Operations**
```
POST /api/agent/login          - Authenticate and start session
POST /api/agent/logout         - End session
POST /api/agent/ready          - Set available
POST /api/agent/not-ready      - Set unavailable
GET  /api/agent/status         - Get current state
```

**Call Operations**
```
POST /api/call/ringing         - New call event
POST /api/call/answer          - Accept call
POST /api/call/drop            - Hang up
POST /api/call/hold            - Put on hold
POST /api/call/unhold          - Resume call
POST /api/call/transfer        - Transfer call
```

**Customer Data**
```
GET  /api/customer/<id>        - Fetch customer info
```

### 8. Real-time Features

#### WebSocket Communication
- Real-time SIP signaling
- Instant state updates
- Live call events
- Push notifications

#### Event Listeners
```javascript
- onInvite: Incoming call
- onEstablished: Call connected
- onTerminated: Call ended
- stateChange: Agent state updates
```

### 9. Security Features

#### Authentication
- Session-based authentication
- SIP credentials encryption
- Secure password storage (hashed)

#### Transport Security
- HTTPS required for production
- WSS (WebSocket Secure) for SIP
- SRTP for media encryption

#### Data Protection
- CSRF protection
- XSS prevention
- SQL injection prevention (SQLAlchemy ORM)

### 10. Monitoring & Reporting

#### Agent Metrics
- Total calls handled
- Average call duration
- Time in each state
- Availability percentage

#### Call Metrics
- Total calls (inbound/outbound)
- Answer rate
- Average wait time
- Call disposition breakdown

#### Real-time Monitoring
- Active agents count
- Calls in progress
- Queue status
- System health

## Advanced Features (Extensible)

### Call Recording
- Automatic call recording
- On-demand recording
- Storage integration (S3, local)
- Playback interface

### Call Queuing
- Skill-based routing
- Priority queuing
- Queue position announcement
- Overflow handling

### Conference Calling
- Multi-party calls
- Add/remove participants
- Conference moderator controls

### IVR Integration
- Menu navigation
- Self-service options
- Intelligent routing
- Callback queue

### Analytics Dashboard
- Real-time dashboards
- Historical reports
- Performance metrics
- Custom report builder

### Supervisor Features
- Monitor calls
- Whisper mode
- Barge-in capability
- Agent coaching

## Technical Specifications

### Browser Compatibility
- ✅ Chrome 90+
- ✅ Firefox 88+
- ✅ Edge 90+
- ✅ Safari 14+
- ⚠️  Mobile browsers (limited)

### Network Requirements
- Minimum bandwidth: 100 kbps per call
- Recommended: 200 kbps per call
- Latency: < 150ms for good quality
- Jitter: < 30ms

### Server Requirements
- Python 3.8+
- PostgreSQL 12+
- SIP server with WebSocket support
- SSL/TLS certificates

## Use Cases

### Customer Support Centers
- Handle support calls
- Access customer history
- Create tickets
- Escalate issues

### Sales Teams
- Outbound calling campaigns
- Lead qualification
- CRM integration
- Call disposition tracking

### Help Desks
- Technical support
- Ticket management
- Screen sharing integration
- Remote assistance

### Collections
- Payment reminders
- Payment arrangement
- Compliance recording
- Disposition codes

## Comparison with Linphone

| Feature | This Module | Linphone |
|---------|-------------|----------|
| SIP Client | ✅ Browser-based | ✅ Native app |
| ACD Features | ✅ Built-in | ❌ Limited |
| Customer Popup | ✅ Integrated | ❌ Not available |
| Web Interface | ✅ Full-featured | ⚠️  Basic |
| Call Center Stats | ✅ Real-time | ❌ Not available |
| Agent States | ✅ Complete | ❌ Not available |
| CRM Integration | ✅ API ready | ⚠️  Manual |
| No Installation | ✅ Browser-only | ❌ Requires install |

## Future Enhancements

### Planned Features
- [ ] Video calling support
- [ ] Screen sharing
- [ ] Chat/messaging
- [ ] Mobile app (React Native)
- [ ] AI-powered call routing
- [ ] Sentiment analysis
- [ ] Voice analytics
- [ ] Multilingual support
- [ ] Chatbot integration
- [ ] Calendar integration

### Integration Roadmap
- [ ] Salesforce integration
- [ ] HubSpot integration
- [ ] Zendesk integration
- [ ] Microsoft Teams integration
- [ ] Slack notifications
- [ ] Email integration

## Best Practices

### For Agents
1. Always log out when leaving
2. Use appropriate Not Ready reasons
3. Complete after-call work promptly
4. Review customer data before answering
5. Use headsets for better audio quality

### For Administrators
1. Monitor system regularly
2. Keep SIP server updated
3. Review call metrics weekly
4. Train agents on new features
5. Backup database regularly

### For Developers
1. Follow security best practices
2. Test with real SIP servers
3. Monitor performance metrics
4. Keep dependencies updated
5. Document customizations

## Support & Resources

- **Documentation**: README.md
- **Quick Start**: QUICKSTART.md
- **Configuration**: config.example.py
- **Database Setup**: init_db.py
- **API Reference**: routes.py

---

Built with ❤️ using Flask, SIP.js, and modern web technologies.

