"""Optional Brave search and article-grounded context helpers."""
import asyncio
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

WEB_ENDPOINT = "https://api.search.brave.com/res/v1/web/search"
CONTEXT_ENDPOINT = "https://api.search.brave.com/res/v1/llm/context"


@dataclass(frozen=True)
class BraveHit:
    title: str
    url: str
    description: str = ""


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return ""
    params = [(k, v) for k, v in parse_qsl(parsed.query) if not (
        k.lower().startswith("utm_") or k.lower() in ("fbclid", "gclid")
    )]
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(),
                       parsed.path.rstrip("/") or "/", "", urlencode(params), ""))


class BraveClient:
    def __init__(self, api_key: str):
        self.api_key = api_key

    async def _get(self, endpoint: str, params: dict) -> dict:
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
            for attempt in range(3):
                try:
                    response = await client.get(endpoint, headers=headers, params=params)
                    if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                        try:
                            delay = max(1.0, min(float(response.headers.get("retry-after", "2")), 8.0))
                        except ValueError:
                            delay = 2 ** attempt
                        await asyncio.sleep(delay)
                        continue
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("Brave returned non-object JSON")
                    return payload
                except (httpx.TransportError, ValueError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2 ** attempt)
        raise RuntimeError("Brave request failed")

    async def discover(self, query: str, count: int = 3, freshness: str = "pw") -> list[BraveHit]:
        payload = await self._get(WEB_ENDPOINT, {
            "q": query, "count": max(1, min(count, 20)),
            "freshness": freshness, "safesearch": "moderate",
        })
        results = payload.get("web", {}).get("results", [])
        hits = []
        for result in results if isinstance(results, list) else []:
            if not isinstance(result, dict):
                continue
            url = canonical_url(str(result.get("url", "")))
            title = str(result.get("title", "")).strip()
            if url and title:
                hits.append(BraveHit(title, url, str(result.get("description", ""))))
        return hits

    async def context_for(self, title: str, url: str, max_tokens: int = 4096) -> str | None:
        """Context endpoint searches by query; only use content for the exact article URL."""
        host = urlparse(url).hostname or ""
        if not host:
            return None
        payload = await self._get(CONTEXT_ENDPOINT, {
            "q": f'"{title[:160]}" site:{host}'[:590],
            "maximum_number_of_tokens": max_tokens,
            "maximum_number_of_urls": 10,
            "context_threshold_mode": "balanced",
        })
        grounding = payload.get("grounding", {})
        entries = grounding.get("generic", []) if isinstance(grounding, dict) else []
        target = canonical_url(url)
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict) or canonical_url(str(entry.get("url", ""))) != target:
                continue
            snippets = entry.get("snippets", [])
            if isinstance(snippets, list):
                text = "\n\n".join(s for s in snippets if isinstance(s, str) and s.strip())
                return text[:12000] or None
        return None
