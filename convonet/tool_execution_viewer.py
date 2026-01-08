"""
Tool Execution Viewer
Tracks and displays tool call execution status in a beautiful, easy-to-read format
"""

import time
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import json

try:
    import sentry_sdk
    SENTRY_AVAILABLE = True
except ImportError:
    SENTRY_AVAILABLE = False


class ToolStatus(Enum):
    """Tool execution status"""
    PENDING = "pending"
    EXECUTING = "executing"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"


@dataclass
class ToolExecution:
    """Represents a single tool execution"""
    tool_name: str
    tool_id: str
    status: ToolStatus = ToolStatus.PENDING
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    duration_ms: Optional[float] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    result: Optional[Any] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    stack_trace: Optional[str] = None
    
    def start(self):
        """Mark tool execution as started"""
        self.status = ToolStatus.EXECUTING
        self.start_time = time.time()
    
    def complete(self, result: Any = None):
        """Mark tool execution as completed successfully"""
        self.status = ToolStatus.SUCCESS
        self.end_time = time.time()
        if self.start_time:
            self.duration_ms = (self.end_time - self.start_time) * 1000
        self.result = result
    
    def fail(self, error: str, error_type: Optional[str] = None, stack_trace: Optional[str] = None):
        """Mark tool execution as failed"""
        self.status = ToolStatus.FAILED
        self.end_time = time.time()
        if self.start_time:
            self.duration_ms = (self.end_time - self.start_time) * 1000
        self.error = error
        self.error_type = error_type
        self.stack_trace = stack_trace
    
    def timeout(self):
        """Mark tool execution as timed out"""
        self.status = ToolStatus.TIMEOUT
        self.end_time = time.time()
        if self.start_time:
            self.duration_ms = (self.end_time - self.start_time) * 1000
        self.error = "Tool execution timed out"
        self.error_type = "TimeoutError"


class ToolExecutionTracker:
    """
    Tracks tool executions for a single agent request
    """
    
    def __init__(self, request_id: Optional[str] = None, user_id: Optional[str] = None):
        self.request_id = request_id or f"req_{int(time.time())}"
        self.user_id = user_id
        self.tools: Dict[str, ToolExecution] = {}
        self.start_time = time.time()
        self.end_time: Optional[float] = None
        self.total_duration_ms: Optional[float] = None
    
    def start_tool(self, tool_name: str, tool_id: str, arguments: Dict[str, Any] = None) -> ToolExecution:
        """Start tracking a tool execution"""
        execution = ToolExecution(
            tool_name=tool_name,
            tool_id=tool_id,
            arguments=arguments or {}
        )
        execution.start()
        self.tools[tool_id] = execution
        return execution
    
    def complete_tool(self, tool_id: str, result: Any = None):
        """Mark a tool as completed"""
        if tool_id in self.tools:
            self.tools[tool_id].complete(result)
            # Save to Redis after each tool completion
            try:
                _save_tracker_to_redis(self)
            except:
                pass
    
    def fail_tool(self, tool_id: str, error: str, error_type: Optional[str] = None, stack_trace: Optional[str] = None):
        """Mark a tool as failed"""
        if tool_id in self.tools:
            self.tools[tool_id].fail(error, error_type, stack_trace)
            # Save to Redis after each tool failure
            try:
                _save_tracker_to_redis(self)
            except:
                pass
    
    def timeout_tool(self, tool_id: str):
        """Mark a tool as timed out"""
        if tool_id in self.tools:
            self.tools[tool_id].timeout()
            # Save to Redis after each tool timeout
            try:
                _save_tracker_to_redis(self)
            except:
                pass
    
    def finish(self):
        """Mark the entire request as finished"""
        self.end_time = time.time()
        self.total_duration_ms = (self.end_time - self.start_time) * 1000
        # Save to Redis when finished
        try:
            _save_tracker_to_redis(self)
        except:
            pass
    
    def get_summary(self) -> Dict[str, Any]:
        """Get summary of all tool executions"""
        successful = [t for t in self.tools.values() if t.status == ToolStatus.SUCCESS]
        failed = [t for t in self.tools.values() if t.status == ToolStatus.FAILED]
        timeout = [t for t in self.tools.values() if t.status == ToolStatus.TIMEOUT]
        pending = [t for t in self.tools.values() if t.status in [ToolStatus.PENDING, ToolStatus.EXECUTING]]
        
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "total_tools": len(self.tools),
            "successful": len(successful),
            "failed": len(failed),
            "timeout": len(timeout),
            "pending": len(pending),
            "total_duration_ms": self.total_duration_ms,
            "all_successful": len(failed) == 0 and len(timeout) == 0 and len(pending) == 0
        }


class ToolExecutionViewer:
    """
    Beautiful console viewer for tool executions
    """
    
    # ANSI color codes
    RESET = '\033[0m'
    BOLD = '\033[1m'
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GRAY = '\033[90m'
    
    # Status icons
    SUCCESS_ICON = "✅"
    FAILED_ICON = "❌"
    TIMEOUT_ICON = "⏱️"
    EXECUTING_ICON = "⚙️"
    PENDING_ICON = "⏳"
    
    @staticmethod
    def display_tool_execution(execution: ToolExecution, index: int = 0):
        """Display a single tool execution in a beautiful format"""
        status_colors = {
            ToolStatus.SUCCESS: ToolExecutionViewer.GREEN,
            ToolStatus.FAILED: ToolExecutionViewer.RED,
            ToolStatus.TIMEOUT: ToolExecutionViewer.YELLOW,
            ToolStatus.EXECUTING: ToolExecutionViewer.BLUE,
            ToolStatus.PENDING: ToolExecutionViewer.GRAY,
        }
        
        status_icons = {
            ToolStatus.SUCCESS: ToolExecutionViewer.SUCCESS_ICON,
            ToolStatus.FAILED: ToolExecutionViewer.FAILED_ICON,
            ToolStatus.TIMEOUT: ToolExecutionViewer.TIMEOUT_ICON,
            ToolStatus.EXECUTING: ToolExecutionViewer.EXECUTING_ICON,
            ToolStatus.PENDING: ToolExecutionViewer.PENDING_ICON,
        }
        
        color = status_colors.get(execution.status, ToolExecutionViewer.RESET)
        icon = status_icons.get(execution.status, "❓")
        
        # Header
        status_str = execution.status.value.upper()
        print(f"\n{color}{'='*80}{ToolExecutionViewer.RESET}")
        print(f"{color}{icon} Tool #{index + 1}: {execution.tool_name} {ToolExecutionViewer.RESET}")
        print(f"{color}{'='*80}{ToolExecutionViewer.RESET}")
        
        # Basic info
        print(f"{ToolExecutionViewer.BOLD}Status:{ToolExecutionViewer.RESET} {color}{status_str}{ToolExecutionViewer.RESET}")
        print(f"{ToolExecutionViewer.BOLD}Tool ID:{ToolExecutionViewer.RESET} {execution.tool_id}")
        
        # Timing
        if execution.duration_ms is not None:
            duration_color = ToolExecutionViewer.GREEN if execution.duration_ms < 1000 else ToolExecutionViewer.YELLOW if execution.duration_ms < 3000 else ToolExecutionViewer.RED
            print(f"{ToolExecutionViewer.BOLD}Duration:{ToolExecutionViewer.RESET} {duration_color}{execution.duration_ms:.2f}ms{ToolExecutionViewer.RESET}")
        elif execution.start_time:
            elapsed = (time.time() - execution.start_time) * 1000
            print(f"{ToolExecutionViewer.BOLD}Elapsed:{ToolExecutionViewer.RESET} {ToolExecutionViewer.BLUE}{elapsed:.2f}ms (still running){ToolExecutionViewer.RESET}")
        
        # Arguments
        if execution.arguments:
            print(f"\n{ToolExecutionViewer.BOLD}Arguments:{ToolExecutionViewer.RESET}")
            args_str = json.dumps(execution.arguments, indent=2)
            # Truncate if too long
            if len(args_str) > 500:
                args_str = args_str[:500] + "\n... (truncated)"
            print(f"{ToolExecutionViewer.CYAN}{args_str}{ToolExecutionViewer.RESET}")
        
        # Result or Error
        if execution.status == ToolStatus.SUCCESS and execution.result is not None:
            print(f"\n{ToolExecutionViewer.BOLD}Result:{ToolExecutionViewer.RESET}")
            result_str = str(execution.result)
            if len(result_str) > 500:
                result_str = result_str[:500] + "... (truncated)"
            print(f"{ToolExecutionViewer.GREEN}{result_str}{ToolExecutionViewer.RESET}")
        
        elif execution.status in [ToolStatus.FAILED, ToolStatus.TIMEOUT]:
            print(f"\n{ToolExecutionViewer.BOLD}Error:{ToolExecutionViewer.RESET}")
            if execution.error_type:
                print(f"{ToolExecutionViewer.RED}Type: {execution.error_type}{ToolExecutionViewer.RESET}")
            print(f"{ToolExecutionViewer.RED}{execution.error}{ToolExecutionViewer.RESET}")
            if execution.stack_trace:
                print(f"\n{ToolExecutionViewer.BOLD}Stack Trace:{ToolExecutionViewer.RESET}")
                # Show first 10 lines of stack trace
                lines = execution.stack_trace.split('\n')[:10]
                print(f"{ToolExecutionViewer.RED}{chr(10).join(lines)}{ToolExecutionViewer.RESET}")
                if len(execution.stack_trace.split('\n')) > 10:
                    print(f"{ToolExecutionViewer.GRAY}... (truncated){ToolExecutionViewer.RESET}")
    
    @staticmethod
    def display_summary(tracker: ToolExecutionTracker):
        """Display a summary of all tool executions"""
        summary = tracker.get_summary()
        
        print(f"\n{ToolExecutionViewer.BOLD}{'='*80}{ToolExecutionViewer.RESET}")
        print(f"{ToolExecutionViewer.BOLD}TOOL EXECUTION SUMMARY{ToolExecutionViewer.RESET}")
        print(f"{ToolExecutionViewer.BOLD}{'='*80}{ToolExecutionViewer.RESET}")
        
        # Overall status
        if summary["all_successful"]:
            status_icon = ToolExecutionViewer.SUCCESS_ICON
            status_color = ToolExecutionViewer.GREEN
            status_text = "ALL TOOLS EXECUTED SUCCESSFULLY"
        else:
            status_icon = ToolExecutionViewer.FAILED_ICON
            status_color = ToolExecutionViewer.RED
            status_text = "SOME TOOLS FAILED"
        
        print(f"\n{status_color}{status_icon} {status_text}{ToolExecutionViewer.RESET}\n")
        
        # Statistics
        print(f"{ToolExecutionViewer.BOLD}Request ID:{ToolExecutionViewer.RESET} {summary['request_id']}")
        if summary.get('user_id'):
            print(f"{ToolExecutionViewer.BOLD}User ID:{ToolExecutionViewer.RESET} {summary['user_id']}")
        print(f"{ToolExecutionViewer.BOLD}Total Tools:{ToolExecutionViewer.RESET} {summary['total_tools']}")
        print(f"{ToolExecutionViewer.GREEN}✅ Successful:{ToolExecutionViewer.RESET} {summary['successful']}")
        print(f"{ToolExecutionViewer.RED}❌ Failed:{ToolExecutionViewer.RESET} {summary['failed']}")
        print(f"{ToolExecutionViewer.YELLOW}⏱️  Timeout:{ToolExecutionViewer.RESET} {summary['timeout']}")
        if summary['pending'] > 0:
            print(f"{ToolExecutionViewer.GRAY}⏳ Pending:{ToolExecutionViewer.RESET} {summary['pending']}")
        
        if summary['total_duration_ms']:
            print(f"{ToolExecutionViewer.BOLD}Total Duration:{ToolExecutionViewer.RESET} {summary['total_duration_ms']:.2f}ms")
        
        # List all tools
        print(f"\n{ToolExecutionViewer.BOLD}Tool Execution Details:{ToolExecutionViewer.RESET}")
        for i, (tool_id, execution) in enumerate(tracker.tools.items()):
            ToolExecutionViewer.display_tool_execution(execution, i)
        
        print(f"\n{ToolExecutionViewer.BOLD}{'='*80}{ToolExecutionViewer.RESET}\n")
        
        # Send summary to Sentry
        if SENTRY_AVAILABLE:
            with sentry_sdk.configure_scope() as scope:
                scope.set_tag("request_id", summary['request_id'])
                scope.set_tag("tool_execution_status", "success" if summary['all_successful'] else "failed")
                scope.set_context("tool_execution_summary", summary)
                
                if not summary['all_successful']:
                    sentry_sdk.capture_message(
                        f"Tool execution failed: {summary['failed']} failed, {summary['timeout']} timeout",
                        level="error"
                    )


# Global tracker storage (for request tracking)
_trackers: Dict[str, ToolExecutionTracker] = {}

# Redis storage for persistence
try:
    from .redis_manager import get_redis_manager
    _redis_manager = get_redis_manager()
    REDIS_AVAILABLE = _redis_manager.is_available() if _redis_manager else False
except:
    REDIS_AVAILABLE = False
    _redis_manager = None


def _save_tracker_to_redis(tracker: ToolExecutionTracker):
    """Save tracker to Redis for persistence"""
    if not REDIS_AVAILABLE or not tracker.request_id:
        return
    
    try:
        import json
        tracker_data = {
            'request_id': tracker.request_id,
            'user_id': tracker.user_id,
            'start_time': tracker.start_time,
            'end_time': tracker.end_time,
            'total_duration_ms': tracker.total_duration_ms,
            'tools': {}
        }
        
        # Serialize tool executions
        for tool_id, execution in tracker.tools.items():
            tracker_data['tools'][tool_id] = {
                'tool_name': execution.tool_name,
                'tool_id': execution.tool_id,
                'status': execution.status.value,
                'start_time': execution.start_time,
                'end_time': execution.end_time,
                'duration_ms': execution.duration_ms,
                'arguments': execution.arguments,
                'result': str(execution.result) if execution.result is not None else None,
                'error': execution.error,
                'error_type': execution.error_type,
                'stack_trace': execution.stack_trace
            }
        
        # Store in Redis with 24 hour TTL
        redis_key = f"tool_tracker:{tracker.request_id}"
        tracker_json = json.dumps(tracker_data)
        _redis_manager.redis_client.setex(redis_key, 86400, tracker_json)
    except Exception as e:
        print(f"⚠️ Failed to save tracker to Redis: {e}")


def _load_tracker_from_redis(request_id: str) -> Optional[ToolExecutionTracker]:
    """Load tracker from Redis"""
    if not REDIS_AVAILABLE:
        return None
    
    try:
        import json
        redis_key = f"tool_tracker:{request_id}"
        data = _redis_manager.redis_client.get(redis_key)
        if not data:
            return None
        
        # Handle both bytes and string data from Redis
        if isinstance(data, bytes):
            data = data.decode('utf-8')
        
        tracker_data = json.loads(data)
        tracker = ToolExecutionTracker(tracker_data['request_id'], tracker_data.get('user_id'))
        tracker.start_time = tracker_data.get('start_time')
        tracker.end_time = tracker_data.get('end_time')
        tracker.total_duration_ms = tracker_data.get('total_duration_ms')
        
        # Restore tool executions
        for tool_id, tool_data in tracker_data.get('tools', {}).items():
            execution = ToolExecution(
                tool_name=tool_data['tool_name'],
                tool_id=tool_data['tool_id'],
                arguments=tool_data.get('arguments', {})
            )
            execution.status = ToolStatus(tool_data['status'])
            execution.start_time = tool_data.get('start_time')
            execution.end_time = tool_data.get('end_time')
            execution.duration_ms = tool_data.get('duration_ms')
            execution.result = tool_data.get('result')
            execution.error = tool_data.get('error')
            execution.error_type = tool_data.get('error_type')
            execution.stack_trace = tool_data.get('stack_trace')
            tracker.tools[tool_id] = execution
        
        return tracker
    except Exception as e:
        print(f"⚠️ Failed to load tracker from Redis: {e}")
        return None


def get_tracker(request_id: str) -> Optional[ToolExecutionTracker]:
    """Get a tracker by request ID (checks memory first, then Redis)"""
    # Check memory first
    if request_id in _trackers:
        return _trackers[request_id]
    
    # Try loading from Redis
    tracker = _load_tracker_from_redis(request_id)
    if tracker:
        _trackers[request_id] = tracker  # Cache in memory
        return tracker
    
    return None


def create_tracker(request_id: Optional[str] = None, user_id: Optional[str] = None) -> ToolExecutionTracker:
    """Create a new tool execution tracker"""
    tracker = ToolExecutionTracker(request_id, user_id)
    if tracker.request_id:
        _trackers[tracker.request_id] = tracker
        _save_tracker_to_redis(tracker)  # Save to Redis immediately
    return tracker


def display_tracker(request_id: str):
    """Display a tracker's execution summary"""
    tracker = get_tracker(request_id)
    if tracker:
        ToolExecutionViewer.display_summary(tracker)
    else:
        print(f"Tracker not found for request_id: {request_id}")

