"""
Tool Execution GUI - Flask routes and API endpoints
Provides web-based GUI for viewing tool execution results
"""

from flask import Blueprint, render_template, jsonify, request
from typing import Dict, List, Optional, Any
import json
from datetime import datetime

# Import the tool execution viewer
from .tool_execution_viewer import (
    ToolExecutionTracker,
    ToolStatus,
    _trackers,
    get_tracker,
    REDIS_AVAILABLE,
    _redis_manager
)

tool_gui_bp = Blueprint('tool_gui', __name__, url_prefix='/tool-execution')


@tool_gui_bp.route('/')
def tool_execution_dashboard():
    """Render the tool execution dashboard"""
    return render_template('tool_execution_dashboard.html')


@tool_gui_bp.route('/api/trackers')
def get_all_trackers():
    """Get all active trackers (from memory and Redis)"""
    trackers_data = []
    
    # Get trackers from memory
    for request_id, tracker in _trackers.items():
        summary = tracker.get_summary()
        trackers_data.append({
            "request_id": request_id,
            "user_id": tracker.user_id,
            "total_tools": summary["total_tools"],
            "successful": summary["successful"],
            "failed": summary["failed"],
            "timeout": summary["timeout"],
            "pending": summary["pending"],
            "all_successful": summary["all_successful"],
            "total_duration_ms": summary["total_duration_ms"],
            "start_time": tracker.start_time,
            "end_time": tracker.end_time
        })
    
    # Also load from Redis (if available)
    try:
        from .tool_execution_viewer import _redis_manager, REDIS_AVAILABLE
        if REDIS_AVAILABLE and _redis_manager:
            import json
            # Get all tracker keys from Redis
            tracker_keys = _redis_manager.redis_client.keys("tool_tracker:*")
            for key in tracker_keys:
                # Handle both bytes and string keys
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                request_id = key_str.replace("tool_tracker:", "")
                # Skip if already in memory
                if request_id in _trackers:
                    continue
                
                # Load from Redis
                tracker = get_tracker(request_id)
                if tracker:
                    summary = tracker.get_summary()
                    trackers_data.append({
                        "request_id": request_id,
                        "user_id": tracker.user_id,
                        "total_tools": summary["total_tools"],
                        "successful": summary["successful"],
                        "failed": summary["failed"],
                        "timeout": summary["timeout"],
                        "pending": summary["pending"],
                        "all_successful": summary["all_successful"],
                        "total_duration_ms": summary["total_duration_ms"],
                        "start_time": tracker.start_time,
                        "end_time": tracker.end_time
                    })
    except Exception as e:
        print(f"⚠️ Error loading trackers from Redis: {e}")
    
    # Sort by start_time (most recent first)
    trackers_data.sort(key=lambda x: x.get("start_time", 0), reverse=True)
    
    # Limit to last 100 trackers
    trackers_data = trackers_data[:100]
    
    return jsonify({
        "success": True,
        "trackers": trackers_data,
        "count": len(trackers_data)
    })


@tool_gui_bp.route('/api/tracker/<request_id>')
def get_tracker_details(request_id: str):
    """Get detailed information about a specific tracker"""
    # Try to get from memory first, then Redis
    tracker = get_tracker(request_id)
    
    if not tracker:
        return jsonify({
            "success": False,
            "error": f"Tracker not found for request_id: {request_id}"
        }), 404
    
    summary = tracker.get_summary()
    
    # Convert tool executions to JSON-serializable format
    tools_data = []
    for tool_id, execution in tracker.tools.items():
        # Preserve result type if it's JSON-serializable (dict/list), otherwise convert to string
        result_data = execution.result
        if result_data is not None:
            # If it's already a dict or list, keep it as-is for proper JSON formatting
            if isinstance(result_data, (dict, list)):
                # Check size and truncate if needed (convert to JSON string to check length)
                result_json_str = json.dumps(result_data)
                if len(result_json_str) > 5000:
                    # For large results, we'll truncate in the frontend
                    # Keep as dict/list but mark as large
                    pass  # Frontend will handle truncation
                # Keep as dict/list for proper formatting
            else:
                # For other types (strings, numbers, etc.), convert to string
                result_str = str(result_data)
                # If it looks like JSON, try to parse it
                if result_str.strip().startswith(('{', '[')):
                    try:
                        result_data = json.loads(result_str)  # Parse to dict/list
                    except:
                        result_data = result_str  # Keep as string if not valid JSON
                else:
                    result_data = result_str
                
                # Truncate if too long
                if isinstance(result_data, str) and len(result_data) > 5000:
                    result_data = result_data[:5000] + "... (truncated)"
        else:
            result_data = None
        
        error_str = execution.error
        if error_str and len(error_str) > 5000:
            error_str = error_str[:5000] + "... (truncated)"
        
        tool_data = {
            "tool_id": tool_id,
            "tool_name": execution.tool_name,
            "status": execution.status.value,
            "start_time": execution.start_time,
            "end_time": execution.end_time,
            "duration_ms": execution.duration_ms,
            "arguments": execution.arguments,
            "result": result_data,  # Can be dict, list, or string
            "error": error_str,
            "error_type": execution.error_type,
            "stack_trace": execution.stack_trace
        }
        tools_data.append(tool_data)
    
    return jsonify({
        "success": True,
        "request_id": request_id,
        "user_id": tracker.user_id,
        "summary": summary,
        "tools": tools_data,
        "start_time": tracker.start_time,
        "end_time": tracker.end_time,
        "total_duration_ms": tracker.total_duration_ms
    })


@tool_gui_bp.route('/api/tracker/<request_id>/summary')
def get_tracker_summary(request_id: str):
    """Get summary of a specific tracker"""
    tracker = get_tracker(request_id)
    
    if not tracker:
        return jsonify({
            "success": False,
            "error": f"Tracker not found for request_id: {request_id}"
        }), 404
    
    summary = tracker.get_summary()
    return jsonify({
        "success": True,
        "summary": summary
    })


@tool_gui_bp.route('/api/stats')
def get_overall_stats():
    """Get overall statistics across all trackers (from memory and Redis)"""
    # Collect all trackers (memory + Redis)
    all_trackers = list(_trackers.values())
    
    # Also load from Redis
    try:
        from .tool_execution_viewer import _redis_manager, REDIS_AVAILABLE, get_tracker
        if REDIS_AVAILABLE and _redis_manager:
            import json
            tracker_keys = _redis_manager.redis_client.keys("tool_tracker:*")
            for key in tracker_keys:
                # Handle both bytes and string keys
                key_str = key.decode('utf-8') if isinstance(key, bytes) else key
                request_id = key_str.replace("tool_tracker:", "")
                if request_id not in _trackers:
                    tracker = get_tracker(request_id)
                    if tracker:
                        all_trackers.append(tracker)
    except Exception as e:
        print(f"⚠️ Error loading trackers from Redis for stats: {e}")
    
    total_requests = len(all_trackers)
    total_tools = 0
    total_successful = 0
    total_failed = 0
    total_timeout = 0
    total_duration = 0
    
    tool_name_counts = {}
    tool_name_success = {}
    tool_name_failed = {}
    
    for tracker in all_trackers:
        summary = tracker.get_summary()
        total_tools += summary["total_tools"]
        total_successful += summary["successful"]
        total_failed += summary["failed"]
        total_timeout += summary["timeout"]
        if summary["total_duration_ms"]:
            total_duration += summary["total_duration_ms"]
        
        # Count tools by name
        for tool_id, execution in tracker.tools.items():
            tool_name = execution.tool_name
            tool_name_counts[tool_name] = tool_name_counts.get(tool_name, 0) + 1
            
            if execution.status == ToolStatus.SUCCESS:
                tool_name_success[tool_name] = tool_name_success.get(tool_name, 0) + 1
            elif execution.status in [ToolStatus.FAILED, ToolStatus.TIMEOUT]:
                tool_name_failed[tool_name] = tool_name_failed.get(tool_name, 0) + 1
    
    avg_duration = total_duration / total_requests if total_requests > 0 else 0
    success_rate = (total_successful / total_tools * 100) if total_tools > 0 else 0
    
    return jsonify({
        "success": True,
        "stats": {
            "total_requests": total_requests,
            "total_tools": total_tools,
            "total_successful": total_successful,
            "total_failed": total_failed,
            "total_timeout": total_timeout,
            "success_rate": round(success_rate, 2),
            "avg_duration_ms": round(avg_duration, 2),
            "tool_name_counts": tool_name_counts,
            "tool_name_success": tool_name_success,
            "tool_name_failed": tool_name_failed
        }
    })

