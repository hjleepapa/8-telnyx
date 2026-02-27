# Tavily Integration

Web search for Convonet agents via [Tavily API](https://docs.tavily.com/).

## Setup

```bash
pip install tavily-python
```

Set environment variable:
```
TAVILY_API_KEY=tvly-your-api-key
```

Get an API key at [tavily.com](https://tavily.com).

## Usage

### Service (Python)

```python
from convonet.tavily import search, search_for_context, is_available

if is_available():
    results = search("current mortgage rates", max_results=5)
    # Or get pre-formatted context for LLM:
    context = search_for_context("What are FHA loan requirements?")
```

### MCP Tool

The `web_search` tool is available to all agents (todo, mortgage, healthcare) when Tavily is configured. The agent can call it when the user asks for:

- Current information (rates, news, weather)
- Research or meeting preparation
- Topics not in the internal knowledge base

## API Credits

- Free tier: 1,000 credits/month
- `basic`/`fast`/`ultra-fast` search: 1 credit
- `advanced` search: 2 credits
