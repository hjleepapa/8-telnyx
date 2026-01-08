# Tool Execution Dashboard Guide

## Overview

The Tool Execution Dashboard is a web-based UI tool for monitoring, troubleshooting, and analyzing tool call requests, responses, and errors. It provides real-time visibility into how tools are being executed by the AI agents.

## Access

**URL:** `/tool-execution/` or `/tool-execution`

**Example:** `https://www.convonetai.com/tool-execution/`

## Features

### 1. Real-Time Statistics
- **Successful Tools**: Count of tools that completed successfully
- **Failed Tools**: Count of tools that failed with errors
- **Timeout Tools**: Count of tools that timed out
- **Success Rate**: Percentage of successful tool executions
- **Total Requests**: Total number of agent requests tracked

### 2. Visual Overview
- **Doughnut Chart**: Visual representation of tool execution status distribution
- Updates automatically every 5 seconds

### 3. Request Tracking
- Lists all recent agent requests with tool executions
- Shows summary for each request:
  - Total tools executed
  - Successful/Failed/Timeout counts
  - Total duration
  - Tool names used

### 4. Detailed Tool Execution View
- Click on any request to see detailed tool execution information:
  - **Tool Name**: Name of the tool that was called
  - **Status**: success, failed, timeout, executing, pending
  - **Duration**: How long the tool took to execute
  - **Arguments**: Input parameters passed to the tool
  - **Result**: Output/response from the tool (if successful)
  - **Error**: Error message (if failed)
  - **Error Type**: Type of exception (if failed)
  - **Stack Trace**: Full stack trace (if failed)

### 5. Search and Filtering
- **Search Bar**: Search by:
  - Request ID
  - User ID
  - Tool name
- **Status Filter**: Filter by:
  - All Status
  - ✅ Success (all tools succeeded)
  - ❌ Failed (some tools failed)
  - ⏱️ Timeout (some tools timed out)

## How It Works

### Automatic Tracking

Tool execution is automatically tracked when:
1. An agent request is made (creates a `request_id`)
2. Tools are called in the `tools_node` function
3. Tools complete, fail, or timeout

### Data Storage

- **In-Memory**: Active trackers stored in `_trackers` dictionary
- **Redis**: Trackers saved to Redis with 24-hour TTL for persistence
- **Dashboard**: Loads from both memory and Redis

### Integration Points

1. **Agent State**: `request_id` is added to `AgentState` when agent executes
2. **Tools Node**: `tools_node` function in `assistant_graph_todo.py`:
   - Creates/gets tracker for the request
   - Tracks tool start, completion, failures, timeouts
   - Saves to Redis after each tool execution

## Troubleshooting Use Cases

### Example 1: Mortgage Bot Not Calling Tools

**Problem:** User says "I want to apply for a mortgage" but agent doesn't call `get_mortgage_application_status`

**How to Debug:**
1. Open Tool Execution Dashboard
2. Search for recent requests with "mortgage" in tool names
3. Check if `get_mortgage_application_status` appears in the request
4. If not, check the agent logs to see why tools weren't called
5. If yes, check the tool execution details:
   - Did it start? (status: executing)
   - Did it complete? (status: success/failed)
   - What was the result/error?

### Example 2: Tool Execution Errors

**Problem:** Tool calls are failing with database errors

**How to Debug:**
1. Filter by "❌ Failed" status
2. Click on a failed request
3. Review the tool execution details:
   - **Error**: Full error message
   - **Error Type**: Exception type (e.g., `psycopg2.errors.InvalidTextRepresentation`)
   - **Stack Trace**: Full stack trace showing where error occurred
   - **Arguments**: What parameters were passed to the tool

### Example 3: Slow Tool Execution

**Problem:** Tools are taking too long to execute

**How to Debug:**
1. Review tool execution details
2. Check **Duration** for each tool
3. Tools taking > 3 seconds are highlighted in yellow/red
4. Identify which tools are slowest
5. Check if there are patterns (e.g., all database tools are slow)

### Example 4: Tool Timeouts

**Problem:** Tools are timing out

**How to Debug:**
1. Filter by "⏱️ Timeout" status
2. Review timeout tools:
   - Which tools are timing out?
   - Are they consistently timing out or intermittent?
   - Check tool duration - if it's exactly 6.0s, it hit the timeout limit

## API Endpoints

### GET `/tool-execution/api/stats`
Get overall statistics across all trackers.

**Response:**
```json
{
  "success": true,
  "stats": {
    "total_requests": 50,
    "total_tools": 120,
    "total_successful": 110,
    "total_failed": 8,
    "total_timeout": 2,
    "success_rate": 91.67,
    "avg_duration_ms": 1250.5,
    "tool_name_counts": {
      "get_mortgage_application_status": 15,
      "create_mortgage_application": 10,
      ...
    }
  }
}
```

### GET `/tool-execution/api/trackers`
Get all recent trackers (last 100).

**Response:**
```json
{
  "success": true,
  "trackers": [
    {
      "request_id": "req_1234567890",
      "user_id": "2893e279-2242-4b65-97b4-c76caa617de5",
      "total_tools": 2,
      "successful": 2,
      "failed": 0,
      "timeout": 0,
      "all_successful": true,
      "total_duration_ms": 1250.5,
      "start_time": 1234567890.123,
      "end_time": 1234567891.373
    },
    ...
  ],
  "count": 50
}
```

### GET `/tool-execution/api/tracker/<request_id>`
Get detailed information about a specific tracker.

**Response:**
```json
{
  "success": true,
  "request_id": "req_1234567890",
  "user_id": "2893e279-2242-4b65-97b4-c76caa617de5",
  "summary": {
    "request_id": "req_1234567890",
    "total_tools": 2,
    "successful": 2,
    "failed": 0,
    "timeout": 0,
    "all_successful": true,
    "total_duration_ms": 1250.5
  },
  "tools": [
    {
      "tool_id": "toolu_01ABC123",
      "tool_name": "get_mortgage_application_status",
      "status": "success",
      "start_time": 1234567890.123,
      "end_time": 1234567890.456,
      "duration_ms": 333.0,
      "arguments": {
        "user_id": "2893e279-2242-4b65-97b4-c76caa617de5"
      },
      "result": "{\"success\": true, \"application_id\": \"...\"}",
      "error": null,
      "error_type": null,
      "stack_trace": null
    },
    ...
  ]
}
```

## Technical Details

### Tool Execution Lifecycle

1. **Request Created**: When `_run_agent_async` is called, a `request_id` is generated
2. **State Initialized**: `request_id` is added to `AgentState`
3. **Tool Called**: When `tools_node` executes:
   - Gets or creates tracker for `request_id`
   - Calls `tracker.start_tool(tool_name, tool_id, arguments)`
4. **Tool Executes**: Tool runs (may take time)
5. **Tool Completes**: One of:
   - `tracker.complete_tool(tool_id, result)` - Success
   - `tracker.fail_tool(tool_id, error, error_type, stack_trace)` - Failure
   - `tracker.timeout_tool(tool_id)` - Timeout
6. **Tracker Saved**: After each tool execution, tracker is saved to Redis
7. **Request Finished**: `tracker.finish()` is called, final save to Redis

### Data Persistence

- **Memory**: Fast access for active requests
- **Redis**: Persists for 24 hours, survives server restarts
- **TTL**: 86400 seconds (24 hours)

### Performance

- **Real-time Updates**: Dashboard refreshes every 5 seconds
- **Lazy Loading**: Tool details loaded only when request is clicked
- **Truncation**: Large results/errors truncated to 5000 chars for UI

## Best Practices

1. **Monitor Regularly**: Check dashboard during development and debugging
2. **Filter by Status**: Use status filter to quickly find failed/timeout tools
3. **Search by Tool Name**: Use search to find all executions of a specific tool
4. **Review Error Details**: Always check stack traces for failed tools
5. **Check Duration**: Identify slow tools that may need optimization

## Example Workflow

1. User reports: "Mortgage bot isn't working"
2. Open Tool Execution Dashboard
3. Search for "mortgage" in recent requests
4. Click on the most recent request
5. Review tool execution details:
   - Did `get_mortgage_application_status` execute?
   - What was the result?
   - Were there any errors?
6. If errors found:
   - Check error message
   - Review stack trace
   - Check tool arguments (was `user_id` correct?)
7. Fix the issue based on error details
8. Test again and verify in dashboard

## Integration Status

✅ **Fully Integrated:**
- Tool execution tracking in `tools_node`
- Request ID passed through `AgentState`
- Redis persistence
- Dashboard UI with search/filter
- Real-time updates

✅ **Works For:**
- All agent types (TodoAgent, MortgageAgent)
- All tool types (MCP tools, database tools, etc.)
- All providers (Claude, Gemini, OpenAI)

## Future Enhancements

- Export tool execution data to CSV/JSON
- Email alerts for tool failures
- Tool execution performance metrics
- Historical trend analysis
- Tool usage analytics
