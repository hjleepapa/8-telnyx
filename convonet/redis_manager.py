"""
Redis Manager for Convonet Project
Handles session management, caching, and pub/sub for real-time features
"""

import json
import os
import time
import redis
from typing import Optional, Dict, Any, List
import logging

def safe_int(value: str, default: int = 0) -> int:
    """Safely convert string to int with fallback"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

logger = logging.getLogger(__name__)

class RedisManager:
    """Redis manager for session storage, caching, and pub/sub"""
    
    def __init__(self):
        """Initialize Redis connection"""
        # Configuration hierarchy: Environment variables -> Environment Config -> Defaults
        redis_host = os.getenv('REDIS_HOST')
        redis_port = safe_int(os.getenv('REDIS_PORT', '6379'), 6379)
        redis_password = os.getenv('REDIS_PASSWORD', '')
        redis_db = safe_int(os.getenv('REDIS_DB', '0'), 0)

        if not redis_host:
            try:
                from .environment_config import config
                redis_host = config.REDIS_HOST
                redis_port = config.REDIS_PORT
                redis_password = config.REDIS_PASSWORD
                redis_db = config.REDIS_DB
            except (ImportError, AttributeError):
                redis_host = 'localhost'
        
        logger.info(f"🔌 Connecting to Redis at {redis_host}:{redis_port} (db={redis_db})")
        
        try:
            self.redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                password=redis_password if redis_password else None,
                db=redis_db,
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=5,
                retry_on_timeout=True
            )
            
            # Test connection (keep startup fast for Cloud Run)
            self.redis_client.ping()
            logger.info("✅ Redis connection established")
            
        except Exception as e:
            logger.error(f"❌ Redis connection failed: {e}")
            # Fallback to in-memory storage for development
            self.redis_client = None
            self._fallback_storage = {}
    
    def is_available(self) -> bool:
        """Check if Redis is available"""
        return self.redis_client is not None
    
    # Session Management
    def create_session(self, session_id: str, session_data: Dict[str, Any], ttl: int = 3600) -> bool:
        """Create a new session with TTL"""
        try:
            if self.redis_client:
                self.redis_client.hset(f"session:{session_id}", mapping=session_data)
                self.redis_client.expire(f"session:{session_id}", ttl)
                logger.info(f"✅ Session created: {session_id}")
                return True
            else:
                # Fallback to in-memory
                self._fallback_storage[f"session:{session_id}"] = session_data
                return True
        except Exception as e:
            logger.error(f"❌ Failed to create session: {e}")
            return False
    
    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        """Get session data"""
        try:
            if self.redis_client:
                session_data = self.redis_client.hgetall(f"session:{session_id}")
                return session_data if session_data else None
            else:
                return self._fallback_storage.get(f"session:{session_id}")
        except Exception as e:
            logger.error(f"❌ Failed to get session: {e}")
            return None
    
    def update_session(self, session_id: str, updates: Dict[str, Any]) -> bool:
        """Update session data"""
        try:
            if self.redis_client:
                self.redis_client.hset(f"session:{session_id}", mapping=updates)
                return True
            else:
                # Fallback to in-memory
                if f"session:{session_id}" in self._fallback_storage:
                    self._fallback_storage[f"session:{session_id}"].update(updates)
                    return True
                return False
        except Exception as e:
            logger.error(f"❌ Failed to update session: {e}")
            return False
    
    def delete_session(self, session_id: str) -> bool:
        """Delete session"""
        try:
            if self.redis_client:
                self.redis_client.delete(f"session:{session_id}")
                return True
            else:
                # Fallback to in-memory
                self._fallback_storage.pop(f"session:{session_id}", None)
                return True
        except Exception as e:
            logger.error(f"❌ Failed to delete session: {e}")
            return False
    
    def extend_session(self, session_id: str, ttl: int = 3600) -> bool:
        """Extend session TTL"""
        try:
            if self.redis_client:
                self.redis_client.expire(f"session:{session_id}", ttl)
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to extend session: {e}")
            return False
    
    # Caching
    def cache_user_data(self, user_id: str, data_type: str, data: Any, ttl: int = 300) -> bool:
        """Cache user-specific data (todos, teams, etc.)"""
        try:
            if self.redis_client:
                cache_key = f"user:{user_id}:{data_type}"
                self.redis_client.setex(cache_key, ttl, json.dumps(data))
                logger.info(f"✅ Cached {data_type} for user {user_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to cache data: {e}")
            return False
    
    def get_cached_user_data(self, user_id: str, data_type: str) -> Optional[Any]:
        """Get cached user data"""
        try:
            if self.redis_client:
                cache_key = f"user:{user_id}:{data_type}"
                cached_data = self.redis_client.get(cache_key)
                return json.loads(cached_data) if cached_data else None
            return None
        except Exception as e:
            logger.error(f"❌ Failed to get cached data: {e}")
            return None
    
    def invalidate_user_cache(self, user_id: str, data_type: str = None) -> bool:
        """Invalidate user cache"""
        try:
            if self.redis_client:
                if data_type:
                    cache_key = f"user:{user_id}:{data_type}"
                    self.redis_client.delete(cache_key)
                else:
                    # Delete all user cache
                    pattern = f"user:{user_id}:*"
                    keys = self.redis_client.keys(pattern)
                    if keys:
                        self.redis_client.delete(*keys)
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to invalidate cache: {e}")
            return False
    
    # Simple key-value operations
    def get(self, key: str) -> Optional[str]:
        """Get a value by key"""
        try:
            if self.redis_client:
                return self.redis_client.get(key)
            else:
                # Fallback to in-memory storage
                return self._fallback_storage.get(key)
        except Exception as e:
            logger.error(f"❌ Failed to get key {key}: {e}")
            return None
    
    def set(self, key: str, value: str, expire: int = None) -> bool:
        """Set a key-value pair with optional expiration"""
        try:
            if self.redis_client:
                if expire:
                    self.redis_client.setex(key, expire, value)
                else:
                    self.redis_client.set(key, value)
                return True
            else:
                # Fallback to in-memory storage
                self._fallback_storage[key] = value
                return True
        except Exception as e:
            logger.error(f"❌ Failed to set key {key}: {e}")
            return False
    
    # Pub/Sub for Real-time Notifications
    def publish_team_notification(self, team_id: str, notification: Dict[str, Any]) -> bool:
        """Publish team notification"""
        try:
            if self.redis_client:
                channel = f"team:{team_id}:notifications"
                self.redis_client.publish(channel, json.dumps(notification))
                logger.info(f"✅ Published notification to team {team_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to publish notification: {e}")
            return False
    
    def publish_user_notification(self, user_id: str, notification: Dict[str, Any]) -> bool:
        """Publish user-specific notification"""
        try:
            if self.redis_client:
                channel = f"user:{user_id}:notifications"
                self.redis_client.publish(channel, json.dumps(notification))
                logger.info(f"✅ Published notification to user {user_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to publish user notification: {e}")
            return False
    
    # Rate Limiting
    def check_rate_limit(self, key: str, limit: int, window: int) -> bool:
        """Check if rate limit is exceeded"""
        try:
            if self.redis_client:
                current_count = self.redis_client.incr(key)
                if current_count == 1:
                    self.redis_client.expire(key, window)
                return current_count <= limit
            return True  # Allow if Redis unavailable
        except Exception as e:
            logger.error(f"❌ Rate limit check failed: {e}")
            return True
    
    def get_rate_limit_key(self, identifier: str, action: str) -> str:
        """Generate rate limit key"""
        return f"rate_limit:{identifier}:{action}"
    
    # Analytics and Monitoring
    def track_agent_activity(self, user_id: str, action: str, metadata: Dict[str, Any] = None) -> bool:
        """Track agent activity for analytics"""
        try:
            if self.redis_client:
                activity_key = f"activity:{user_id}:{int(time.time())}"
                activity_data = {
                    'action': action,
                    'timestamp': time.time(),
                    'metadata': metadata or {}
                }
                self.redis_client.setex(activity_key, 86400, json.dumps(activity_data))  # 24h TTL
                return True
            return False
        except Exception as e:
            logger.error(f"❌ Failed to track activity: {e}")
            return False
    
    def get_user_activity(self, user_id: str, hours: int = 24) -> List[Dict[str, Any]]:
        """Get user activity for last N hours"""
        try:
            if self.redis_client:
                pattern = f"activity:{user_id}:*"
                keys = self.redis_client.keys(pattern)
                activities = []
                for key in keys:
                    activity_data = self.redis_client.get(key)
                    if activity_data:
                        activities.append(json.loads(activity_data))
                return sorted(activities, key=lambda x: x['timestamp'], reverse=True)
            return []
        except Exception as e:
            logger.error(f"❌ Failed to get activity: {e}")
            return []


# Global Redis manager instance
redis_manager = RedisManager()

def get_redis_manager() -> RedisManager:
    """Get the global Redis manager instance"""
    return redis_manager

# Convenience functions for easy import
def create_session(session_id: str, session_data: Dict[str, Any], ttl: int = 3600) -> bool:
    """Create a new session"""
    return redis_manager.create_session(session_id, session_data, ttl)

def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Get session data"""
    return redis_manager.get_session(session_id)

def update_session(session_id: str, updates: Dict[str, Any]) -> bool:
    """Update session data"""
    return redis_manager.update_session(session_id, updates)

def delete_session(session_id: str) -> bool:
    """Delete session"""
    return redis_manager.delete_session(session_id)

def cache_user_data(user_id: str, data_type: str, data: Any, ttl: int = 300) -> bool:
    """Cache user data"""
    return redis_manager.cache_user_data(user_id, data_type, data, ttl)

def get_cached_user_data(user_id: str, data_type: str) -> Optional[Any]:
    """Get cached user data"""
    return redis_manager.get_cached_user_data(user_id, data_type)

def publish_team_notification(team_id: str, notification: Dict[str, Any]) -> bool:
    """Publish team notification"""
    return redis_manager.publish_team_notification(team_id, notification)

def publish_user_notification(user_id: str, notification: Dict[str, Any]) -> bool:
    """Publish user notification"""
    return redis_manager.publish_user_notification(user_id, notification)
