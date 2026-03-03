"""
Hybrid Streaming Implementation for Gemini using Native SDK
This module provides real-time streaming for Gemini while maintaining LangGraph integration
"""
import asyncio
import gc
import json
import os
import sys
from typing import Optional, Dict, Any, Callable, List
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

try:
    from google import genai
    from google.genai import types
    GEMINI_SDK_AVAILABLE = True
except ImportError:
    GEMINI_SDK_AVAILABLE = False
    print("⚠️ Google GenAI SDK not available. Install with: pip install google-genai")


# Global Gemini client to prevent leaks and reuse connections
_gemini_client = None

class GeminiStreamingHandler:
    """
    Handles Gemini streaming using native SDK while maintaining LangGraph compatibility
    """
    
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        tools: Optional[List[BaseTool]] = None,
        system_prompt: Optional[str] = None,
        on_text_chunk: Optional[Callable[[str], None]] = None,
        on_tool_call: Optional[Callable[[Dict[str, Any]], None]] = None,
        on_complete: Optional[Callable[[str, List[Dict]], None]] = None,
    ):
        global _gemini_client
        self.api_key = api_key
        self.model = model
        self.tools = tools or []
        self.system_prompt = system_prompt
        self.on_text_chunk = on_text_chunk
        self.on_tool_call = on_tool_call
        self.on_complete = on_complete
        
        if not GEMINI_SDK_AVAILABLE:
            raise ImportError("Google GenAI SDK not available. Install with: pip install google-genai")
        
        # Reuse or initialize client
        if _gemini_client is None:
            _gemini_client = genai.Client(api_key=api_key)
        self.client = _gemini_client
        self._response_stream = None  # Track active stream for cleanup
    
    def _resolve_schema_refs(self, schema: Dict[str, Any], defs: Dict[str, Any] = None) -> Dict[str, Any]:
        """Resolve $ref references in JSON Schema to flat schema"""
        if defs is None:
            defs = schema.get("$defs", {})
        
        if not isinstance(schema, dict):
            return schema
        
        resolved = {}
        for key, value in schema.items():
            if key == "$ref":
                # Resolve reference
                ref_path = value
                if ref_path.startswith("#/$defs/"):
                    ref_name = ref_path.split("/")[-1]
                    if ref_name in defs:
                        # Recursively resolve the referenced schema
                        resolved.update(self._resolve_schema_refs(defs[ref_name], defs))
                    else:
                        # Reference not found, use string type as fallback
                        resolved = {"type": "string"}
                else:
                    # External reference, use string as fallback
                    resolved = {"type": "string"}
            elif key == "anyOf":
                # Handle anyOf - take first option and resolve it
                if isinstance(value, list) and len(value) > 0:
                    first_option = value[0]
                    if isinstance(first_option, dict) and "$ref" in first_option:
                        resolved.update(self._resolve_schema_refs(first_option, defs))
                    else:
                        resolved.update(self._resolve_schema_refs(first_option, defs))
            elif isinstance(value, dict):
                resolved[key] = self._resolve_schema_refs(value, defs)
            elif isinstance(value, list):
                resolved[key] = [self._resolve_schema_refs(item, defs) if isinstance(item, dict) else item for item in value]
            else:
                resolved[key] = value
        
        return resolved
    
    def _convert_tools_to_gemini_format(self) -> List[Dict[str, Any]]:
        """Convert LangChain tools to Gemini function declarations"""
        gemini_tools = []
        for tool in self.tools:
            # Get tool schema - handle both Pydantic models and dicts
            schema = {}
            defs = {}
            try:
                if hasattr(tool, 'args_schema') and tool.args_schema:
                    # Check if args_schema is a Pydantic model (has schema() method)
                    if hasattr(tool.args_schema, 'schema'):
                        schema = tool.args_schema.schema()
                    # Check if args_schema is already a dict
                    elif isinstance(tool.args_schema, dict):
                        schema = tool.args_schema
                    # Try get_input_schema() method (LangChain standard)
                    elif hasattr(tool, 'get_input_schema'):
                        schema = tool.get_input_schema().schema() if hasattr(tool.get_input_schema(), 'schema') else tool.get_input_schema()
                # Fallback: try get_input_schema() directly on tool
                elif hasattr(tool, 'get_input_schema'):
                    input_schema = tool.get_input_schema()
                    if hasattr(input_schema, 'schema'):
                        schema = input_schema.schema()
                    elif isinstance(input_schema, dict):
                        schema = input_schema
                
                # Extract $defs if present
                if isinstance(schema, dict):
                    defs = schema.get("$defs", {})
                    # Resolve $ref references in schema
                    schema = self._resolve_schema_refs(schema, defs)
                    
            except Exception as e:
                print(f"⚠️ Error getting schema for tool {tool.name}: {e}", flush=True)
                import traceback
                traceback.print_exc()
                # Use empty schema as fallback
                schema = {}
            
            # Convert to Gemini format - ensure properties don't have $ref
            properties = schema.get("properties", {})
            # Clean properties - remove any remaining $ref or $defs
            cleaned_properties = {}
            for prop_name, prop_schema in properties.items():
                if isinstance(prop_schema, dict):
                    # Remove $ref, $defs, and anyOf with $ref
                    cleaned_prop = {k: v for k, v in prop_schema.items() 
                                   if k not in ["$ref", "$defs"] and 
                                   not (k == "anyOf" and isinstance(v, list) and any(isinstance(item, dict) and "$ref" in item for item in v))}
                    # If anyOf exists without $ref, use first option
                    if "anyOf" in cleaned_prop and isinstance(cleaned_prop["anyOf"], list) and len(cleaned_prop["anyOf"]) > 0:
                        first_option = cleaned_prop["anyOf"][0]
                        if isinstance(first_option, dict) and "$ref" not in first_option:
                            cleaned_prop = first_option
                        elif isinstance(first_option, dict):
                            # Still has $ref, use enum if available, else string
                            if "enum" in first_option:
                                cleaned_prop = {"type": "string", "enum": first_option["enum"]}
                            else:
                                cleaned_prop = {"type": "string"}
                    cleaned_properties[prop_name] = cleaned_prop
                else:
                    cleaned_properties[prop_name] = prop_schema
            
            # Convert to Gemini format
            gemini_tool = {
                "function_declarations": [{
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": {
                        "type": "OBJECT",
                        "properties": cleaned_properties,
                        "required": schema.get("required", [])
                    }
                }]
            }
            gemini_tools.append(gemini_tool)
        
        print(f"🔧 Converted {len(gemini_tools)} tools to Gemini format", flush=True)
        if len(gemini_tools) > 0:
            first_tool_decl = gemini_tools[0].get('function_declarations', [{}])
            if first_tool_decl:
                print(f"🔧 First tool: {first_tool_decl[0].get('name', 'unknown')}", flush=True)
                print(f"🔧 First tool description: {first_tool_decl[0].get('description', '')[:50]}...", flush=True)
            else:
                print(f"⚠️ First tool has no function_declarations", flush=True)
        else:
            print(f"⚠️ No tools converted!", flush=True)
        
        return gemini_tools
    
    async def stream_response(
        self,
        messages: List[Any],
        session_id: Optional[str] = None,
    ) -> tuple[str, List[Dict[str, Any]]]:
        """
        Stream response from Gemini using native SDK
        
        Args:
            messages: List of LangChain messages
            session_id: Optional session ID for WebSocket emission
            
        Returns:
            Tuple of (final_text, tool_calls)
        """
        # Convert LangChain messages to Gemini format using SDK types
        # Import types at function level to ensure it's available
        if not GEMINI_SDK_AVAILABLE:
            # Fallback to dict format if SDK not available
            gemini_messages = []
            for msg in messages:
                if isinstance(msg, SystemMessage):
                    continue
                elif isinstance(msg, HumanMessage):
                    gemini_messages.append({"role": "user", "parts": [{"text": str(msg.content)}]})
                elif isinstance(msg, AIMessage):
                    gemini_messages.append({"role": "model", "parts": [{"text": str(msg.content)}]})
                elif isinstance(msg, ToolMessage):
                    response_data = msg.content
                    if isinstance(response_data, str):
                        response_data = {"result": response_data}
                    gemini_messages.append({
                        "role": "user",
                        "parts": [{"function_response": {"name": getattr(msg, 'name', 'unknown'), "response": response_data}}]
                    })
        else:
            # Use SDK types if available
            from google.genai import types as genai_types
            gemini_messages = []
            for msg in messages:
                if isinstance(msg, SystemMessage):
                    # System messages are handled separately
                    continue
                elif isinstance(msg, HumanMessage):
                    # Use SDK Content type for user messages
                    try:
                        user_content = genai_types.Content(
                            role="user",
                            parts=[genai_types.Part(text=str(msg.content))]
                        )
                        gemini_messages.append(user_content)
                    except Exception as e:
                        # Fallback to dict format if SDK types fail
                        print(f"⚠️ Error creating Content for HumanMessage, using dict: {e}", flush=True)
                        gemini_messages.append({
                            "role": "user",
                            "parts": [{"text": str(msg.content)}]
                        })
                elif isinstance(msg, AIMessage):
                    # Use SDK Content type for model messages
                    try:
                        model_content = genai_types.Content(
                            role="model",
                            parts=[genai_types.Part(text=str(msg.content))]
                        )
                        gemini_messages.append(model_content)
                    except Exception as e:
                        # Fallback to dict format if SDK types fail
                        print(f"⚠️ Error creating Content for AIMessage, using dict: {e}", flush=True)
                        gemini_messages.append({
                            "role": "model",
                            "parts": [{"text": str(msg.content)}]
                        })
                elif isinstance(msg, ToolMessage):
                    # Convert tool result to Gemini format using SDK FunctionResponse
                    try:
                        # FunctionResponse expects response to be a dict, not a string or list
                        # Wrap strings and lists in a dict
                        response_data = msg.content
                        if isinstance(response_data, str):
                            # Try to parse as JSON if possible, otherwise use as-is
                            try:
                                import json
                                response_data = json.loads(response_data)
                            except:
                                # Not JSON, use as string value
                                response_data = {"result": response_data}
                        elif isinstance(response_data, list):
                            # Lists must be wrapped in a dict
                            response_data = {"result": response_data}
                        elif not isinstance(response_data, dict):
                            # Any other type (int, bool, etc.) - wrap in dict
                            response_data = {"result": response_data}
                        
                        function_response = genai_types.FunctionResponse(
                            name=getattr(msg, 'name', 'unknown'),
                            response=response_data
                        )
                        tool_content = genai_types.Content(
                            role="user",
                            parts=[genai_types.Part(function_response=function_response)]
                        )
                        gemini_messages.append(tool_content)
                    except Exception as e:
                        # Fallback to dict format if SDK types fail
                        print(f"⚠️ Error creating Content for ToolMessage, using dict: {e}", flush=True)
                        # Ensure response is a dict, not a string or list
                        response_data = msg.content
                        if isinstance(response_data, str):
                            try:
                                import json
                                response_data = json.loads(response_data)
                            except:
                                response_data = {"result": response_data}
                        elif isinstance(response_data, list):
                            # Lists must be wrapped in a dict
                            response_data = {"result": response_data}
                        elif not isinstance(response_data, dict):
                            # Any other type - wrap in dict
                            response_data = {"result": response_data}
                        gemini_messages.append({
                            "role": "user",
                            "parts": [{
                                "function_response": {
                                    "name": getattr(msg, 'name', 'unknown'),
                                    "response": response_data
                                }
                            }]
                        })
        
        # Prepare generation config
        generation_config = {
            "temperature": 0.0,
        }
        
        # Add tools if available
        tools_config = None
        if self.tools:
            tools_config = self._convert_tools_to_gemini_format()
        
        # Prepare system instruction
        system_instruction = self.system_prompt if self.system_prompt else None
        
        # Stream response
        full_text = ""
        tool_calls = []
        current_tool_call = None
        iteration = 0  # Track iteration for debug logging
        
        try:
            # Use async generate_content_stream for streaming
            # Note: system_instruction may need to be passed differently or included in contents
            # Build the request parameters
            # Google GenAI SDK format: tools should be in config, not as separate parameter
            request_params = {
                "model": self.model,
                "contents": gemini_messages,
            }
            
            # Add tools to generation config (correct format for Google GenAI SDK)
            if tools_config:
                # Flatten tools_config - combine all function_declarations into one list
                all_function_declarations = []
                for tool_config in tools_config:
                    if isinstance(tool_config, dict) and 'function_declarations' in tool_config:
                        all_function_declarations.extend(tool_config['function_declarations'])
                
                if all_function_declarations:
                    # Tools should be passed in config using SDK's types
                    # Convert dict function_declarations to types.FunctionDeclaration objects
                    try:
                        from google.genai import types
                        # Convert each function declaration dict to FunctionDeclaration object
                        function_decl_objects = []
                        for func_decl_dict in all_function_declarations:
                            try:
                                # Create FunctionDeclaration from dict
                                func_decl = types.FunctionDeclaration(**func_decl_dict)
                                function_decl_objects.append(func_decl)
                            except Exception as e:
                                print(f"⚠️ Error creating FunctionDeclaration for {func_decl_dict.get('name', 'unknown')}: {e}", flush=True)
                                # Skip this function declaration if it fails
                                continue
                        
                        if function_decl_objects:
                            # Create Tool with FunctionDeclaration objects
                            tool_obj = types.Tool(function_declarations=function_decl_objects)
                            generation_config["tools"] = [tool_obj]
                            print(f"🔧 Added {len(function_decl_objects)} function declaration(s) to config using SDK types", flush=True)
                        else:
                            print(f"⚠️ No valid function declarations created, skipping tools", flush=True)
                    except (ImportError, TypeError, AttributeError) as e:
                        # Fallback: try dict format (might work with some SDK versions)
                        print(f"⚠️ Could not use SDK types, trying dict format: {e}", flush=True)
                        import traceback
                        traceback.print_exc()
                        generation_config["tools"] = [{"function_declarations": all_function_declarations}]
                        print(f"🔧 Added {len(all_function_declarations)} function declaration(s) to config using dict format", flush=True)
                    
                    if len(all_function_declarations) > 0:
                        print(f"🔧 First tool in config: {all_function_declarations[0].get('name', 'unknown')}", flush=True)
                else:
                    print(f"⚠️ No function_declarations found in tools_config", flush=True)
            else:
                print(f"⚠️ No tools_config to add to request", flush=True)
            
            # Add generation config to request
            request_params["config"] = generation_config
            
            # Add system instruction to contents (not as parameter - not supported by SDK)
            # Check if system instruction is already in messages
            has_system_instruction = False
            if gemini_messages:
                first_msg = gemini_messages[0]
                # Check if first message is a system instruction
                # Handle both Content objects and dicts
                if isinstance(first_msg, dict):
                    has_system_instruction = first_msg.get("role") == "system"
                else:
                    # Content object - check role attribute
                    has_system_instruction = getattr(first_msg, "role", None) == "system"
            
            # Prepend system instruction as a user message if not already present
            if system_instruction and not has_system_instruction:
                gemini_messages.insert(0, {
                    "role": "user",
                    "parts": [{"text": f"System: {system_instruction}"}]
                })
                request_params["contents"] = gemini_messages
            
            # Ensure system_instruction is NOT in request_params (not supported by SDK)
            request_params.pop("system_instruction", None)
            
            # Use async generate_content_stream for streaming
            response_stream = None
            try:
                if hasattr(self.client, 'aio'):
                    try:
                        response_stream = await self.client.aio.models.generate_content_stream(**request_params)
                        self._response_stream = response_stream  # Track for cleanup
                    except TypeError as e:
                        # Handle parameter errors
                        error_msg = str(e)
                        if "system_instruction" in error_msg:
                            # system_instruction should already be removed, but handle it just in case
                            print(f"⚠️ system_instruction parameter error (should be in contents): {error_msg}", flush=True)
                            request_params.pop("system_instruction", None)
                            request_params["contents"] = gemini_messages
                            response_stream = await self.client.aio.models.generate_content_stream(**request_params)
                            self._response_stream = response_stream
                        elif "tools" in error_msg:
                            # If tools parameter error, tools are already in config (handled above)
                            # This shouldn't happen, but handle it gracefully
                            print(f"⚠️ tools parameter error (tools should be in config, already handled): {error_msg}", flush=True)
                            request_params["contents"] = gemini_messages
                            response_stream = await self.client.aio.models.generate_content_stream(**request_params)
                            self._response_stream = response_stream
                        else:
                            # Unknown parameter error - log and re-raise
                            print(f"❌ Unknown TypeError in Gemini streaming: {error_msg}", flush=True)
                            print(f"🔍 request_params keys: {list(request_params.keys())}", flush=True)
                            raise
                else:
                    # Fallback to sync streaming (will need to wrap in executor)
                    import asyncio
                    try:
                        response_stream = await asyncio.to_thread(
                            lambda: self.client.models.generate_content_stream(**request_params)
                        )
                        self._response_stream = response_stream
                    except TypeError as e:
                        # If system_instruction parameter is not supported, try without it
                        if "system_instruction" in str(e):
                            print(f"⚠️ system_instruction parameter not supported, including in contents instead", flush=True)
                            request_params.pop("system_instruction", None)
                            # Check if system instruction is already in messages (handle both Content objects and dicts)
                            has_system = False
                            if gemini_messages:
                                first_msg = gemini_messages[0]
                                if isinstance(first_msg, dict):
                                    has_system = first_msg.get("role") == "system"
                                else:
                                    has_system = getattr(first_msg, "role", None) == "system"
                            if not has_system:
                                gemini_messages.insert(0, {
                                    "role": "user",
                                    "parts": [{"text": f"System: {system_instruction}"}]
                                })
                            request_params["contents"] = gemini_messages
                            response_stream = await asyncio.to_thread(
                                lambda: self.client.models.generate_content_stream(**request_params)
                            )
                            self._response_stream = response_stream
                        elif "tools" in str(e):
                            # If tools parameter error, tools are already in config (handled above)
                            print(f"⚠️ tools parameter error in sync path (tools should be in config)", flush=True)
                            # Just retry - tools are already in config
                            response_stream = await asyncio.to_thread(
                                lambda: self.client.models.generate_content_stream(**request_params)
                            )
                            self._response_stream = response_stream
                        else:
                            raise
                
                async for chunk in response_stream:
                    # Debug: Log chunk structure (first chunk only)
                    if len(tool_calls) == 0 and full_text == "":
                        chunk_attrs = [attr for attr in dir(chunk) if not attr.startswith('_')]
                        print(f"🔍 First chunk attributes: {chunk_attrs[:15]}...", flush=True)
                        print(f"🔍 Chunk type: {type(chunk)}", flush=True)
                        # Try to inspect chunk contents
                        try:
                            chunk_dict = chunk.__dict__ if hasattr(chunk, '__dict__') else {}
                            print(f"🔍 Chunk dict keys: {list(chunk_dict.keys())[:10]}...", flush=True)
                            
                            # Check candidates for function calls
                            if hasattr(chunk, 'candidates') and chunk.candidates:
                                print(f"🔍 Found {len(chunk.candidates)} candidate(s)", flush=True)
                                candidate = chunk.candidates[0]
                                candidate_attrs = [attr for attr in dir(candidate) if not attr.startswith('_')]
                                print(f"🔍 Candidate attributes: {candidate_attrs[:15]}...", flush=True)
                                
                                # Check for function_calls in candidate
                                if hasattr(candidate, 'function_calls'):
                                    print(f"🔍 Candidate has function_calls: {candidate.function_calls}", flush=True)
                                if hasattr(candidate, 'content'):
                                    print(f"🔍 Candidate has content: {type(candidate.content)}", flush=True)
                                    if hasattr(candidate.content, 'parts'):
                                        print(f"🔍 Content has {len(candidate.content.parts)} part(s)", flush=True)
                                        for i, part in enumerate(candidate.content.parts):
                                            part_attrs = [attr for attr in dir(part) if not attr.startswith('_')]
                                            print(f"🔍 Part {i} attributes: {part_attrs[:10]}...", flush=True)
                                            if hasattr(part, 'function_call') and part.function_call:
                                                print(f"🔍 Part {i} has function_call!", flush=True)
                                                # Extract function call immediately
                                                func_call = part.function_call
                                                tool_call = {
                                                    "name": func_call.name if hasattr(func_call, 'name') else getattr(func_call, 'function_name', 'unknown'),
                                                    "id": getattr(func_call, 'id', None),
                                                    "args": func_call.args if hasattr(func_call, 'args') else (func_call.arguments if hasattr(func_call, 'arguments') else {})
                                                }
                                                print(f"🔧 Extracted function call: {tool_call['name']} with args: {tool_call['args']}", flush=True)
                                                # Check for duplicates using helper function (defined in main loop)
                                                # For now, use simple check - will be improved in main loop
                                                is_duplicate = any(
                                                    tc.get('name') == tool_call.get('name') and 
                                                    (tc.get('id') == tool_call.get('id') if tool_call.get('id') else 
                                                     tc.get('args') == tool_call.get('args'))
                                                    for tc in tool_calls
                                                )
                                                if not is_duplicate:
                                                    tool_calls.append(tool_call)
                                                    function_calls_detected = True
                                                    if self.on_tool_call:
                                                        self.on_tool_call(tool_call)
                                                else:
                                                    print(f"🔧 Skipping duplicate tool call in debug section: {tool_call['name']}", flush=True)
                        except Exception as e:
                            print(f"⚠️ Error inspecting chunk: {e}", flush=True)
                            import traceback
                            traceback.print_exc()
                    
                    # Handle text chunks
                    if hasattr(chunk, 'text') and chunk.text:
                        text_chunk = chunk.text
                        full_text += text_chunk
                        
                        # Emit text chunk via callback
                        if self.on_text_chunk:
                            self.on_text_chunk(text_chunk)
                    
                    # Handle function calls (check multiple possible attribute names)
                    function_calls_detected = False
                    
                    # Method 1: Check function_calls attribute
                    if hasattr(chunk, 'function_calls') and chunk.function_calls:
                        print(f"🔧 Found function_calls in chunk: {len(chunk.function_calls)}", flush=True)
                        for func_call in chunk.function_calls:
                            tool_call = {
                                "name": func_call.name if hasattr(func_call, 'name') else getattr(func_call, 'function_name', 'unknown'),
                                "id": getattr(func_call, 'id', None),
                                "args": func_call.args if hasattr(func_call, 'args') else (func_call.arguments if hasattr(func_call, 'arguments') else {})
                            }
                            # Check for duplicates
                            is_duplicate = any(
                                tc.get('name') == tool_call.get('name') and 
                                (tc.get('id') == tool_call.get('id') if tool_call.get('id') else 
                                 tc.get('args') == tool_call.get('args'))
                                for tc in tool_calls
                            )
                            if not is_duplicate:
                                tool_calls.append(tool_call)
                                function_calls_detected = True
                                
                                # Emit tool call via callback
                                if self.on_tool_call:
                                    self.on_tool_call(tool_call)
                            else:
                                print(f"🔧 Skipping duplicate tool call: {tool_call['name']} (id: {tool_call.get('id', 'none')})", flush=True)
                    
                    # Method 2: Check function_call (singular) attribute
                    if hasattr(chunk, 'function_call') and chunk.function_call:
                        print(f"🔧 Found function_call in chunk", flush=True)
                        if not current_tool_call:
                            current_tool_call = {
                                "name": chunk.function_call.name if hasattr(chunk.function_call, 'name') else getattr(chunk.function_call, 'function_name', 'unknown'),
                                "id": getattr(chunk.function_call, 'id', None),
                                "args": {}
                            }
                        
                        # Accumulate function call arguments
                        if hasattr(chunk.function_call, 'args'):
                            current_tool_call["args"].update(chunk.function_call.args)
                        elif hasattr(chunk.function_call, 'arguments'):
                            current_tool_call["args"].update(chunk.function_call.arguments)
                        function_calls_detected = True
                    
                    # Method 3: Check candidates[0].content.parts for function_call (Gemini API format)
                    if hasattr(chunk, 'candidates') and chunk.candidates:
                        for candidate in chunk.candidates:
                            # Check candidate.function_calls directly
                            if hasattr(candidate, 'function_calls') and candidate.function_calls:
                                print(f"🔧 Found function_calls in candidate: {len(candidate.function_calls)}", flush=True)
                                for func_call in candidate.function_calls:
                                    tool_call = {
                                        "name": func_call.name if hasattr(func_call, 'name') else getattr(func_call, 'function_name', 'unknown'),
                                        "id": getattr(func_call, 'id', None),
                                        "args": func_call.args if hasattr(func_call, 'args') else (func_call.arguments if hasattr(func_call, 'arguments') else {})
                                    }
                                    # Check for duplicates
                                    is_duplicate = any(
                                        tc.get('name') == tool_call.get('name') and 
                                        (tc.get('id') == tool_call.get('id') if tool_call.get('id') else 
                                         tc.get('args') == tool_call.get('args'))
                                        for tc in tool_calls
                                    )
                                    if not is_duplicate:
                                        tool_calls.append(tool_call)
                                        function_calls_detected = True
                                        
                                        if self.on_tool_call:
                                            self.on_tool_call(tool_call)
                                    else:
                                        print(f"🔧 Skipping duplicate tool call: {tool_call['name']} (id: {tool_call.get('id', 'none')})", flush=True)
                            
                            # Check candidate.content.parts for function_call
                            if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                                for part in candidate.content.parts:
                                    if hasattr(part, 'function_call') and part.function_call:
                                        print(f"🔧 Found function_call in content.parts!", flush=True)
                                        func_call = part.function_call
                                        tool_call = {
                                            "name": func_call.name if hasattr(func_call, 'name') else getattr(func_call, 'function_name', 'unknown'),
                                            "id": getattr(func_call, 'id', None),
                                            "args": func_call.args if hasattr(func_call, 'args') else (func_call.arguments if hasattr(func_call, 'arguments') else {})
                                        }
                                        # Check for duplicates (by ID if both have IDs, or by name+args)
                                        is_duplicate = any(
                                            tc.get('name') == tool_call.get('name') and 
                                            (tc.get('id') == tool_call.get('id') if tool_call.get('id') else 
                                             tc.get('args') == tool_call.get('args'))
                                            for tc in tool_calls
                                        )
                                        if not is_duplicate:
                                            tool_calls.append(tool_call)
                                            function_calls_detected = True
                                            
                                            if self.on_tool_call:
                                                self.on_tool_call(tool_call)
                                        else:
                                            print(f"🔧 Skipping duplicate tool call: {tool_call['name']} (id: {tool_call.get('id', 'none')})", flush=True)
                
                # Finalize any pending tool call
                if current_tool_call:
                    # Check for duplicates before adding
                    is_duplicate = any(
                        tc.get('name') == current_tool_call.get('name') and 
                        (tc.get('id') == current_tool_call.get('id') if current_tool_call.get('id') else 
                         tc.get('args') == current_tool_call.get('args'))
                        for tc in tool_calls
                    )
                    if not is_duplicate:
                        tool_calls.append(current_tool_call)
                        if self.on_tool_call:
                            self.on_tool_call(current_tool_call)
                    else:
                        print(f"🔧 Skipping duplicate current_tool_call: {current_tool_call.get('name')}", flush=True)
                
                # IMPORTANT: Check the final aggregated response for function calls
                # The streaming chunks might not contain function calls, but the final response might
                # Try to get the aggregated response from the stream
                try:
                    # Some SDKs provide an aggregated response object
                    if hasattr(response_stream, 'response') or hasattr(response_stream, 'aggregate'):
                        final_response_obj = getattr(response_stream, 'response', None) or getattr(response_stream, 'aggregate', None)
                        if final_response_obj:
                            print(f"🔍 Checking final response object for function calls...", flush=True)
                            # Check candidates[0].content.parts for function calls
                            if hasattr(final_response_obj, 'candidates') and final_response_obj.candidates:
                                candidate = final_response_obj.candidates[0]
                                if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                                    for part in candidate.content.parts:
                                        if hasattr(part, 'function_call') and part.function_call:
                                            func_call = part.function_call
                                            print(f"🔧 Found function_call in final response part: {func_call.name if hasattr(func_call, 'name') else 'unknown'}", flush=True)
                                            tool_call = {
                                                "name": func_call.name if hasattr(func_call, 'name') else 'unknown',
                                                "id": getattr(func_call, 'id', None),
                                                "args": func_call.args if hasattr(func_call, 'args') else (func_call.arguments if hasattr(func_call, 'arguments') else {})
                                            }
                                            # Check for duplicates (by ID if both have IDs, or by name+args)
                                            is_duplicate = any(
                                                tc.get('name') == tool_call.get('name') and 
                                                (tc.get('id') == tool_call.get('id') if tool_call.get('id') else 
                                                 tc.get('args') == tool_call.get('args'))
                                                for tc in tool_calls
                                            )
                                            if not is_duplicate:
                                                tool_calls.append(tool_call)
                                                print(f"🔧 Found function call in final response: {tool_call['name']}", flush=True)
                                                if self.on_tool_call:
                                                    self.on_tool_call(tool_call)
                                            else:
                                                print(f"🔧 Skipping duplicate tool call in final response: {tool_call['name']}", flush=True)
                except Exception as e:
                    print(f"⚠️ Error checking final response object: {e}", flush=True)
                
                # Debug: Log tool calls found
                if tool_calls:
                    print(f"🔧 Total tool calls detected: {len(tool_calls)}", flush=True)
                    for tc in tool_calls:
                        print(f"🔧   - {tc.get('name', 'unknown')} with args: {tc.get('args', {})}", flush=True)
                else:
                    print(f"⚠️ No tool calls detected in streaming response", flush=True)
                
                # Call completion callback
                if self.on_complete:
                    self.on_complete(full_text, tool_calls)
                
                return full_text, tool_calls
            finally:
                # Cleanup: Clear response stream reference to allow garbage collection
                if response_stream is not None:
                    # Try to close/cleanup the stream if it has a close method
                    if hasattr(response_stream, 'close'):
                        try:
                            if asyncio.iscoroutinefunction(response_stream.close):
                                await response_stream.close()
                            else:
                                response_stream.close()
                        except:
                            pass
                    # Clear reference
                    self._response_stream = None
                    response_stream = None
                    
        except Exception as e:
            print(f"❌ Gemini streaming error: {e}", flush=True)
            import traceback
            traceback.print_exc()
            # Ensure cleanup even on error
            if 'response_stream' in locals() and response_stream is not None:
                self._response_stream = None
                response_stream = None
            raise


async def stream_gemini_with_tools(
    prompt: str,
    api_key: str,
    model: str,
    tools: List[BaseTool],
    system_prompt: str,
    messages: List[Any],
    socketio=None,
    session_id: Optional[str] = None,
) -> tuple[str, List[Dict[str, Any]]]:
    """
    Convenience function to stream Gemini response with tool support and execution
    
    Args:
        prompt: User prompt
        api_key: Gemini API key
        model: Model name
        tools: List of LangChain tools
        system_prompt: System prompt
        messages: Conversation history
        socketio: Optional SocketIO instance for real-time emission
        session_id: Optional session ID
        
    Returns:
        Tuple of (response_text, tool_calls)
    """
    text_chunks = []
    all_tool_calls = []
    all_tool_results = {}  # Track tool results by tool_id: {result, status, error, duration_ms}
    conversation_messages = messages + [HumanMessage(content=prompt)]
    max_iterations = 5  # Prevent infinite loops
    iteration = 0  # Track current iteration
    
    def on_text_chunk(chunk: str):
        """Emit text chunk via WebSocket"""
        text_chunks.append(chunk)
        if socketio and session_id:
            socketio.emit(
                'agent_stream_chunk',
                {
                    'session_id': session_id,
                    'text_chunk': chunk,
                    'is_final': False
                },
                namespace='/voice',
                room=session_id
            )
    
    def on_tool_call(tool_call: Dict[str, Any]):
        """Emit tool call via WebSocket"""
        all_tool_calls.append(tool_call)
        if socketio and session_id:
            socketio.emit(
                'agent_stream_chunk',
                {'tool_call': tool_call, 'type': 'tool'},
                namespace='/voice',
                room=session_id
            )
    
    handler = None
    try:
        handler = GeminiStreamingHandler(
            api_key=api_key,
            model=model,
            tools=tools,
            system_prompt=system_prompt,
            on_text_chunk=on_text_chunk,
            on_tool_call=on_tool_call,
        )
        
        # Loop: Get response → Execute tools → Feed results back → Get final response
        iteration = 0
        final_text = ""
        
        while iteration < max_iterations:
            iteration += 1
            print(f"🔄 Gemini iteration {iteration}/{max_iterations}", flush=True)
            
            # Stream response from Gemini
            response_text, tool_calls = await handler.stream_response(
                messages=conversation_messages,
                session_id=session_id,
            )
            
            # If there are tool calls, execute them and continue the loop
            if tool_calls and len(tool_calls) > 0:
                print(f"🔧 Executing {len(tool_calls)} tool call(s) from Gemini...", flush=True)
                
                # Execute each tool call
                tool_results = []
                for tc in tool_calls:
                    tool_name = tc.get('name', 'unknown')
                    tool_args = tc.get('args', {})
                    tool_id = tc.get('id', None)
                    
                    # Generate tool_id if missing (needed for tracking)
                    if not tool_id:
                        import uuid
                        tool_id = f"gemini_tool_{uuid.uuid4().hex[:12]}"
                        tc['id'] = tool_id  # Update the tool call dict with generated ID
                        print(f"🔧 Generated tool_id for {tool_name}: {tool_id}", flush=True)
                    
                    # Find the tool
                    tool = None
                    for t in tools:
                        if t.name == tool_name:
                            tool = t
                            break
                    
                    if tool:
                        import time as tool_time
                        tool_start_time = tool_time.time()
                        try:
                            print(f"🔧 Executing tool: {tool_name} with args: {tool_args}", flush=True)
                            # Execute tool (with timeout) - increased from 6s to 15s for MCP tools
                            tool_timeout = 15.0  # MCP tools can take time, especially calendar operations
                            if hasattr(tool, 'ainvoke'):
                                result = await asyncio.wait_for(tool.ainvoke(tool_args), timeout=tool_timeout)
                            else:
                                result = await asyncio.wait_for(asyncio.to_thread(tool.invoke, tool_args), timeout=tool_timeout)
                            
                            tool_duration_ms = (tool_time.time() - tool_start_time) * 1000
                            tool_result = {
                                'name': tool_name,
                                'id': tool_id,
                                'response': str(result)
                            }
                            tool_results.append(tool_result)
                            
                            # Track result for agent monitor
                            if tool_id:
                                all_tool_results[tool_id] = {
                                    'result': str(result),
                                    'status': 'success',
                                    'error': None,
                                    'duration_ms': tool_duration_ms
                                }
                            
                            print(f"✅ Tool {tool_name} completed: {str(result)[:100]}...", flush=True)
                        except asyncio.TimeoutError:
                            tool_duration_ms = (tool_time.time() - tool_start_time) * 1000
                            tool_result = {
                                'name': tool_name,
                                'id': tool_id,
                                'response': "I'm sorry, the operation timed out. Please try again."
                            }
                            tool_results.append(tool_result)
                            
                            # Track timeout for agent monitor
                            if tool_id:
                                all_tool_results[tool_id] = {
                                    'result': None,
                                    'status': 'timeout',
                                    'error': 'Tool execution timed out',
                                    'duration_ms': tool_duration_ms
                                }
                            
                            print(f"⏰ Tool {tool_name} timed out", flush=True)
                        except Exception as e:
                            tool_duration_ms = (tool_time.time() - tool_start_time) * 1000
                            error_str = str(e)
                            tool_result = {
                                'name': tool_name,
                                'id': tool_id,
                                'response': f"I encountered an error: {error_str[:200]}"
                            }
                            tool_results.append(tool_result)
                            
                            # Track error for agent monitor
                            if tool_id:
                                all_tool_results[tool_id] = {
                                    'result': None,
                                    'status': 'failed',
                                    'error': error_str[:200],
                                    'duration_ms': tool_duration_ms
                                }
                            
                            print(f"❌ Tool {tool_name} error: {error_str}", flush=True)
                    else:
                        tool_result = {
                            'name': tool_name,
                            'id': tool_id,
                            'response': f"Tool {tool_name} not found"
                        }
                        tool_results.append(tool_result)
                        print(f"⚠️ Tool {tool_name} not found", flush=True)
                
                # Add tool results to conversation as ToolMessages
                import uuid
                for tr in tool_results:
                    # Generate a unique ID if tool_call_id is None (required by ToolMessage)
                    tool_call_id = tr.get('id', None)
                    if tool_call_id is None:
                        # Generate a unique ID for this tool call
                        tool_call_id = f"gemini_tool_{uuid.uuid4().hex[:12]}"
                        print(f"🔧 Generated tool_call_id for {tr['name']}: {tool_call_id}", flush=True)
                    
                    tool_message = ToolMessage(
                        content=tr['response'],
                        name=tr['name'],
                        tool_call_id=tool_call_id
                    )
                    conversation_messages.append(tool_message)
                
                # Continue loop to get final response with tool results
                print(f"🔄 Feeding tool results back to Gemini for final response...", flush=True)
                print(f"📊 Tool results summary: {len(tool_results)} result(s)", flush=True)
                for tr in tool_results:
                    print(f"   - {tr['name']}: {tr['response'][:100]}...", flush=True)
                # Continue to next iteration (iteration is incremented at start of while loop)
                continue
            else:
                # No tool calls - this is the final response
                final_text = response_text
                break
        
        # Log final response for debugging
        print(f"📝 Gemini final response length: {len(final_text)} chars", flush=True)
        print(f"📝 Gemini final response preview: {final_text[:200]}...", flush=True)
        
        # Emit final chunk marker
        if socketio and session_id:
            socketio.emit(
                'agent_stream_chunk',
                {
                    'session_id': session_id,
                    'text_chunk': '',  # Empty chunk to signal completion
                    'is_final': True
                },
                namespace='/voice',
                room=session_id
            )
        
        # Enrich tool calls with results for agent monitor
        enriched_tool_calls = []
        for tc in all_tool_calls:
            tool_id = tc.get('id')
            enriched_tc = tc.copy()
            if tool_id and tool_id in all_tool_results:
                result_info = all_tool_results[tool_id]
                enriched_tc['result'] = result_info.get('result')
                enriched_tc['status'] = result_info.get('status', 'pending')
                enriched_tc['error'] = result_info.get('error')
                enriched_tc['duration_ms'] = result_info.get('duration_ms')
            enriched_tool_calls.append(enriched_tc)
        
        return final_text, enriched_tool_calls
    finally:
        # Cleanup: Clear stream reference
        if handler is not None:
            handler._response_stream = None
            handler = None
        
        # Clear local accumulators to free memory
        text_chunks.clear()
        all_tool_calls.clear()
        
        # Force garbage collection for large objects
        gc.collect()

