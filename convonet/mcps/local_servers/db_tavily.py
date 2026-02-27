"""
Tavily MCP Server - Web search tool for Convonet agents.

Provides web_search tool for real-time web information.
Requires: TAVILY_API_KEY

Output formatted for voice: summary first, then details. Markdown stripped for TTS.
"""

import re
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv
import os
import sys

load_dotenv()

# Ensure project root is in path for convonet imports
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

mcp = FastMCP("db_tavily")


def _strip_markdown_for_voice(text: str) -> str:
    """Remove markdown that would be spoken aloud (#, *, **, URLs, etc.)."""
    if not text:
        return ""
    # Remove headers (# ## ###)
    t = re.sub(r'^#{1,6}\s*', '', text, flags=re.MULTILINE)
    # Remove **bold** and *italic*
    t = re.sub(r'\*\*(.+?)\*\*', r'\1', t)
    t = re.sub(r'\*(.+?)\*', r'\1', t)
    t = re.sub(r'__(.+?)__', r'\1', t)
    t = re.sub(r'_(.+?)_', r'\1', t)
    # Remove URLs
    t = re.sub(r'https?://[^\s]+', '', t)
    # Remove image/data patterns like :max_bytes(150000):strip_icc()
    t = re.sub(r':max_bytes\([^)]+\)[^\s]*', '', t)
    t = re.sub(r'[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|gif|webp)(?:\s|$)', '', t)
    # Collapse multiple spaces/newlines
    t = re.sub(r'\n{3,}', '\n\n', t)
    t = re.sub(r' {2,}', ' ', t)
    return t.strip()


def _first_sentences(text: str, max_chars: int = 200) -> str:
    """Extract first 1-2 sentences for summary."""
    if not text or len(text) <= max_chars:
        return text
    # Split by sentence endings
    match = re.match(r'^(.+?[.!?])\s+', text[:max_chars + 50])
    return match.group(1).strip() if match else text[:max_chars].rsplit('.', 1)[0] + '.'


@mcp.tool()
def web_search(
    query: str,
    max_results: int = 5,
    topic: str = "general",
) -> str:
    """Search the web for current information using Tavily.

    Use this tool when the user asks for:
    - Current information (mortgage rates, news, weather)
    - Research or meeting preparation
    - Topics that require up-to-date web data
    - Anything not in the internal knowledge base

    Args:
        query: The search query (e.g., "current 30-year mortgage rates", "latest AI news").
        max_results: Number of results to return (1-20, default 5).
        topic: "general" (default), "news" (real-time updates), or "finance" (rates, markets).

    Returns:
        Formatted search results with titles, URLs, and content snippets.
    """
    try:
        from convonet.tavily import search, is_available

        if not is_available():
            return "⚠️ Web search is not available. TAVILY_API_KEY is not configured."

        response = search(
            query=query,
            max_results=max_results,
            search_depth="basic",
            topic=topic,
            include_answer=False,
        )

        results = response.get("results", [])
        if not results:
            return f"❌ No web results found for: '{query}'"

        # Format for voice: summary first, then details. No markdown (#, *, **).
        summary = _first_sentences(_strip_markdown_for_voice(results[0].get("content", "")), max_chars=180)
        parts = [f"Here are the web search results for {query}. Summary: {summary}"]
        parts.append("Here are more details.")
        for i, r in enumerate(results, 1):
            title = _strip_markdown_for_voice(r.get("title", "Untitled"))
            content = _strip_markdown_for_voice(r.get("content", ""))
            if content:
                parts.append(f"Source {i}: {title}. {content}")

        return "\n\n".join(parts)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Web search error: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
