import asyncio
import logging
import os
import time
import traceback
from langchain_core.tools import BaseTool
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_anthropic import ChatAnthropic
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph import StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.checkpoint.memory import InMemorySaver
from typing import List, Optional, Literal
from dotenv import load_dotenv

from .state import AgentState
from .mcps.local_servers.db_todo import TodoPriority, ReminderImportance
from .llm_provider_manager import get_llm_provider_manager, LLMProvider
from .mortgage_prompts import MORTGAGE_SYSTEM_PROMPT
# Optional Composio imports - app should work without them
try:
    from .composio_tools import get_all_integration_tools, test_composio_connection
    COMPOSIO_AVAILABLE = True
except ImportError as e:
    print(f"⚠️ Composio not available: {e}")
    COMPOSIO_AVAILABLE = False
    def get_all_integration_tools():
        return []
    def test_composio_connection():
        return False
from .redis_manager import redis_manager


load_dotenv()

# Configure logging to suppress HTTP request logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)


class TodoAgent:
    def __init__(
            self,
            name: str = "Convonet Assistant",
            model: str = None,
            provider: Optional[LLMProvider] = None,
            tools: List[BaseTool] = [],
            system_prompt: str = """You are a productivity assistant that helps users manage todos, reminders, and calendar events. You MUST use tools to perform actions - never just ask for more information.

            CRITICAL RULES:
            1. ALWAYS use tools when users mention creating, adding, or doing something
            2. NEVER ask "what would you like to create?" - infer the intent and use the appropriate tool
            3. Be proactive - if user says "create", "add", "todo", "reminder", "calendar event", immediately use the tool
            4. Make reasonable assumptions when details are missing

            Your messages are read aloud, so be brief and conversational.

            TOOL USAGE GUIDELINES:
            
            PERSONAL PRODUCTIVITY:
            - "create a todo" / "add a task" → use create_todo immediately
            - "create a reminder" → use create_reminder immediately
            - "create/schedule a meeting" / "calendar event" → use create_calendar_event immediately
            - "what are my todos?" / "show todos" → use get_todos immediately
            - "mark [todo] as completed" / "complete [todo]" → use complete_todo immediately
            - "delete todo/reminder/event" → use delete_todo/delete_reminder/delete_calendar_event immediately
            - "update todo/reminder/event" → use update_todo/update_reminder/update_calendar_event immediately
            
            EXTERNAL INTEGRATIONS (via Composio):
            - "send a Slack message" / "message the team" → use Slack tools
            - "create a GitHub issue" / "open a ticket" → use GitHub tools
            - "send an email" / "email someone" → use Gmail tools
            - "create a Notion page" / "add to Notion" → use Notion tools
            - "create a Jira ticket" / "log a bug" → use Jira tools
            - "sync calendar" → use sync_google_calendar_events immediately
            
            TEAM MANAGEMENT:
            - "create a team" / "create [name] team" → use create_team immediately
            - "what teams" / "show teams" / "list teams" → use get_teams immediately
            - "who is in [team]" / "show team members" → use get_team_members immediately
            
            MEMBERSHIP MANAGEMENT:
            - "add [email] to [team]" / "add [email] to [team] as [role]" → use add_team_member immediately
            - "remove [email] from [team]" → use remove_team_member immediately
            - "change [email] to [role]" / "promote [email] to [role]" → use change_member_role immediately
            - "search for [name]" / "find user [name]" → use search_users immediately
            
            TEAM TODO MANAGEMENT:
            - "create todo for [team]" → use create_team_todo immediately
            - "assign [task] to [person] in [team]" → FIRST get_team_members to find user, THEN use create_team_todo
            - "create [priority] todo for [team]" → use create_team_todo immediately
            
            CALL TRANSFER (VOICE CALLS ONLY):
            - "transfer me" / "speak to agent" / "talk to human" → use transfer_to_agent immediately
            - "transfer to [department]" → use transfer_to_agent with department parameter
            - "what departments" / "who can I talk to" → use get_available_departments immediately
            
            CRITICAL RULES FOR TEAM OPERATIONS:
            1. When user mentions a team name, use get_teams to find the exact team_id
            2. When assigning to a person, use get_team_members to find their user_id
            3. Always validate membership before creating team todos
            4. If team name is ambiguous, list available teams and ask for clarification
            5. Default role for new members is "member" unless specified
            
            AUTHENTICATION CONTEXT:
            - authenticated_user_id: The user who made the call (available in state)
            - creator_id: Should be set to authenticated_user_id for all created items
            - When user says "my todos", filter by creator_id or assignee_id = authenticated_user_id

            PRIORITY MAPPING (use these defaults):
            - Shopping/errands: medium priority
            - Work/business: high priority
            - Personal/hobbies: low priority
            - If no priority mentioned: medium priority
            - If no due date mentioned: ALWAYS use today's date (2025-10-17)
            
            CRITICAL: Today's date is 2025-10-17 (October 17, 2025). NEVER use dates from 2023 or 2024. Always use 2025-10-17 as the default date when no specific date is provided.

            <todo_priorities>
            {todo_priorities}
            </todo_priorities>

            <reminder_importance>
            {reminder_importance}
            </reminder_importance>

            <db_schema>
            - todos_anthropic (id, created_at, updated_at, title, description, completed, priority, due_date, google_calendar_event_id)
            - reminders_anthropic (id, created_at, updated_at, reminder_text, importance, reminder_date, google_calendar_event_id)
            - calendar_events_anthropic (id, created_at, updated_at, title, description, event_from, event_to, google_calendar_event_id)
            </db_schema>

            AVAILABLE TOOLS (use them proactively):
            
            PERSONAL PRODUCTIVITY:
            - create_todo: Create personal todos with title, description, priority, due_date
            - get_todos: Get all todos
            - complete_todo: Mark todos as done
            - update_todo: Modify todo properties
            - delete_todo: Remove todos
            - create_reminder: Create reminders with text, importance, date
            - get_reminders: Get all reminders
            - delete_reminder: Remove reminders
            - create_calendar_event: Create events with title, start/end times, description
            - get_calendar_events: Get all events
            - delete_calendar_event: Remove events
            
            TEAM COLLABORATION:
            - create_team: Create a new team with name and description
            - get_teams: List all available teams
            - get_team_members: Get members of a specific team
            - create_team_todo: Create a todo for a team (with optional assignee)
            - add_team_member: Add a user to a team by email with role
            - remove_team_member: Remove a user from a team by email
            - change_member_role: Change a team member's role
            - search_users: Search for users by name or email
            
            DATABASE:
            - query_db: Execute SQL queries
            
            WEB SEARCH (current information):
            - web_search: Search the web for current info (weather, news, rates, research). Use when user asks about real-time data or topics not in knowledge base.
            
            CALL TRANSFER (VOICE CALLS):
            - transfer_to_agent: Transfer call to human agent or department
            - get_available_departments: List available departments for transfer

            EXAMPLES:
            
            PERSONAL PRODUCTIVITY:
            User: "Create a todo for grocery shopping" 
               → IMMEDIATELY use create_todo(title="Grocery shopping", priority="medium", due_date="2025-10-17")
            
            User: "Add Costco shopping to my list" 
               → IMMEDIATELY use create_todo(title="Costco shopping", priority="medium")
            
            User: "Remind me to call mom tomorrow at 2 PM" 
               → IMMEDIATELY use create_reminder(reminder_text="Call mom", reminder_date="2025-10-01T14:00:00", importance="medium")
            
            User: "What are my todos?" 
               → IMMEDIATELY use get_todos()
            
            TEAM CREATION:
            User: "Create a development team" 
               → IMMEDIATELY use create_team(name="Development Team", description="")
            
            User: "Create a hackathon team for our project" 
               → IMMEDIATELY use create_team(name="Hackathon Team", description="Project team")
            
            TEAM DISCOVERY:
            User: "What teams are available?" 
               → IMMEDIATELY use get_teams()
            
            User: "Who is in the development team?" 
               → FIRST use get_teams() to find team_id
               → THEN use get_team_members(team_id)
            
            MEMBERSHIP MANAGEMENT:
            User: "Add john@example.com to the development team as admin" 
               → IMMEDIATELY use add_team_member(team_name="development", email="john@example.com", role="admin")
            
            User: "Remove sarah@example.com from the marketing team" 
               → IMMEDIATELY use remove_team_member(team_name="marketing", email="sarah@example.com")
            
            User: "Change john@example.com to owner role in the dev team" 
               → IMMEDIATELY use change_member_role(team_name="dev", email="john@example.com", new_role="owner")
            
            User: "Search for users named John" 
               → IMMEDIATELY use search_users(search_term="John")
            
            TEAM TODO CREATION (MULTI-STEP):
            User: "Create a high priority todo for the dev team"
               STEP 1: use get_teams() → find team_id for "dev team"
               STEP 2: use create_team_todo(title="Todo", team_id="{{found_id}}", priority="high")
            
            User: "Assign a code review task to John in the development team"
               STEP 1: use get_teams() → find team_id for "development team"
               STEP 2: use get_team_members(team_id) → find user_id for "John"
               STEP 3: use create_team_todo(
                   title="Code review task",
                   team_id="{{team_id}}",
                   assignee_id="{{john_id}}",
                   priority="medium"
               )
            
            User: "Create a demo team meeting tomorrow at 5 PM"
               → IMMEDIATELY use create_calendar_event(
                   title="Demo team meeting",
                   event_from="2025-10-18T17:00:00",
                   event_to="2025-10-18T18:00:00"
               )
            
            COMPLEX TEAM WORKFLOW:
            User: "Create a hackathon team and add admin@convonet.com as owner"
               STEP 1: use create_team(name="Hackathon Team")
               STEP 2: use add_team_member(team_name="Hackathon", email="admin@convonet.com", role="owner")
               STEP 3: Confirm "Hackathon team created and admin@convonet.com added as owner"
            
            CALL TRANSFER EXAMPLES:
            User: "I need to speak to a human"
               → IMMEDIATELY use transfer_to_agent(department="support", reason="User requested human agent")
            
            User: "Transfer me to sales"
               → IMMEDIATELY use transfer_to_agent(department="sales", reason="User requested sales department")
            
            User: "This is too complicated, I need help"
               → IMMEDIATELY use transfer_to_agent(department="support", reason="User needs additional assistance")
            
            User: "What departments can I talk to?"
               → IMMEDIATELY use get_available_departments()
            
            ROLE HIERARCHY:
            - owner: Full control (can delete team, manage all members)
            - admin: Can add/remove members, manage todos
            - member: Can create todos, participate in team
            - viewer: Read-only access to team todos

            Remember: ACT FIRST, ASK LATER. Use tools immediately when you understand the user's intent.
            When dealing with teams, ALWAYS verify team/user existence before operations.
            """,
            ) -> None:
        self.name = name
        self.system_prompt = system_prompt
        self.tools = tools
        
        # Get provider manager
        provider_manager = get_llm_provider_manager()
        
        # Determine provider (from parameter, env var, or default)
        if provider:
            self.provider = provider
        else:
            # Try to get from environment variable
            self.provider = os.getenv("LLM_PROVIDER", "claude").lower()
            if self.provider not in ["claude", "gemini", "openai"]:
                self.provider = "claude"  # Default to Claude
        
        # Get model (provider-specific)
        self.model = model  # Will be set by provider manager if None
        
        # Validate provider is available
        if not provider_manager.is_provider_available(self.provider):
            # Fallback to default available provider
            default_provider = provider_manager.get_default_provider()
            if default_provider:
                print(f"⚠️ Provider '{self.provider}' not available, falling back to '{default_provider}'")
                self.provider = default_provider
            else:
                raise ValueError("No LLM providers are available. Please configure at least one API key.")
        
        # Create LLM using provider manager
        try:
            import sys
            print(f"🤖 Initializing {provider_manager.providers[self.provider]['name']} with provider: {self.provider}", flush=True)
            sys.stdout.flush()
            print(f"🚀 About to call provider_manager.create_llm()...", flush=True)
            sys.stdout.flush()
            self.llm = provider_manager.create_llm(
                provider=self.provider,
                model=self.model,
                temperature=0.0,
                tools=self.tools,
            )
            print(f"✅ provider_manager.create_llm() returned successfully", flush=True)
            sys.stdout.flush()
            
            # Get the actual model name used
            if hasattr(self.llm, 'model_name'):
                self.model = self.llm.model_name
            elif hasattr(self.llm, 'model'):
                self.model = self.llm.model
            
            print(f"✅ {provider_manager.providers[self.provider]['name']} initialized successfully", flush=True)
            sys.stdout.flush()
            print(f"✅ Model: {self.model}", flush=True)
            sys.stdout.flush()
            
        except Exception as e:
            error_msg = f"❌ Failed to initialize {self.provider} LLM: {e}"
            print(error_msg)
            import traceback
            print(f"❌ Full traceback:\n{traceback.format_exc()}")
            
            # Try fallback to default provider
            if self.provider != "claude":
                print(f"⚠️ Attempting fallback to Claude...")
                try:
                    self.provider = "claude"
                    self.llm = provider_manager.create_llm(
                        provider="claude",
                        model=None,
                        temperature=0.0,
                        tools=self.tools,
                    )
                    print(f"✅ Fallback to Claude successful")
                    print(f"⚠️ WARNING: Requested {self.provider} but using Claude due to initialization failure")
                except Exception as fallback_error:
                    raise Exception(f"Failed to initialize LLM with {self.provider} and fallback failed: {str(e)}")
            else:
                raise
        
        self.graph = self.build_graph()

    def build_graph(self,) -> CompiledStateGraph:
        builder = StateGraph(AgentState)

        async def assistant(state: AgentState):
            """The main assistant node that uses the LLM to generate responses."""
            # Log which provider is actually being used
            print(f"🤖 Using LLM provider: {self.provider}, model: {self.model}")
            
            # inject todo priorities and reminder importance into the system prompt
            system_prompt = self.system_prompt.format(
                todo_priorities=", ".join([p.value for p in TodoPriority]),
                reminder_importance=", ".join([i.value for i in ReminderImportance])
                )
            
            # Add Gemini-specific instructions for tool calling
            if self.provider == "gemini":
                gemini_tool_instruction = """

CRITICAL FOR GEMINI TOOL CALLING:
1. You MUST use tools when users request actions (create, add, schedule, etc.)
2. Do NOT just describe what you would do - ACTUALLY CALL THE TOOLS
3. When user says "create X", immediately call the create_X tool
4. When user says "add Y", immediately call the appropriate add/create tool
5. Tools are bound to your model - use them directly, don't ask for permission
6. If you need to create something, use the tool NOW, not later

EXAMPLE:
User: "Create a workout event for December 4th at 7PM"
You: [IMMEDIATELY call create_calendar_event tool with title="workout", event_from="2025-12-04T19:00:00", event_to="2025-12-04T20:00:00"]

DO NOT respond with text like "I'll create..." - ACTUALLY CALL THE TOOL!
"""
                system_prompt = system_prompt + gemini_tool_instruction

            print(f"🤖 Assistant processing: {state.messages[-1].content if state.messages else 'No messages'}")
            print(f"🤖 Message count: {len(state.messages)}")
            
            # Filter messages to ensure proper tool_use/tool_result pairing
            # Both Claude and OpenAI require that every tool_use is immediately followed by tool_result
            # If a tool_use doesn't have all its tool_result messages, skip it to avoid API errors
            filtered_messages = []
            i = 0
            warned_tool_ids = set()  # Track ToolMessage IDs we've already warned about to prevent log spam
            
            # Debug: Log all messages before filtering
            print(f"🔍 Messages before filtering ({len(state.messages)} total):", flush=True)
            for idx, msg in enumerate(state.messages):
                msg_type = type(msg).__name__
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    tool_ids = [getattr(tc, 'id', getattr(tc, 'tool_call_id', None)) for tc in msg.tool_calls]
                    print(f"🔍   [{idx}] {msg_type} with {len(msg.tool_calls)} tool_calls: {tool_ids}", flush=True)
                elif hasattr(msg, 'tool_call_id'):
                    print(f"🔍   [{idx}] {msg_type} with tool_call_id: {msg.tool_call_id}", flush=True)
                else:
                    content_preview = str(getattr(msg, 'content', ''))[:50] if hasattr(msg, 'content') else 'N/A'
                    print(f"🔍   [{idx}] {msg_type}: {content_preview}...", flush=True)
            while i < len(state.messages):
                msg = state.messages[i]
                
                # Check if this is a tool_use message (has tool_calls or tool_use in content)
                # Claude uses content field with tool_use items, OpenAI/Gemini use tool_calls attribute
                tool_calls_list = []
                tool_call_ids = set()
                
                # Method 1: Check tool_calls attribute (OpenAI/Gemini format)
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    # Filter out None values (sometimes tool_calls is [None])
                    valid_tool_calls = [tc for tc in msg.tool_calls if tc is not None]
                    if valid_tool_calls:
                        tool_calls_list = valid_tool_calls
                        for tc in tool_calls_list:
                            # OpenAI format: tc.id or tc['id']
                            if isinstance(tc, dict):
                                tool_id = tc.get('id') or tc.get('tool_call_id')
                            else:
                                tool_id = getattr(tc, 'id', None) or getattr(tc, 'tool_call_id', None) or getattr(tc, 'toolCallId', None)
                            if tool_id:
                                tool_call_ids.add(tool_id)
                            else:
                                # Debug: log what we found
                                print(f"⚠️ Tool call has no ID: {tc} (type: {type(tc)})", flush=True)
                                if isinstance(tc, dict):
                                    print(f"⚠️   Tool call dict keys: {list(tc.keys())}", flush=True)
                                else:
                                    print(f"⚠️   Tool call attributes: {[attr for attr in dir(tc) if not attr.startswith('_')][:10]}", flush=True)
                
                # Method 2: Check content field for tool_use items (Claude format)
                if not tool_calls_list and hasattr(msg, 'content'):
                    content = msg.content
                    # Content can be a list (Claude format) or a string
                    if isinstance(content, list):
                        for item in content:
                            # Check if this is a tool_use item
                            if isinstance(item, dict) and item.get('type') == 'tool_use':
                                tool_calls_list.append(item)
                                tool_id = item.get('id') or item.get('tool_call_id')
                                if tool_id:
                                    tool_call_ids.add(tool_id)
                
                if tool_calls_list:
                    tool_names = []
                    for tc in tool_calls_list:
                        if isinstance(tc, dict):
                            tool_names.append(tc.get('name', 'unknown'))
                        else:
                            tool_names.append(getattr(tc, 'name', getattr(tc, 'functionName', 'unknown')))
                    print(f"🤖 Found {len(tool_calls_list)} tool calls in message {i}: {tool_names}")
                    print(f"🤖 Tool call IDs: {list(tool_call_ids)}")
                    
                    if not tool_call_ids:
                        print(f"⚠️ Tool calls found but no IDs extracted - checking alternative formats...", flush=True)
                        # Try to extract IDs from content structure (Claude format)
                        if hasattr(msg, 'content') and isinstance(msg.content, list):
                            for item in msg.content:
                                if isinstance(item, dict) and item.get('type') == 'tool_use':
                                    tool_id = item.get('id')
                                    if tool_id:
                                        tool_call_ids.add(tool_id)
                                        print(f"✅ Extracted tool call ID from content: {tool_id}", flush=True)
                        
                        # Try to extract from tool_calls again with more detailed inspection
                        if not tool_call_ids and hasattr(msg, 'tool_calls') and msg.tool_calls:
                            print(f"🔍 Inspecting tool_calls structure: {msg.tool_calls}", flush=True)
                            for idx, tc in enumerate(msg.tool_calls):
                                if tc is None:
                                    print(f"⚠️   Tool call {idx} is None", flush=True)
                                    continue
                                print(f"🔍   Tool call {idx}: {tc} (type: {type(tc)})", flush=True)
                                if isinstance(tc, dict):
                                    print(f"🔍     Dict keys: {list(tc.keys())}", flush=True)
                                    # Try different key names
                                    tool_id = tc.get('id') or tc.get('tool_call_id') or tc.get('call_id')
                                    if tool_id:
                                        tool_call_ids.add(tool_id)
                                        print(f"✅ Extracted tool call ID from dict: {tool_id}", flush=True)
                                else:
                                    # Try different attribute names
                                    tool_id = getattr(tc, 'id', None) or getattr(tc, 'tool_call_id', None) or getattr(tc, 'call_id', None)
                                    if tool_id:
                                        tool_call_ids.add(tool_id)
                                        print(f"✅ Extracted tool call ID from object: {tool_id}", flush=True)
                        
                        # Last resort: Try to get IDs from following ToolMessages
                        # If tool execution succeeded, ToolMessages will have the correct tool_call_id
                        if not tool_call_ids:
                            print(f"🔍 Trying to extract tool call IDs from following ToolMessages...", flush=True)
                            j = i + 1
                            while j < len(state.messages) and j < i + 5:  # Check next 5 messages
                                next_msg = state.messages[j]
                                tool_call_id = None
                                if hasattr(next_msg, 'tool_call_id') and next_msg.tool_call_id:
                                    tool_call_id = next_msg.tool_call_id
                                elif hasattr(next_msg, 'toolCallId') and next_msg.toolCallId:
                                    tool_call_id = next_msg.toolCallId
                                elif isinstance(next_msg, ToolMessage):
                                    tool_call_id = getattr(next_msg, 'tool_call_id', None) or getattr(next_msg, 'toolCallId', None)
                                
                                if tool_call_id:
                                    tool_call_ids.add(tool_call_id)
                                    print(f"✅ Extracted tool call ID from ToolMessage at index {j}: {tool_call_id}", flush=True)
                                    break  # Found one, that's enough to proceed
                                j += 1
                        
                        if not tool_call_ids:
                            print(f"⚠️ Tool calls found but no IDs extracted - skipping message {i} and any following ToolMessages", flush=True)
                            # Skip this AIMessage with tool_calls (no IDs)
                            # Also skip any ToolMessages that immediately follow (they're orphaned)
                            j = i + 1
                        while j < len(state.messages):
                            next_msg = state.messages[j]
                            # Check if this is a tool result message - use comprehensive check
                            is_tool_result = (
                                isinstance(next_msg, ToolMessage)
                            ) or (
                                hasattr(next_msg, 'tool_call_id') and next_msg.tool_call_id
                            ) or (
                                hasattr(next_msg, 'toolCallId') and next_msg.toolCallId
                            ) or (
                                hasattr(next_msg, 'id') and hasattr(next_msg, 'content') and 
                                not hasattr(next_msg, 'tool_calls')
                            )
                            
                            if is_tool_result:
                                # This is a ToolMessage - skip it since the tool_use was skipped
                                print(f"⚠️   Also skipping orphaned ToolMessage at index {j} (tool_call_id: {getattr(next_msg, 'tool_call_id', getattr(next_msg, 'toolCallId', 'unknown'))})")
                                j += 1
                            else:
                                # Not a ToolMessage - stop skipping
                                break
                        i = j
                        continue
                    
                    # Check if all tool calls have corresponding tool_result messages immediately after
                    # CRITICAL: Both Claude and OpenAI require ALL tool_results to be in the message(s) immediately following tool_use
                    result_ids = set()
                    tool_result_messages = []  # Track all tool result messages
                    j = i + 1
                    found_all_results = False
                    
                    while j < len(state.messages):
                        next_msg = state.messages[j]
                        
                        # CRITICAL: Stop if we hit another tool_use message (AIMessage with tool_calls)
                        # This means we've moved to the next tool execution cycle
                        if hasattr(next_msg, 'tool_calls') and next_msg.tool_calls:
                            # Check if this is a valid tool_use (not just [None])
                            valid_tool_calls = [tc for tc in next_msg.tool_calls if tc is not None]
                            if valid_tool_calls:
                                # This is a new tool_use - stop looking for results
                                print(f"⚠️ Hit another tool_use at message {j} - stopping search for tool results", flush=True)
                                break
                        
                        # Check if this is a tool result message - check multiple possible attribute names
                        tool_call_id = None
                        if hasattr(next_msg, 'tool_call_id') and next_msg.tool_call_id:
                            tool_call_id = next_msg.tool_call_id
                        elif hasattr(next_msg, 'toolCallId') and next_msg.toolCallId:
                            tool_call_id = next_msg.toolCallId
                        elif isinstance(next_msg, ToolMessage):
                            tool_call_id = getattr(next_msg, 'tool_call_id', None) or getattr(next_msg, 'toolCallId', None)
                        elif hasattr(next_msg, 'id') and hasattr(next_msg, 'content'):
                            # Some ToolMessage implementations use 'id' instead of 'tool_call_id'
                            tool_call_id = getattr(next_msg, 'id', None)
                        
                        if tool_call_id:
                            # CRITICAL: Only count this as a result if it matches one of our expected tool call IDs
                            if tool_call_id in tool_call_ids:
                                result_ids.add(tool_call_id)
                                tool_result_messages.append(j)  # Track index of tool result message
                                # Avoid log spam for repeated matches
                                if tool_call_id not in warned_tool_ids:
                                    print(f"🤖 Found matching tool_result for tool_call_id: {tool_call_id}", flush=True)
                                    warned_tool_ids.add(tool_call_id)
                                
                                # Check if we've found all results
                                if tool_call_ids.issubset(result_ids):
                                    found_all_results = True
                                    j += 1  # Include this message, then stop
                                    break
                            else:
                                # This is a ToolMessage but with a different ID - likely from a previous execution
                                # Only warn once per unique ToolMessage ID to prevent log spam
                                if tool_call_id not in warned_tool_ids:
                                    print(f"⚠️ Found ToolMessage with non-matching ID: {tool_call_id} (expected: {list(tool_call_ids)})", flush=True)
                                    warned_tool_ids.add(tool_call_id)
                                # Don't add it to results, but continue looking
                        elif hasattr(next_msg, 'tool_calls') and next_msg.tool_calls:
                            # If we hit another tool_use message, stop looking for results
                            # CRITICAL: If we haven't found all results, this tool_use is incomplete - skip it
                            if not found_all_results:
                                # This tool_use doesn't have all its results - stop here
                                print(f"⚠️ Hit another tool_use at message {j} before finding all results")
                                break
                            # If we found all results, we can stop here (next tool_use will be handled separately)
                            break
                        else:
                            # This is a regular message (not tool_result, not tool_use)
                            # CRITICAL: If we haven't found all results yet, this breaks the chain - skip the tool_use
                            if not found_all_results:
                                # Regular message breaks the tool_use/tool_result chain - stop here
                                print(f"⚠️ Hit regular message at {j} before finding all results")
                                break
                            # If we found all results, we can include up to here, then stop
                            break
                    
                    # Check if all tool calls have results
                    missing_results = tool_call_ids - result_ids
                    if missing_results or not found_all_results:
                        # Skip this tool_use message and its partial results (if any)
                        # This happens when tool execution fails/times out
                        print(f"⚠️ Skipping tool_use message {i}: missing tool_result for {missing_results if missing_results else 'incomplete results'} (found {len(result_ids)}/{len(tool_call_ids)})")
                        print(f"⚠️   Expected IDs: {list(tool_call_ids)}")
                        print(f"⚠️   Found IDs: {list(result_ids)}")
                        # Skip the tool_use message and any partial tool_result messages
                        # j points to the first message after the tool_use (or after partial results)
                        i = j  # j points to the first message after the incomplete tool_use/results
                        continue
                    else:
                        print(f"✅ All tool calls have results, including message {i} and results")
                        print(f"✅   Tool call IDs: {list(tool_call_ids)}")
                        print(f"✅   Result IDs: {list(result_ids)}")
                        # Include the tool_use message
                        filtered_messages.append(state.messages[i])
                        # Include ALL tool_result messages immediately after
                        for result_idx in tool_result_messages:
                            filtered_messages.append(state.messages[result_idx])
                        i = j
                        continue
                
                # Regular message (not tool_use)
                # CRITICAL: Don't include orphaned ToolMessages (ToolMessages without preceding tool_use)
                # Check if this is a ToolMessage - use STRICT check to avoid false positives
                from langchain_core.messages import HumanMessage, AIMessage
                
                # Debug: Log message type
                msg_type = type(msg).__name__
                is_human = isinstance(msg, HumanMessage)
                is_ai = isinstance(msg, AIMessage)
                is_tool = isinstance(msg, ToolMessage)
                
                # CRITICAL: NEVER treat HumanMessage or AIMessage as ToolMessage
                # Only check for ToolMessage type - explicitly exclude HumanMessage and AIMessage
                if is_human or is_ai:
                    # This is a HumanMessage or AIMessage - NEVER skip it, NEVER treat as ToolMessage
                    is_tool_message = False
                    # Debug log to verify HumanMessage/AIMessage are not being skipped
                    if is_human:
                        print(f"✅ HumanMessage detected at index {i} - will NOT skip", flush=True)
                    elif is_ai:
                        print(f"✅ AIMessage detected at index {i} - will NOT skip", flush=True)
                else:
                    # Check if it's actually a ToolMessage (only if NOT HumanMessage or AIMessage)
                    is_tool_message = is_tool
                    # Also check for tool_call_id attribute (some ToolMessage implementations use this)
                    if not is_tool_message:
                        tool_call_id = getattr(msg, 'tool_call_id', None) or getattr(msg, 'toolCallId', None)
                        if tool_call_id:
                            # Only consider it a ToolMessage if it also has content
                            if hasattr(msg, 'content'):
                                is_tool_message = True
                
                # CRITICAL SAFETY CHECK: NEVER skip HumanMessage or AIMessage, even if detection logic fails
                if is_human or is_ai:
                    # This is definitely a HumanMessage or AIMessage - ALWAYS include it
                    filtered_messages.append(msg)
                    i += 1
                    continue
                
                if is_tool_message:
                    # This is a ToolMessage - check if there's a preceding AIMessage with tool_calls in filtered_messages
                    # Look backwards for the most recent AIMessage with tool_calls
                    has_preceding_tool_use = False
                    for k in range(len(filtered_messages) - 1, -1, -1):
                        prev_msg = filtered_messages[k]
                        if hasattr(prev_msg, 'tool_calls') and prev_msg.tool_calls:
                            # Check if any tool_call ID matches this ToolMessage's tool_call_id
                            tool_call_id = getattr(msg, 'tool_call_id', None) or getattr(msg, 'toolCallId', None)
                            if tool_call_id:
                                for tc in prev_msg.tool_calls:
                                    tc_id = getattr(tc, 'id', None) or getattr(tc, 'tool_call_id', None) or getattr(tc, 'toolCallId', None)
                                    if tc_id == tool_call_id:
                                        has_preceding_tool_use = True
                                        break
                            if has_preceding_tool_use:
                                break
                    
                    if not has_preceding_tool_use:
                        print(f"⚠️ Skipping orphaned ToolMessage at index {i} (no preceding tool_use in filtered messages)")
                        i += 1
                        continue
                
                # Regular message or ToolMessage with valid preceding tool_use - include it
                filtered_messages.append(msg)
                i += 1
            
            # CRITICAL SAFETY CHECK: Ensure we have at least one message
            # If all messages were filtered out, this will cause Claude/OpenAI API errors
            if len(filtered_messages) == 0:
                print(f"⚠️ WARNING: All messages filtered out! Re-adding original messages to prevent API error", flush=True)
                # Re-add all original messages if filtering removed everything
                # This prevents "messages: at least one message is required" errors
                filtered_messages = state.messages.copy()
                print(f"✅ Restored {len(filtered_messages)} messages to prevent API error", flush=True)
            
            # Debug: Log filtered messages
            print(f"🔍 Messages after filtering ({len(filtered_messages)} total):", flush=True)
            for idx, msg in enumerate(filtered_messages):
                msg_type = type(msg).__name__
                if hasattr(msg, 'tool_calls') and msg.tool_calls:
                    tool_ids = [getattr(tc, 'id', getattr(tc, 'tool_call_id', None)) for tc in msg.tool_calls]
                    print(f"🔍   [{idx}] {msg_type} with {len(msg.tool_calls)} tool_calls: {tool_ids}", flush=True)
                elif hasattr(msg, 'tool_call_id'):
                    print(f"🔍   [{idx}] {msg_type} with tool_call_id: {msg.tool_call_id}", flush=True)
                else:
                    content_preview = str(getattr(msg, 'content', ''))[:50] if hasattr(msg, 'content') else 'N/A'
                    print(f"🔍   [{idx}] {msg_type}: {content_preview}...", flush=True)
            
            try:
                response = await self.llm.ainvoke([SystemMessage(content=system_prompt)] + filtered_messages)
                
                # Log response details for debugging
                print(f"🤖 Assistant response type: {type(response)}")
                print(f"🤖 Assistant response content: {response.content}")
                
                # Check for tool calls - Gemini might use different attribute names/structures
                tool_calls = None
                tool_call_count = 0
                
                # Method 1: Check tool_calls attribute (standard LangChain)
                if hasattr(response, 'tool_calls') and response.tool_calls:
                    tool_calls = response.tool_calls
                    tool_call_count = len(tool_calls)
                    print(f"🤖 Found tool_calls attribute with {tool_call_count} calls")
                
                # Method 2: Check if response.content contains tool_use (Gemini format)
                elif self.provider == "gemini" and hasattr(response, 'content'):
                    # Gemini might return tool_use in content as a list
                    if isinstance(response.content, list):
                        for item in response.content:
                            if hasattr(item, 'type') and item.type == 'tool_use':
                                if tool_calls is None:
                                    tool_calls = []
                                tool_calls.append(item)
                                tool_call_count += 1
                        if tool_call_count > 0:
                            print(f"🤖 Found {tool_call_count} tool_use items in Gemini response content")
                
                # Method 3: Check response.additional_kwargs for Gemini tool calls
                if tool_call_count == 0 and hasattr(response, 'additional_kwargs'):
                    additional = response.additional_kwargs
                    if 'candidates' in additional:
                        candidates = additional['candidates']
                        if candidates and 'content' in candidates[0]:
                            content = candidates[0]['content']
                            if 'parts' in content:
                                for part in content['parts']:
                                    if 'functionCall' in part:
                                        tool_call_count += 1
                                        if tool_calls is None:
                                            tool_calls = []
                                        tool_calls.append(part['functionCall'])
                        if tool_call_count > 0:
                            print(f"🤖 Found {tool_call_count} functionCall items in Gemini additional_kwargs")
                
                if tool_calls and tool_call_count > 0:
                    print(f"✅ Tool calls detected: {tool_call_count} calls")
                    for i, tc in enumerate(tool_calls):
                        if isinstance(tc, dict):
                            name = tc.get('name', tc.get('functionName', 'unknown'))
                            print(f"🤖   Tool call {i+1}: {name}")
                        elif hasattr(tc, 'name'):
                            print(f"🤖   Tool call {i+1}: {tc.name}")
                        elif hasattr(tc, 'functionName'):
                            print(f"🤖   Tool call {i+1}: {tc.functionName}")
                        else:
                            print(f"🤖   Tool call {i+1}: {tc}")
                else:
                    print(f"⚠️ No tool calls detected (provider: {self.provider})")
                    if self.provider == "gemini":
                        print(f"⚠️ WARNING: Gemini returned no tool calls.")
                        print(f"⚠️ Response attributes: {dir(response)}")
                        if hasattr(response, 'additional_kwargs'):
                            print(f"⚠️ Additional kwargs keys: {list(response.additional_kwargs.keys())}")
                
                print(f"🤖 Available tools: {len(self.tools)}")
                print(f"🤖 Tool names: {[tool.name for tool in self.tools[:5]]}...")  # Show first 5 tools
                
                state.messages.append(response)
                return state
            except Exception as e:
                error_str = str(e)
                # Check if it's a 404 model not found error
                if "404" in error_str or "not_found_error" in error_str or "model:" in error_str:
                    print(f"❌ Model 404 error during execution: {e}")
                    print(f"❌ Current model: {self.model}")
                    # Clear the global cache to force reinitialization
                    import convonet.routes as routes_module
                    routes_module._agent_graph_cache = None
                    routes_module._agent_graph_model = None
                    print("🔄 Cleared agent graph cache due to model 404 error")
                    # Raise a special exception that will trigger retry
                    raise RuntimeError(f"MODEL_404_ERROR: Model {self.model} not found. Cache cleared. Please retry.") from e
                # For other errors, re-raise
                raise

        async def tools_node(state: AgentState):
            """Execute async MCP tools and return results."""
            try:
                print(f"🔧 Tools node executing with {len(self.tools)} tools available")
                
                # Initialize tool execution tracker
                from .tool_execution_viewer import create_tracker, get_tracker, ToolStatus
                request_id = state.request_id or f"req_{int(time.time())}"
                tracker = get_tracker(request_id)
                if not tracker:
                    tracker = create_tracker(request_id, state.authenticated_user_id)
                
                # Get the last message which should contain tool calls
                last_message = state.messages[-1]
                if not hasattr(last_message, 'tool_calls') or not last_message.tool_calls:
                    return state
                
                # Execute each tool call - CRITICAL: Every tool_use MUST have a tool_result
                # OPTIMIZATION: Execute tools in parallel for lower latency
                tool_messages = []
                tool_call_ids = set()  # Track all tool call IDs to ensure we handle them all
                
                # Collect all tool calls first
                tool_calls_list = list(last_message.tool_calls)
                
                # OPTIMIZATION: Execute multiple tools in parallel
                if len(tool_calls_list) > 1:
                    print(f"🚀 Executing {len(tool_calls_list)} tools in parallel for lower latency", flush=True)
                    
                    async def execute_tool_async(tool_call):
                        """Execute a single tool call"""
                        tool_name = tool_call.get('name', 'unknown')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id', f'tool_{len(tool_messages)}')
                        
                        # Start tracking tool execution
                        execution = tracker.start_tool(tool_name, tool_id, tool_args)
                        
                        try:
                            # Find the tool by name
                            tool = None
                            for t in self.tools:
                                if t.name == tool_name:
                                    tool = t
                                    break
                            
                            if tool:
                                # Reduced timeout for faster failure (was 8s, now 6s)
                                tool_timeout = 6.0
                                try:
                                    if hasattr(tool, 'ainvoke'):
                                        result = await asyncio.wait_for(tool.ainvoke(tool_args), timeout=tool_timeout)
                                    else:
                                        result = await asyncio.wait_for(asyncio.to_thread(tool.invoke, tool_args), timeout=tool_timeout)
                                    
                                    # Track successful completion
                                    tracker.complete_tool(tool_id, result)
                                    print(f"✅ Tool {tool_name} completed successfully")
                                    return {
                                        'tool_call_id': tool_id,
                                        'content': str(result),
                                        'status': 'success'
                                    }
                                except asyncio.TimeoutError:
                                    # Track timeout
                                    tracker.timeout_tool(tool_id)
                                    return {
                                        'tool_call_id': tool_id,
                                        'content': "I'm sorry, the database operation timed out. Please try again.",
                                        'status': 'timeout'
                                    }
                            else:
                                # Track tool not found error
                                tracker.fail_tool(tool_id, f"Tool {tool_name} not found", "ToolNotFoundError")
                                return {
                                    'tool_call_id': tool_id,
                                    'content': f"Tool {tool_name} not found",
                                    'status': 'error'
                                }
                        except Exception as e:
                            error_str = str(e)
                            error_type = type(e).__name__
                            stack_trace = traceback.format_exc()
                            
                            # Track failure
                            tracker.fail_tool(tool_id, error_str, error_type, stack_trace)
                            
                            print(f"❌ Tool {tool_name} error: {error_str}")
                            print(f"❌ Tool {tool_name} error type: {error_type}")
                            print(f"❌ Tool {tool_name} full error traceback:")
                            traceback.print_exc()
                            
                            # Provide more specific error messages
                            if "timeout" in error_str.lower() or "timed out" in error_str.lower():
                                error_msg = "I'm sorry, the database operation timed out. Please try again."
                            elif "connection" in error_str.lower() or "connect" in error_str.lower() or "BrokenResourceError" in error_type:
                                error_msg = "I encountered a database connection issue. The operation may have completed. Please check your calendar."
                            elif "Database not available" in error_str or "DB_URI" in error_str:
                                error_msg = "I'm sorry, there's a database connection issue. Please try again in a moment."
                            else:
                                error_msg = f"I encountered an error: {error_str[:200]}"
                            
                            return {
                                'tool_call_id': tool_id,
                                'content': error_msg,
                                'status': 'error'
                            }
                    
                    # Execute all tools in parallel
                    tool_results = await asyncio.gather(*[execute_tool_async(tc) for tc in tool_calls_list])
                    
                    # Convert results to ToolMessage format
                    for result in tool_results:
                        from langchain_core.messages import ToolMessage
                        tool_messages.append(ToolMessage(
                            content=result['content'],
                            tool_call_id=result['tool_call_id']
                        ))
                else:
                    # Single tool - execute sequentially (original logic)
                    for tool_call in tool_calls_list:
                        tool_name = tool_call.get('name', 'unknown')
                        tool_args = tool_call.get('args', {})
                        tool_id = tool_call.get('id', f'tool_{len(tool_messages)}')
                        tool_call_ids.add(tool_id)
                    
                        print(f"🔧 Executing tool: {tool_name} (id: {tool_id}) with args: {tool_args}")
                        
                        # Start tracking tool execution
                        execution = tracker.start_tool(tool_name, tool_id, tool_args)
                        
                        # Initialize result variable to avoid UnboundLocalError
                        result = None
                        
                        # Retry logic for MCP connection failures
                        max_retries = 2
                        retry_count = 0
                        
                        try:
                            # Find the tool by name
                            tool = None
                            for t in self.tools:
                                if t.name == tool_name:
                                    tool = t
                                    break
                            
                            if tool:
                                # OPTIMIZED: Reduced timeout for faster failure detection
                                # Reduced from 8s to 6s for lower latency (Gemini uses native SDK, doesn't need longer timeout)
                                tool_timeout = 6.0
                                
                                # Retry logic for MCP connection failures
                                max_retries = 2
                                retry_count = 0
                                result = None
                                
                                while retry_count < max_retries and result is None:
                                    try:
                                        if hasattr(tool, 'ainvoke'):
                                            result = await asyncio.wait_for(tool.ainvoke(tool_args), timeout=tool_timeout)
                                        else:
                                            result = await asyncio.wait_for(asyncio.to_thread(tool.invoke, tool_args), timeout=tool_timeout)
                                        
                                        # Track successful completion
                                        tracker.complete_tool(tool_id, result)
                                        print(f"✅ Tool {tool_name} completed successfully")
                                        break  # Success, exit retry loop
                                    except asyncio.TimeoutError:
                                        # Track timeout
                                        tracker.timeout_tool(tool_id)
                                        result = "I'm sorry, the database operation timed out. Please try again."
                                        print(f"⏰ Tool {tool_name} timed out after {tool_timeout} seconds")
                                        break  # Timeout is not retryable
                                    except ExceptionGroup as eg:
                                        # Unwrap ExceptionGroup and get the first exception
                                        print(f"❌ Tool {tool_name} ExceptionGroup with {len(eg.exceptions)} exception(s)")
                                        for i, exc in enumerate(eg.exceptions):
                                            print(f"❌   Exception {i+1}: {type(exc).__name__}: {exc}")
                                        first_error = eg.exceptions[0] if eg.exceptions else eg
                                        error_str = str(first_error)
                                        error_type = type(first_error).__name__
                                        
                                        # Log full error details for debugging
                                        import traceback
                                        print(f"❌ Tool {tool_name} full error traceback:")
                                        traceback.print_exc()
                                        
                                        # Handle BrokenResourceError (MCP connection issue) - retry once
                                        if "BrokenResourceError" in error_type or not error_str.strip():
                                            if retry_count < max_retries - 1:
                                                print(f"🔄 MCP connection broken, clearing cache and retrying ({retry_count + 1}/{max_retries})...")
                                                # Clear MCP tools cache to force reconnection
                                                from convonet.routes import _mcp_tools_cache
                                                import convonet.routes as routes_module
                                                routes_module._mcp_tools_cache = None
                                                retry_count += 1
                                                await asyncio.sleep(0.5)  # Brief delay before retry
                                                result = None  # Reset to retry
                                                continue  # Retry the tool call
                                            else:
                                                result = "I encountered a connection issue with the MCP server. The operation may have completed. Please check your calendar."
                                        elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                                            result = "I'm sorry, the database operation timed out. Please try again."
                                            break  # Timeout is not retryable
                                        elif "connection" in error_str.lower() or "connect" in error_str.lower():
                                            result = f"I encountered a database connection issue: {error_str[:150]}. Please try again."
                                            break  # Connection errors are not retryable (already handled above)
                                        else:
                                            result = f"I encountered an error: {error_str[:200]}"
                                            break  # Other errors are not retryable
                                        print(f"❌ Tool {tool_name} error (unwrapped): {error_str if error_str else error_type}")
                                    except Exception as tool_error:
                                        error_str = str(tool_error)
                                        error_type = type(tool_error).__name__
                                        print(f"❌ Tool {tool_name} error: {error_str}")
                                        print(f"❌ Tool {tool_name} error type: {error_type}")
                                        
                                        # Log full traceback for debugging
                                        import traceback
                                        print(f"❌ Tool {tool_name} full error traceback:")
                                        traceback.print_exc()
                                        
                                        # Handle BrokenResourceError (MCP connection issue) - retry once
                                        if "BrokenResourceError" in error_type:
                                            if retry_count < max_retries - 1:
                                                print(f"🔄 MCP connection broken, clearing cache and retrying ({retry_count + 1}/{max_retries})...")
                                                # Clear MCP tools cache to force reconnection
                                                from convonet.routes import _mcp_tools_cache
                                                import convonet.routes as routes_module
                                                routes_module._mcp_tools_cache = None
                                                retry_count += 1
                                                await asyncio.sleep(0.5)  # Brief delay before retry
                                                result = None  # Reset to retry
                                                continue  # Retry the tool call
                                            else:
                                                result = "I encountered a connection issue with the MCP server. The operation may have completed. Please check your calendar or todo list."
                                                break  # Max retries reached
                                        # Handle BrokenPipeError (MCP server process crashed - likely OOM)
                                        elif "BrokenPipeError" in error_type or "Broken pipe" in error_str:
                                            if retry_count < max_retries - 1:
                                                print(f"⚠️ MCP server process crashed (likely memory issue), clearing cache and retrying ({retry_count + 1}/{max_retries})...", flush=True)
                                                # Clear MCP tools cache to force MCP server restart
                                                from convonet.routes import _mcp_tools_cache
                                                import convonet.routes as routes_module
                                                routes_module._mcp_tools_cache = None
                                                retry_count += 1
                                                await asyncio.sleep(1.0)  # Longer delay for process restart
                                                result = None  # Reset to retry
                                                continue  # Retry the tool call
                                            else:
                                                result = "I encountered a system error (likely due to memory constraints). Please try again or contact support."
                                                print(f"❌ MCP server BrokenPipeError after {max_retries} retries - may indicate insufficient RAM", flush=True)
                                                break  # Max retries reached
                                        elif "UnboundLocalError" in error_type or "call_tool_result" in error_str:
                                            # This is an error in the MCP adapter library - don't retry
                                            result = "I encountered a system error while executing the tool. Please try again."
                                            print(f"❌ MCP adapter library error: {error_str}", flush=True)
                                            break  # Don't retry library errors
                                        elif "TaskGroup" in error_str:
                                            result = "I encountered a system processing error. The task may have been created successfully. Please check your todo list."
                                            break  # Not retryable
                                        elif "Database not available" in error_str or "DB_URI" in error_str:
                                            result = "I'm sorry, there's a database connection issue. Please try again in a moment."
                                            break  # Not retryable
                                        elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                                            result = "I'm sorry, the database operation timed out. Please try again."
                                            break  # Not retryable
                                        elif "connection" in error_str.lower() or "connect" in error_str.lower():
                                            result = f"I encountered a database connection issue: {error_str[:150]}. Please try again."
                                            break  # Not retryable
                                        elif "validation" in error_str.lower():
                                            result = "I encountered a data validation error. Let me try again."
                                            break  # Not retryable
                                        elif not error_str.strip():
                                            # Empty error message
                                            result = "I encountered an unexpected error. Please try again or rephrase your request."
                                            break  # Not retryable
                                        else:
                                            result = f"I encountered an error: {error_str[:200]}"
                                            break  # Not retryable
                                        
                                        # If we get here and result is still None, set a default error message
                                        if result is None:
                                            result = f"I encountered an error: {error_str[:200]}"
                                            break
                                        
                                        # Track failure if result is an error message
                                        if result and ("error" in result.lower() or "sorry" in result.lower() or "issue" in result.lower()):
                                            tracker.fail_tool(tool_id, error_str, error_type, traceback.format_exc())
                            else:
                                # Track tool not found error
                                tracker.fail_tool(tool_id, f"Tool {tool_name} not found", "ToolNotFoundError")
                                result = f"Tool {tool_name} not found"
                                print(f"⚠️ Tool {tool_name} not found in available tools")
                            
                            # Ensure result is set before creating ToolMessage
                            if result is None:
                                result = f"Tool {tool_name} execution completed with no result"
                            
                            from langchain_core.messages import ToolMessage
                            tool_message = ToolMessage(
                                content=str(result),
                                name=tool_name,
                                tool_call_id=tool_id
                            )
                            tool_messages.append(tool_message)
                            print(f"🔧 Tool {tool_name} result: {result}")
                        except Exception as e:
                            # Outer exception handler for tool execution
                            error_str = str(e)
                            error_type = type(e).__name__
                            stack_trace = traceback.format_exc()
                            
                            # Track failure
                            tracker.fail_tool(tool_id, error_str, error_type, stack_trace)
                            
                            print(f"❌ Outer exception for tool {tool_name}: {error_str}")
                            traceback.print_exc()
                            from langchain_core.messages import ToolMessage
                            tool_message = ToolMessage(
                                content=f"Error executing tool: {error_str[:200]}",
                                name=tool_name,
                                tool_call_id=tool_id
                            )
                            tool_messages.append(tool_message)
                        
                
                # CRITICAL: Verify we have results for ALL tool calls
                result_ids = {msg.tool_call_id for msg in tool_messages if hasattr(msg, 'tool_call_id')}
                missing_ids = tool_call_ids - result_ids
                if missing_ids:
                    print(f"⚠️ WARNING: Missing tool results for IDs: {missing_ids}")
                    # Create error messages for any missing tool results
                    for missing_id in missing_ids:
                        error_tool_message = ToolMessage(
                            content="Tool execution failed - no result returned",
                            name="system_error",
                            tool_call_id=missing_id
                        )
                        tool_messages.append(error_tool_message)
                
                # Verify we have exactly one result per tool call
                if len(tool_messages) != len(last_message.tool_calls):
                    print(f"⚠️ WARNING: Tool message count mismatch. Expected {len(last_message.tool_calls)}, got {len(tool_messages)}")
                    print(f"⚠️ Tool call IDs: {[tc.get('id') for tc in last_message.tool_calls]}")
                    print(f"⚠️ Tool message IDs: {[msg.tool_call_id for msg in tool_messages if hasattr(msg, 'tool_call_id')]}")
                
                # Add tool messages to state - CRITICAL: Must be added in order
                state.messages.extend(tool_messages)
                print(f"✅ Added {len(tool_messages)} tool result messages to state")
                
                # Mark tracker as finished
                tracker.finish()
                return state
                
            except Exception as e:
                error_str = str(e)
                print(f"❌ Critical error in tools_node: {error_str}")
                print(f"❌ Error type: {type(e)}")
                
                # Return a user-friendly error message
                from langchain_core.messages import ToolMessage
                error_message = ToolMessage(
                    content="I encountered a system processing error. The task may have been created successfully. Please check your todo list.",
                    name="system_error",
                    tool_call_id="error_handling"
                )
                state.messages.append(error_message)
                return state

        builder.add_node(assistant)
        builder.add_node("tools", tools_node)

        builder.set_entry_point("assistant")
        builder.add_conditional_edges(
            "assistant",
            tools_condition
        )
        builder.add_edge("tools", "assistant")

        return builder.compile(checkpointer=InMemorySaver())

    def draw_graph(self,):
        if self.graph is None:
            raise ValueError("Graph not built yet")
        from IPython.display import Image

        return Image(self.graph.get_graph().draw_mermaid_png())


class MortgageAgent(TodoAgent):
    """
    Mortgage Application Agent - Specialized agent for mortgage application process.
    Inherits from TodoAgent but uses mortgage-specific prompts and tools.
    """
    
    def __init__(
        self,
        name: str = "Mortgage Application Assistant",
        model: str = None,
        provider: Optional[LLMProvider] = None,
        tools: List[BaseTool] = [],
        system_prompt: str = MORTGAGE_SYSTEM_PROMPT,
    ) -> None:
        """
        Initialize Mortgage Agent with mortgage-specific prompts.
        
        Args:
            name: Agent name
            model: LLM model name (optional)
            provider: LLM provider (claude, gemini, openai)
            tools: List of tools to bind (should include mortgage MCP tools)
            system_prompt: System prompt (defaults to MORTGAGE_SYSTEM_PROMPT)
        """
        # Call parent __init__ with mortgage-specific prompt
        super().__init__(
            name=name,
            model=model,
            provider=provider,
            tools=tools,
            system_prompt=system_prompt
        )


# Lazy initialization to avoid circular imports and environment variable issues
_agent_instance = None

def get_agent():
    """Get or create the TodoAgent instance (lazy initialization)"""
    global _agent_instance
    if _agent_instance is None:
        _agent_instance = TodoAgent()
    return _agent_instance


def get_mortgage_agent(tools: List[BaseTool] = [], provider: Optional[LLMProvider] = None, model: str = None) -> MortgageAgent:
    """
    Get or create a MortgageAgent instance.
    
    Args:
        tools: List of tools to bind (should include mortgage MCP tools)
        provider: LLM provider (claude, gemini, openai)
        model: LLM model name (optional)
        
    Returns:
        MortgageAgent instance
    """
    return MortgageAgent(tools=tools, provider=provider, model=model)


# Import healthcare prompt at runtime to avoid circular imports
def _get_healthcare_prompt():
    from .healthcare_payer_prompts import HEALTHCARE_PAYER_SYSTEM_PROMPT
    return HEALTHCARE_PAYER_SYSTEM_PROMPT


class HealthcareAgent(TodoAgent):
    """
    Healthcare Payer Agent - Specialized agent for healthcare member services.
    Handles claims, eligibility, benefits, prior authorizations, and provider network.
    Inherits from TodoAgent but uses healthcare-specific prompts and tools.
    """
    
    def __init__(
        self,
        name: str = "Healthcare Member Services Assistant",
        model: str = None,
        provider: Optional[LLMProvider] = None,
        tools: List[BaseTool] = [],
        system_prompt: str = None,
    ) -> None:
        """
        Initialize Healthcare Agent with healthcare-specific prompts.
        
        Args:
            name: Agent name
            model: LLM model name (optional)
            provider: LLM provider (claude, gemini, openai)
            tools: List of tools to bind (should include healthcare MCP tools)
            system_prompt: System prompt (defaults to HEALTHCARE_PAYER_SYSTEM_PROMPT)
        """
        # Get healthcare prompt if not provided
        if system_prompt is None:
            system_prompt = _get_healthcare_prompt()
        
        # Call parent __init__ with healthcare-specific prompt
        super().__init__(
            name=name,
            model=model,
            provider=provider,
            tools=tools,
            system_prompt=system_prompt
        )


def get_healthcare_agent(tools: List[BaseTool] = [], provider: Optional[LLMProvider] = None, model: str = None) -> HealthcareAgent:
    """
    Get or create a HealthcareAgent instance.
    
    Args:
        tools: List of tools to bind (should include healthcare MCP tools)
        provider: LLM provider (claude, gemini, openai)
        model: LLM model name (optional)
        
    Returns:
        HealthcareAgent instance
    """
    return HealthcareAgent(tools=tools, provider=provider, model=model)


# For backwards compatibility
agent = None  # Will be initialized on first use

if __name__ == "__main__":
    get_agent().draw_graph()
