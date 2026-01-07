# Mortgage Bot Voice Integration Guide

## Overview

The mortgage bot **uses the same WebRTC voice assistant route** as the todo bot. The system automatically detects mortgage intent and switches to the appropriate agent.

## Voice Assistant Route

**Single Route for Both Bots:**
- **URL:** `/webrtc/voice-assistant`
- **File:** `convonet/webrtc_voice_server.py` (line 316)
- **Template:** `templates/webrtc_voice_assistant.html`

## How It Works

### Automatic Intent Detection

The system automatically detects when a user wants to use the mortgage bot:

1. **User speaks:** "I want to apply for a mortgage"
2. **Intent Detection:** `detect_mortgage_intent()` checks for mortgage keywords
3. **Agent Selection:** System switches to `MortgageAgent` with mortgage tools
4. **Response:** Bot responds with mortgage-specific guidance

### Mortgage Keywords

The following keywords trigger mortgage mode:
- "mortgage"
- "apply for mortgage"
- "mortgage application"
- "pre-approved" / "pre approved"
- "home loan" / "house loan"
- "credit score"
- "DTI ratio" / "debt to income"
- "down payment"
- "closing costs"
- "loan amount"
- "property value"
- "mortgage documents"
- "W-2" / "pay stub" / "tax return"
- "bank statement"
- "mortgage approval"

### Code Flow

```
User Input (Voice/Text)
    ↓
detect_mortgage_intent(prompt)
    ↓
agent_type = "mortgage" if detected else "todo"
    ↓
_get_agent_graph(agent_type=agent_type)
    ↓
Filter tools (mortgage tools for mortgage agent, todo tools for todo agent)
    ↓
Create MortgageAgent or TodoAgent
    ↓
Process with appropriate agent
```

## Implementation Details

### 1. Intent Detection

**File:** `convonet/mortgage_intent_detection.py`

```python
from convonet.mortgage_intent_detection import detect_mortgage_intent

# In _run_agent_async()
agent_type = "mortgage" if detect_mortgage_intent(prompt) else "todo"
```

### 2. Agent Selection

**File:** `convonet/routes.py` (line ~1531)

```python
async def _run_agent_async(prompt: str, ...):
    # Detect mortgage intent
    agent_type = "mortgage" if detect_mortgage_intent(prompt) else "todo"
    
    # Get appropriate agent graph
    agent_graph = await _get_agent_graph(user_id=user_id, agent_type=agent_type)
```

### 3. Tool Filtering

**File:** `convonet/routes.py` (line ~1351)

When `agent_type="mortgage"`:
- Only mortgage MCP tools are included
- Todo/team/calendar tools are excluded

When `agent_type="todo"`:
- Only todo/team/calendar tools are included
- Mortgage tools are excluded

### 4. Agent Creation

**File:** `convonet/routes.py` (line ~1376)

```python
if agent_type == "mortgage":
    agent = MortgageAgent(tools=mortgage_tools, provider=provider, model=model)
else:
    agent = TodoAgent(tools=todo_tools, provider=provider, model=model)
```

## Usage Examples

### Example 1: Mortgage Application

**User:** "I want to apply for a mortgage"

**System:**
1. Detects mortgage intent
2. Switches to MortgageAgent
3. Uses mortgage tools only

**Bot:** "Great! Let's start by reviewing your financial situation. Do you know your current credit score?"

### Example 2: Todo Task

**User:** "Create a todo for grocery shopping"

**System:**
1. No mortgage intent detected
2. Uses TodoAgent (default)
3. Uses todo/team/calendar tools

**Bot:** "I've created a todo for grocery shopping."

### Example 3: Mixed Conversation

**User:** "I want to apply for a mortgage"

**System:** Switches to MortgageAgent

**User:** "What are my todos?"

**System:** 
- Still uses MortgageAgent (session-based)
- But MortgageAgent doesn't have todo tools
- Bot: "I'm currently helping you with your mortgage application. Would you like to continue with that, or switch to managing your todos?"

**Note:** For better UX, you might want to add explicit switching commands like "switch to todos" or "switch to mortgage".

## Session-Based Agent Selection

Currently, the agent type is determined per request based on intent detection. For a better user experience, you could:

1. **Store agent preference in session:**
   ```python
   # In Redis session
   session_data['agent_type'] = 'mortgage'
   ```

2. **Allow explicit switching:**
   ```python
   if "switch to mortgage" in prompt.lower():
       agent_type = "mortgage"
   elif "switch to todos" in prompt.lower():
       agent_type = "todo"
   ```

3. **Persist agent type across conversation:**
   - Once mortgage intent is detected, keep using MortgageAgent for that session
   - Until user explicitly switches or starts a new session

## Testing

### Test Mortgage Intent Detection

```python
from convonet.mortgage_intent_detection import detect_mortgage_intent

assert detect_mortgage_intent("I want to apply for a mortgage") == True
assert detect_mortgage_intent("What's my credit score?") == True
assert detect_mortgage_intent("Create a todo") == False
```

### Test Voice Assistant

1. Go to `/webrtc/voice-assistant`
2. Authenticate with PIN
3. Say: "I want to apply for a mortgage"
4. System should switch to MortgageAgent
5. Bot should respond with mortgage-specific guidance

## Summary

✅ **Same Route:** Both bots use `/webrtc/voice-assistant`  
✅ **Automatic Detection:** System detects mortgage intent automatically  
✅ **Tool Filtering:** Only relevant tools are loaded for each agent  
✅ **Seamless Switching:** Users don't need to navigate to different pages  
✅ **Session Management:** Agent type can be persisted in session for better UX
