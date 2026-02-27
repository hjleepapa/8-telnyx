"""
Tavily MCP Server - Web search tool for Convonet agents.

Provides web_search tool for real-time web information.
Requires: TAVILY_API_KEY
"""

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

        # Format for agent consumption
        parts = [f"🔍 Web search results for: {query}\n"]
        for i, r in enumerate(results, 1):
            title = r.get("title", "Untitled")
            url = r.get("url", "")
            content = r.get("content", "")
            parts.append(f"\n### {i}. {title}")
            parts.append(f"URL: {url}")
            parts.append(f"Content: {content}")

        return "\n".join(parts)

    except Exception as e:
        import traceback
        traceback.print_exc()
        return f"❌ Web search error: {str(e)}"


if __name__ == "__main__":
    mcp.run(transport="stdio")
