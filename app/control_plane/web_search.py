"""Web search for deep research — a guarded, deny-by-default external-service
client (the M13 egress pattern).

NO search engine is vendored. This calls a generic JSON search API over the
hardened outbound URL guard; the operator points ``WEB_SEARCH`` at a non-AGPL
provider or a separately-run service. Nothing here embeds SearXNG or any copyleft
engine — that stays excluded by governance (docs/12 "Excluded by Default",
docs/13 §3), and adding web research does not relax it.

Security model mirrors M13 integrations:
  - every call routes through validate_outbound_url + guarded_open (SSRF-hardened:
    deny-by-default host allow-list, no private/link-local/metadata IPs, DNS-pin);
  - deny-by-default: unconfigured -> no client -> web mode is simply unavailable;
  - content policy: results are UNTRUSTED, model-facing sources; the caller audits
    shape only (never the query text or the result text). Prompt-injection risk
    from fetched content is handled by treating all model output as untrusted and
    executing nothing from it (same stance as the retrieval corpus).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from urllib.parse import quote

from app.control_plane.outbound import (
    OutboundReject,
    guarded_open,
    validate_outbound_url,
)

logger = logging.getLogger(__name__)

_DEFAULT_TOP_K = 5
_MAX_SNIPPET = 500
_MAX_TITLE = 200
_MAX_RESULT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class WebResult:
    title: str
    url: str
    snippet: str


@dataclass(frozen=True)
class WebSearchConfig:
    provider: str
    url: str                    # search endpoint template, e.g. https://api.x.com/search?q=
    allowed_hosts: frozenset    # deny-by-default host allow-list for the guard
    api_key: str = ""
    api_key_header: str = "Authorization"
    top_k: int = _DEFAULT_TOP_K
    timeout: float = 10.0


def parse_web_search_config(raw: str | None) -> "WebSearchConfig | None":
    """Parse the WEB_SEARCH env JSON into a config, or None (web mode disabled).

    {"provider","url","host","api_key","api_key_header","top_k"}. ``host`` is the
    endpoint's hostname and becomes the guard's sole allowed host. Malformed or
    absent config -> None -> deny by default.
    """
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        logger.warning("WEB_SEARCH is not valid JSON — web research disabled.")
        return None
    if not isinstance(data, dict):
        return None
    url = data.get("url")
    host = data.get("host")
    if not isinstance(url, str) or not url:
        return None
    if not isinstance(host, str) or not host:
        return None
    top_k = data.get("top_k", _DEFAULT_TOP_K)
    return WebSearchConfig(
        provider=str(data.get("provider", "web")),
        url=url,
        allowed_hosts=frozenset({host}),
        api_key=str(data.get("api_key", "")),
        api_key_header=str(data.get("api_key_header", "Authorization")),
        top_k=int(top_k) if isinstance(top_k, int) and top_k > 0 else _DEFAULT_TOP_K,
    )


class WebSearchClient:
    """Runs one guarded search query and returns untrusted results (never raises)."""

    def __init__(self, config: WebSearchConfig, *, opener=None) -> None:
        self._cfg = config
        # `opener(url, headers) -> response` is injectable for tests; the default
        # is the hardened guard, so no test ever opens a real socket.
        self._open = opener or self._guarded

    def _guarded(self, url: str, headers: dict):
        target = validate_outbound_url(url, allowed_hosts=self._cfg.allowed_hosts)
        return guarded_open(
            target, method="GET", headers=headers, body=None,
            timeout=self._cfg.timeout, max_response_bytes=_MAX_RESULT_BYTES,
        )

    def search(self, query: str) -> list[WebResult]:
        # Append the URL-encoded query to the configured endpoint template. If the
        # template does not already end in a separator, add "&q=".
        sep = "" if self._cfg.url.endswith(("=", "?", "&", "/")) else "&q="
        url = self._cfg.url + sep + quote(query)
        headers = {"Accept": "application/json"}
        if self._cfg.api_key:
            headers[self._cfg.api_key_header] = self._cfg.api_key
        try:
            resp = self._open(url, headers)
        except OutboundReject:
            return []                       # guard blocked (bad host / rebinding / scheme)
        except (TimeoutError, OSError):
            return []
        if getattr(resp, "status", 500) // 100 != 2:
            return []
        return self._parse(getattr(resp, "body", b""))

    def _parse(self, body: bytes) -> list[WebResult]:
        try:
            data = json.loads(body)
        except (ValueError, json.JSONDecodeError):
            return []
        items = self._extract_items(data)
        if not isinstance(items, list):
            return []
        out: list[WebResult] = []
        for it in items[: self._cfg.top_k]:
            if not isinstance(it, dict):
                continue
            url = it.get("url") or it.get("link") or it.get("href") or ""
            if not isinstance(url, str) or not url:
                continue
            title = it.get("title") or it.get("name") or url
            snippet = (it.get("snippet") or it.get("content")
                       or it.get("description") or it.get("text") or "")
            out.append(WebResult(
                title=str(title)[:_MAX_TITLE],
                url=url,
                snippet=str(snippet)[:_MAX_SNIPPET],
            ))
        return out

    @staticmethod
    def _extract_items(data):
        """Accept the common result shapes without coupling to one provider."""
        if isinstance(data, list):
            return data
        if not isinstance(data, dict):
            return None
        for key in ("results", "organic", "items", "data"):
            value = data.get(key)
            if isinstance(value, list):
                return value
        # Bing-style: {"webPages": {"value": [...]}}
        web_pages = data.get("webPages")
        if isinstance(web_pages, dict) and isinstance(web_pages.get("value"), list):
            return web_pages["value"]
        return None
