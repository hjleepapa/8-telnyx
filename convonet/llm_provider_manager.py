"""
LLM Provider Manager - Supports multiple LLM providers (Claude, Gemini, OpenAI)
"""

import os
from typing import Optional, Literal
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage

# Provider type
LLMProvider = Literal["claude", "gemini", "openai"]


class LLMProviderManager:
    """Manages multiple LLM providers and provides a unified interface."""
    
    def __init__(self):
        self.providers = {}
        self._initialize_providers()
    
    def _initialize_providers(self):
        """Initialize available LLM providers."""
        # Claude (Anthropic)
        try:
            from langchain_anthropic import ChatAnthropic
            self.providers["claude"] = {
                "class": ChatAnthropic,
                "name": "Claude (Anthropic)",
                "api_key_env": "ANTHROPIC_API_KEY",
                "model_env": "ANTHROPIC_MODEL",
                "default_model": "claude-sonnet-4-20250514",
                "available": bool(os.getenv("ANTHROPIC_API_KEY")),
            }
        except ImportError:
            self.providers["claude"] = {"available": False, "name": "Claude (Anthropic)"}
        
        # Gemini (Google)
        try:
            # Try both possible import paths
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
            except ImportError:
                from langchain_google_vertexai import ChatVertexAI as ChatGoogleGenerativeAI
            
            self.providers["gemini"] = {
                "class": ChatGoogleGenerativeAI,
                "name": "Gemini (Google)",
                "api_key_env": "GOOGLE_API_KEY",
                "model_env": "GOOGLE_MODEL",
                # Default to gemini-2.0-flash (fast and efficient)
                # See https://ai.google.dev/gemini-api/docs/models for all available models
                # Other options: gemini-1.5-pro (most powerful), gemini-1.5-flash, gemini-2.0-flash-lite
                "default_model": "gemini-2.0-flash",
                "available": bool(os.getenv("GOOGLE_API_KEY")),
            }
        except ImportError:
            self.providers["gemini"] = {"available": False, "name": "Gemini (Google)"}
        
        # OpenAI
        try:
            from langchain_openai import ChatOpenAI
            self.providers["openai"] = {
                "class": ChatOpenAI,
                "name": "OpenAI",
                "api_key_env": "OPENAI_API_KEY",
                "model_env": "OPENAI_MODEL",
                "default_model": "gpt-4o",
                "available": bool(os.getenv("OPENAI_API_KEY")),
            }
        except ImportError:
            self.providers["openai"] = {"available": False, "name": "OpenAI"}
    
    def get_available_providers(self) -> list[dict]:
        """Get list of available providers with their status."""
        return [
            {
                "id": provider_id,
                "name": info.get("name", provider_id),
                "available": info.get("available", False),
            }
            for provider_id, info in self.providers.items()
        ]
    
    def create_llm(
        self,
        provider: LLMProvider,
        model: Optional[str] = None,
        temperature: float = 0.0,
        tools: Optional[list] = None,
    ) -> BaseChatModel:
        """
        Create an LLM instance for the specified provider.
        
        Args:
            provider: LLM provider ID ("claude", "gemini", or "openai")
            model: Model name (optional, uses default if not provided)
            temperature: Temperature setting (default: 0.0)
            tools: List of tools to bind (optional)
        
        Returns:
            BaseChatModel instance
        
        Raises:
            ValueError: If provider is not available or API key is missing
        """
        if provider not in self.providers:
            raise ValueError(f"Unknown provider: {provider}")
        
        provider_info = self.providers[provider]
        
        if not provider_info.get("available", False):
            raise ValueError(
                f"Provider '{provider}' is not available. "
                f"Please set {provider_info.get('api_key_env', 'API_KEY')} environment variable."
            )
        
        # Get API key
        api_key = os.getenv(provider_info["api_key_env"])
        if not api_key:
            raise ValueError(
                f"API key not found for {provider}. "
                f"Please set {provider_info['api_key_env']} environment variable."
            )
        
        # Get model name
        model_name = model or os.getenv(
            provider_info["model_env"],
            provider_info["default_model"]
        )
        
        # Create LLM instance
        llm_class = provider_info["class"]
        
        try:
            if provider == "claude":
                llm = llm_class(
                    model=model_name,
                    api_key=api_key,
                    temperature=temperature,
                )
            elif provider == "gemini":
                print(f"🤖 Initializing Gemini (Google) with provider: {provider}")
                print(f"🤖 Gemini model: {model_name}")
                print(f"🤖 Gemini API key present: {bool(api_key)}")
                
                # Try google_api_key first, then api_key
                try:
                    llm = llm_class(
                        model=model_name,
                        google_api_key=api_key,
                        temperature=temperature,
                        # Force tool calling for Gemini
                        convert_system_message_to_human=True,  # Some Gemini models need this
                    )
                    print(f"✅ Gemini LLM created successfully with google_api_key")
                except TypeError as e:
                    print(f"⚠️ google_api_key failed, trying api_key: {e}")
                    # Fallback to api_key if google_api_key doesn't work
                    try:
                        llm = llm_class(
                            model=model_name,
                            api_key=api_key,
                            temperature=temperature,
                            convert_system_message_to_human=True,
                        )
                        print(f"✅ Gemini LLM created successfully with api_key")
                    except Exception as e2:
                        print(f"❌ Both google_api_key and api_key failed: {e2}")
                        import traceback
                        print(f"❌ Traceback: {traceback.format_exc()}")
                        raise
                except Exception as e:
                    print(f"❌ Failed to create Gemini LLM: {e}")
                    import traceback
                    print(f"❌ Traceback: {traceback.format_exc()}")
                    raise
            elif provider == "openai":
                llm = llm_class(
                    model=model_name,
                    api_key=api_key,
                    temperature=temperature,
                )
            else:
                raise ValueError(f"Unsupported provider: {provider}")
            
            # Bind tools if provided
            if tools:
                if provider == "gemini":
                    # Gemini tool binding can hang or take a very long time
                    # Check if we should skip tool binding entirely (environment variable)
                    skip_gemini_tools = os.getenv('SKIP_GEMINI_TOOL_BINDING', 'false').lower() == 'true'
                    
                    if skip_gemini_tools:
                        print(f"⚠️ Skipping Gemini tool binding (SKIP_GEMINI_TOOL_BINDING=true)")
                        print(f"⚠️ Tool calls will NOT be available for Gemini - agent will respond with text only")
                    else:
                        # Use a timeout and make it optional if it fails
                        import sys
                        print(f"🔧 Binding {len(tools)} tools to Gemini LLM (this may take a moment)...", flush=True)
                        sys.stdout.flush()
                        print(f"💡 If this hangs, set SKIP_GEMINI_TOOL_BINDING=true to skip tool binding", flush=True)
                        sys.stdout.flush()
                        try:
                            import threading
                            import time
                            
                            binding_result = {'success': False, 'llm': llm, 'error': None, 'done': False}
                            
                            def bind_tools_sync():
                                """Bind tools synchronously in a thread"""
                                import sys
                                try:
                                    print(f"🧵 Thread: About to call llm.bind_tools()...", flush=True)
                                    sys.stdout.flush()
                                    start_time = time.time()
                                    binding_result['llm'] = llm.bind_tools(tools=tools)
                                    elapsed = time.time() - start_time
                                    print(f"🧵 Thread: bind_tools() completed in {elapsed:.2f}s", flush=True)
                                    sys.stdout.flush()
                                    binding_result['success'] = True
                                except Exception as e:
                                    print(f"🧵 Thread: bind_tools() raised exception: {e}", flush=True)
                                    sys.stdout.flush()
                                    binding_result['error'] = e
                                finally:
                                    binding_result['done'] = True
                                    print(f"🧵 Thread: bind_tools_sync() finished, done=True", flush=True)
                                    sys.stdout.flush()
                            
                            print(f"🚀 Starting bind_tools thread...", flush=True)
                            sys.stdout.flush()
                            bind_thread = threading.Thread(target=bind_tools_sync, daemon=True)
                            bind_thread.start()
                            print(f"⏳ Waiting for bind_tools with 5s timeout...", flush=True)
                            sys.stdout.flush()
                            bind_thread.join(timeout=5.0)  # 5 second timeout for Gemini tool binding
                            
                            if not binding_result['done']:
                                print(f"⏱️ Gemini tool binding timed out after 5 seconds", flush=True)
                                sys.stdout.flush()
                                print(f"⚠️ Continuing without tool binding - Gemini tool calls will be disabled", flush=True)
                                sys.stdout.flush()
                                print(f"💡 Tip: Set SKIP_GEMINI_TOOL_BINDING=true to skip binding entirely and speed up initialization", flush=True)
                                sys.stdout.flush()
                            elif binding_result['error']:
                                print(f"⚠️ Gemini tool binding failed: {binding_result['error']}", flush=True)
                                sys.stdout.flush()
                                print(f"⚠️ Continuing without tool binding - tool calls may not work properly", flush=True)
                                sys.stdout.flush()
                            elif binding_result['success']:
                                llm = binding_result['llm']
                                print(f"✅ Successfully bound tools to Gemini LLM", flush=True)
                                sys.stdout.flush()
                            else:
                                print(f"⚠️ Gemini tool binding returned no result, continuing without binding", flush=True)
                                sys.stdout.flush()
                        except Exception as tool_error:
                            print(f"⚠️ Warning: Failed to bind tools to Gemini LLM: {tool_error}", flush=True)
                            sys.stdout.flush()
                            import traceback
                            traceback.print_exc()
                            print(f"⚠️ Continuing without tool binding - tool calls may not work properly", flush=True)
                            sys.stdout.flush()
                else:
                    # For Claude and OpenAI, tool binding is usually fast and reliable
                    try:
                        print(f"🔧 Binding {len(tools)} tools to {provider} LLM...")
                        llm = llm.bind_tools(tools=tools)
                        print(f"✅ Successfully bound tools to {provider} LLM")
                    except Exception as tool_error:
                        print(f"⚠️ Warning: Failed to bind tools to {provider} LLM: {tool_error}")
                        print(f"⚠️ Continuing without tool binding - tool calls may not work properly")
            
            return llm
        except Exception as e:
            error_msg = f"Failed to create {provider} LLM: {str(e)}"
            print(f"❌ {error_msg}")
            import traceback
            print(f"❌ Traceback: {traceback.format_exc()}")
            raise ValueError(error_msg)
    
    def get_default_provider(self) -> Optional[LLMProvider]:
        """Get the default provider (first available)."""
        for provider_id, info in self.providers.items():
            if info.get("available", False):
                return provider_id
        return None
    
    def is_provider_available(self, provider: LLMProvider) -> bool:
        """Check if a provider is available."""
        return self.providers.get(provider, {}).get("available", False)


# Global instance
_llm_provider_manager = None


def get_llm_provider_manager() -> LLMProviderManager:
    """Get the global LLM provider manager instance."""
    global _llm_provider_manager
    if _llm_provider_manager is None:
        _llm_provider_manager = LLMProviderManager()
    return _llm_provider_manager

