"""
LLM Agent Interaction Monitor
Tracks and stores agent interactions for monitoring and debugging
"""

import time
import json
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field, asdict
from enum import Enum

from .redis_manager import get_redis_manager


class AgentInteractionStatus(Enum):
    """Agent interaction status"""
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ToolCallInfo:
    """Information about a tool call"""
    tool_name: str
    tool_id: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    duration_ms: Optional[float] = None
    status: str = "pending"  # success, failed, timeout


@dataclass
class AgentInteraction:
    """Represents a single agent interaction"""
    request_id: str
    user_id: Optional[str] = None
    user_name: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    provider: Optional[str] = None  # claude, gemini, openai
    model: Optional[str] = None
    user_prompt: str = ""
    agent_response: Optional[str] = None
    tool_calls: List[ToolCallInfo] = field(default_factory=list)
    status: AgentInteractionStatus = AgentInteractionStatus.PENDING
    duration_ms: Optional[float] = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        data = asdict(self)
        data['status'] = self.status.value
        data['timestamp'] = self.timestamp
        data['datetime'] = datetime.fromtimestamp(self.timestamp).isoformat()
        # Convert tool_calls
        data['tool_calls'] = [asdict(tc) for tc in self.tool_calls]
        return data


class AgentMonitor:
    """
    Monitors and stores agent interactions
    """
    
    def __init__(self):
        self.redis = get_redis_manager()
        self.max_interactions = 1000  # Keep last 1000 interactions
        self._memory_recent: List[AgentInteraction] = []
        
        # Check Redis availability
        if self.redis and self.redis.redis_client:
            print(f"✅ AgentMonitor: Redis connection available", flush=True)
        else:
            print(f"⚠️ AgentMonitor: Redis connection NOT available", flush=True)
    
    def track_interaction(
        self,
        request_id: str,
        user_id: Optional[str] = None,
        user_name: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        user_prompt: str = "",
        agent_response: Optional[str] = None,
        tool_calls: List[ToolCallInfo] = None,
        status: AgentInteractionStatus = AgentInteractionStatus.SUCCESS,
        duration_ms: Optional[float] = None,
        error: Optional[str] = None,
        metadata: Dict[str, Any] = None
    ) -> bool:
        """Track an agent interaction"""
        print(f"📊 AgentMonitor: Tracking interaction {request_id} for provider={provider}, user={user_id}", flush=True)
        try:
            print(f"📊 AgentMonitor: Creating interaction object...", flush=True)
            interaction = AgentInteraction(
                request_id=request_id,
                user_id=user_id,
                user_name=user_name,
                provider=provider,
                model=model,
                user_prompt=user_prompt,
                agent_response=agent_response,
                tool_calls=tool_calls or [],
                status=status,
                duration_ms=duration_ms,
                error=error,
                metadata=metadata or {}
            )
            
            # Store in Redis
            print(f"📊 AgentMonitor: Serializing to Redis...", flush=True)
            interaction_key = f"agent_interaction:{request_id}"
            interaction_data = json.dumps(interaction.to_dict())
            print(f"📊 AgentMonitor: Data size: {len(interaction_data)} bytes", flush=True)
            self.redis.set(interaction_key, interaction_data, expire=86400 * 7)  # 7 days
            print(f"📊 AgentMonitor: Stored in Redis", flush=True)

            # Also keep an in-memory fallback for recent interactions
            self._memory_recent.insert(0, interaction)
            if len(self._memory_recent) > self.max_interactions:
                self._memory_recent = self._memory_recent[:self.max_interactions]
            
            # Add to recent interactions list
            recent_key = "agent_interactions:recent"
            self.redis.redis_client.lpush(recent_key, request_id)
            self.redis.redis_client.ltrim(recent_key, 0, self.max_interactions - 1)
            self.redis.redis_client.expire(recent_key, 86400 * 7)  # 7 days
            
            # Add to provider-specific list
            if provider:
                provider_key = f"agent_interactions:provider:{provider}"
                self.redis.redis_client.lpush(provider_key, request_id)
                self.redis.redis_client.ltrim(provider_key, 0, self.max_interactions - 1)
                self.redis.redis_client.expire(provider_key, 86400 * 7)
            
            # Add to user-specific list
            if user_id:
                user_key = f"agent_interactions:user:{user_id}"
                self.redis.redis_client.lpush(user_key, request_id)
                self.redis.redis_client.ltrim(user_key, 0, 100 - 1)  # Keep last 100 per user
                self.redis.redis_client.expire(user_key, 86400 * 7)
            
            print(f"✅ AgentMonitor: Successfully tracked interaction {request_id}", flush=True)
            return True
        except Exception as e:
            print(f"❌ AgentMonitor: Error tracking agent interaction: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return False
    
    def get_interaction(self, request_id: str) -> Optional[AgentInteraction]:
        """Get a specific interaction by request ID"""
        try:
            interaction_key = f"agent_interaction:{request_id}"
            data = self.redis.get(interaction_key)
            if data:
                interaction_dict = json.loads(data)
                # Reconstruct ToolCallInfo objects
                tool_calls = [
                    ToolCallInfo(**tc) for tc in interaction_dict.get('tool_calls', [])
                ]
                interaction_dict['tool_calls'] = tool_calls
                interaction_dict['status'] = AgentInteractionStatus(interaction_dict['status'])
                return AgentInteraction(**{k: v for k, v in interaction_dict.items() if k != 'datetime'})
            return None
        except Exception as e:
            print(f"❌ Error getting interaction: {e}")
            return None
    
    def get_recent_interactions(self, limit: int = 50) -> List[AgentInteraction]:
        """Get recent interactions"""
        try:
            recent_key = "agent_interactions:recent"
            request_ids = self.redis.redis_client.lrange(recent_key, 0, limit - 1)
            print(f"📊 AgentMonitor: Found {len(request_ids) if request_ids else 0} recent interaction IDs", flush=True)
            
            interactions = []
            for req_id in request_ids:
                if isinstance(req_id, bytes):
                    req_id = req_id.decode('utf-8')
                interaction = self.get_interaction(req_id)
                if interaction:
                    interactions.append(interaction)
            
            print(f"📊 AgentMonitor: Retrieved {len(interactions)} interactions", flush=True)
            if interactions:
                return interactions

            # Fallback to in-memory cache if Redis list is empty
            if self._memory_recent:
                print(f"📊 AgentMonitor: Using in-memory fallback ({len(self._memory_recent)} interactions)", flush=True)
                return self._memory_recent[:limit]

            return interactions
        except Exception as e:
            print(f"❌ AgentMonitor: Error getting recent interactions: {e}", flush=True)
            import traceback
            traceback.print_exc()
            if self._memory_recent:
                return self._memory_recent[:limit]
            return []
    
    def get_interactions_by_provider(self, provider: str, limit: int = 50) -> List[AgentInteraction]:
        """Get interactions for a specific provider"""
        try:
            provider_key = f"agent_interactions:provider:{provider}"
            request_ids = self.redis.redis_client.lrange(provider_key, 0, limit - 1)
            
            interactions = []
            for req_id in request_ids:
                if isinstance(req_id, bytes):
                    req_id = req_id.decode('utf-8')
                interaction = self.get_interaction(req_id)
                if interaction:
                    interactions.append(interaction)
            
            return interactions
        except Exception as e:
            print(f"❌ Error getting provider interactions: {e}")
            return []
    
    def get_stats(self) -> Dict[str, Any]:
        """Get overall statistics"""
        try:
            recent = self.get_recent_interactions(limit=1000)
            
            total = len(recent)
            by_provider = {}
            by_status = {}
            total_tool_calls = 0
            total_duration = 0
            
            for interaction in recent:
                # Count by provider
                provider = interaction.provider or "unknown"
                by_provider[provider] = by_provider.get(provider, 0) + 1
                
                # Count by status
                status = interaction.status.value
                by_status[status] = by_status.get(status, 0) + 1
                
                # Count tool calls
                total_tool_calls += len(interaction.tool_calls)
                
                # Sum duration
                if interaction.duration_ms:
                    total_duration += interaction.duration_ms
            
            avg_duration = total_duration / total if total > 0 else 0
            
            return {
                "total_interactions": total,
                "by_provider": by_provider,
                "by_status": by_status,
                "total_tool_calls": total_tool_calls,
                "avg_duration_ms": avg_duration,
                "avg_tool_calls_per_interaction": total_tool_calls / total if total > 0 else 0
            }
        except Exception as e:
            print(f"❌ Error getting stats: {e}")
            return {
                "total_interactions": 0,
                "by_provider": {},
                "by_status": {},
                "total_tool_calls": 0,
                "avg_duration_ms": 0,
                "avg_tool_calls_per_interaction": 0
            }


# Global instance
_agent_monitor = None


def get_agent_monitor() -> AgentMonitor:
    """Get the global agent monitor instance"""
    global _agent_monitor
    if _agent_monitor is None:
        _agent_monitor = AgentMonitor()
    return _agent_monitor

