"""
Tavily Search Service - Web search API for AI agents.

Provides real-time web search via Tavily API. Use for:
- Current information (rates, news, weather)
- Research and meeting preparation
- Fallback when internal knowledge base has no answer

Config: TAVILY_API_KEY (required)
Docs: https://docs.tavily.com/
"""

import os
import logging
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

TAVILY_AVAILABLE = False
_tavily_client = None

try:
    from tavily import TavilyClient
    TAVILY_AVAILABLE = True
except ImportError:
    logger.warning("tavily-python not installed. pip install tavily-python")


def get_tavily_client() -> Optional["TavilyClient"]:
    """Get or create Tavily client singleton."""
    global _tavily_client
    if not TAVILY_AVAILABLE:
        return None
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return None
    if _tavily_client is None:
        try:
            _tavily_client = TavilyClient(api_key=api_key)
        except Exception as e:
            logger.error(f"Tavily client init failed: {e}")
            return None
    return _tavily_client


def search(
    query: str,
    max_results: int = 5,
    search_depth: str = "basic",
    topic: str = "general",
    include_answer: bool = False,
) -> Dict[str, Any]:
    """
    Search the web via Tavily API.

    Args:
        query: Search query string.
        max_results: Max results to return (1-20, default 5).
        search_depth: "basic" (1 credit), "advanced" (2 credits), "fast", "ultra-fast".
        topic: "general", "news", or "finance".
        include_answer: Include LLM-generated answer in response.

    Returns:
        Dict with keys: results, query, response_time, answer (if requested).
        Empty dict on failure.
    """
    client = get_tavily_client()
    if not client:
        return {}

    try:
        response = client.search(
            query=query,
            max_results=min(max(1, max_results), 20),
            search_depth=search_depth,
            topic=topic,
            include_answer=include_answer,
        )
        return response
    except Exception as e:
        logger.error(f"Tavily search failed: {e}")
        return {}


def search_for_context(query: str, max_results: int = 5) -> str:
    """
    Search and return a context string suitable for LLM consumption.
    Uses get_search_context when available, else formats search results.

    Returns:
        Formatted context string, or empty string on failure.
    """
    client = get_tavily_client()
    if not client:
        return ""

    try:
        if hasattr(client, "get_search_context"):
            return client.get_search_context(query=query)
        # Fallback: use search and format
        response = search(query, max_results=max_results)
        return _format_results_as_context(response)
    except Exception as e:
        logger.error(f"Tavily context search failed: {e}")
        return ""


def _format_results_as_context(response: Dict[str, Any]) -> str:
    """Format Tavily search results as context string."""
    results = response.get("results", [])
    if not results:
        return ""

    parts = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "Untitled")
        url = r.get("url", "")
        content = r.get("content", "")
        parts.append(f"[{i}] {title}\nURL: {url}\n{content}")
    return "\n\n".join(parts)


def is_available() -> bool:
    """Check if Tavily service is available (SDK + API key)."""
    return TAVILY_AVAILABLE and bool(os.getenv("TAVILY_API_KEY", "").strip())
