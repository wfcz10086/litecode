---
name: deep-search
description: >
  Deep web research skill. Use whenever the user asks to "research", "search",
  "find information about", "investigate", or needs current data beyond training
  cutoff. Triggers on keywords: search, research, latest, current, find, look up,
  what is the price/status/news of. Supports /effort low|mid|high depth control.
  Always prefer this over single web_search calls for multi-source tasks.
---

# DeepSearch Skill

Multi-source web research with parallel queries, full-page fetch, and structured synthesis.

## When to invoke

- User asks for current data (prices, news, docs, status)
- Topic requires cross-source verification
- Question is ambiguous and benefits from multiple search angles
- User says "research X" or "deep dive on X"

## Effort levels

| Level | Sources | Use when |
|-------|---------|---------| 
| `/effort low`  | 3  | Quick fact lookup |
| `/effort mid`  | 8  | Default — balanced |
| `/effort high` | 15 | Comprehensive report, full page fetch |

Include the effort flag in the query string:
```
deep_search(query="BTC price /effort low")
deep_search(query="LLM architecture trends /effort high")
```

## Multi-query mode

For complex topics, pass explicit sub-queries to run in parallel:
```python
deep_search(
  query="React vs Vue 2026",
  queries=[
    "React performance 2026",
    "Vue 3 ecosystem 2026",
    "React vs Vue benchmark comparison"
  ],
  effort="high"
)
```

## Output structure

The tool returns a Markdown summary:
```
## Key Findings
- bullet 1 [1]
- bullet 2 [2]

## Details
prose with citations...

## Sources
[1] Title — https://...
[2] Title — https://...
```

## Architecture

- Backend: vLLM /v1/chat/completions (query expansion + synthesis)
- Search: web_fetch via domestic search engines (Bing CN / Baidu), config-driven
- Fallback: curl + DuckDuckGo HTML parse (no API key needed)
- Full-page fetch: httpx + inline html-strip (no deps)
- All queries run via asyncio.gather — parallel, not sequential
- Search sources configurable via config.json `search` section
