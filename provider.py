"""Hermes web-search backend for Google Search grounding via an Anthropic Messages gateway."""

from __future__ import annotations

import http.client
import json
import os
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from agent.web_search_provider import WebSearchProvider

VERSION = "0.1.0"
USER_AGENT = f"hermes-antigravity-search/{VERSION}"
DEFAULT_MODEL = "gemini-3.1-flash-lite"
MAX_LIMIT = 100
MAX_TOKENS = 2048
MAX_DESCRIPTION_CHARS = 500
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 90
LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


def _env(name: str) -> str:
    try:
        from agent.web_search_provider import get_provider_env
    except ImportError:
        return os.getenv(name, "").strip()
    return get_provider_env(name)


def _endpoint(base_url: str) -> str:
    base_url = base_url.rstrip("/")
    if base_url.endswith("/v1/messages"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/messages"
    return f"{base_url}/v1/messages"


def _validate_endpoint(endpoint: str) -> None:
    if any(ord(char) <= 32 or ord(char) == 127 for char in endpoint):
        raise ValueError(
            "ANTIGRAVITY_SEARCH_BASE_URL must not contain whitespace or control characters"
        )
    try:
        parsed = urlparse(endpoint)
        hostname = (parsed.hostname or "").lower()
        _ = parsed.port  # Validate the port without echoing parser exceptions.
    except ValueError:
        raise ValueError("ANTIGRAVITY_SEARCH_BASE_URL must be a valid absolute URL") from None
    if parsed.username is not None or parsed.password is not None or not hostname:
        raise ValueError(
            "ANTIGRAVITY_SEARCH_BASE_URL must be an absolute URL without embedded credentials"
        )
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and hostname in LOCAL_HTTP_HOSTS:
        return
    raise ValueError(
        "ANTIGRAVITY_SEARCH_BASE_URL must use HTTPS; HTTP is allowed only for localhost"
    )


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _open_request(request: urllib.request.Request, timeout: float):
    opener = urllib.request.build_opener(_NoRedirectHandler())
    return opener.open(request, timeout=timeout)


def _valid_url(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    return url


def _snippets_by_url(content: list[Any]) -> dict[str, str]:
    """Collect grounded text segments cited for each source URL."""
    snippets: dict[str, list[str]] = {}
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        for citation in block.get("citations") or []:
            if not isinstance(citation, dict):
                continue
            url = _valid_url(citation.get("url"))
            text = citation.get("cited_text") or block.get("text")
            if url is None or not isinstance(text, str) or not text.strip():
                continue
            parts = snippets.setdefault(url, [])
            text = " ".join(text.split())
            if text not in parts:
                parts.append(text)
    return {url: " ".join(parts)[:MAX_DESCRIPTION_CHARS] for url, parts in snippets.items()}


def _answer_text(content: list[Any]) -> str:
    return "".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ).strip()


def _search_results(content: list[Any]) -> list[Any] | None:
    """Return the grounded result list, or None when no search was performed."""
    found = None
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "web_search_tool_result":
            continue
        results = block.get("content")
        if not isinstance(results, list):
            return None
        found = (found or []) + results
    return found


def _error(message: str) -> dict[str, Any]:
    return {"success": False, "error": message}


class AntigravityWebSearchProvider(WebSearchProvider):
    """Search-only provider backed by Google Search grounding through a Messages gateway."""

    @property
    def name(self) -> str:
        return "antigravity"

    @property
    def display_name(self) -> str:
        return "Antigravity Google Search"

    def is_available(self) -> bool:
        return bool(_env("ANTIGRAVITY_SEARCH_BASE_URL") and _env("ANTIGRAVITY_SEARCH_API_KEY"))

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return False

    def search(self, query: str, limit: int = 5) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return _error("Search query is required")
        try:
            limit = max(1, min(int(limit), MAX_LIMIT))
        except (TypeError, ValueError):
            limit = 5

        base_url = _env("ANTIGRAVITY_SEARCH_BASE_URL")
        api_key = _env("ANTIGRAVITY_SEARCH_API_KEY")
        if not base_url:
            return _error("ANTIGRAVITY_SEARCH_BASE_URL is not set")
        if not api_key:
            return _error("ANTIGRAVITY_SEARCH_API_KEY is not set")
        if any(ord(char) < 32 or ord(char) == 127 for char in api_key):
            return _error("ANTIGRAVITY_SEARCH_API_KEY must not contain control characters")
        endpoint = _endpoint(base_url)
        try:
            _validate_endpoint(endpoint)
        except ValueError as exc:
            return _error(str(exc))

        payload = {
            "model": _env("ANTIGRAVITY_SEARCH_MODEL") or DEFAULT_MODEL,
            "max_tokens": MAX_TOKENS,
            "stream": False,
            "messages": [{"role": "user", "content": query}],
            "tools": [dict(WEB_SEARCH_TOOL)],
        }
        try:
            request = urllib.request.Request(
                endpoint,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "anthropic-version": "2023-06-01",
                    "User-Agent": USER_AGENT,
                },
                method="POST",
            )
            with _open_request(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read(MAX_RESPONSE_BYTES + 1)
                if len(body) > MAX_RESPONSE_BYTES:
                    return _error("Antigravity Search response is too large")
                data = json.loads(body)
        except urllib.error.HTTPError as exc:
            return _error(f"Antigravity Search returned HTTP {exc.code}")
        except (urllib.error.URLError, TimeoutError):
            return _error("Could not reach Antigravity Search")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error("Antigravity Search returned invalid JSON")
        except (ValueError, http.client.InvalidURL):
            return _error("Antigravity Search request configuration is invalid")

        if not isinstance(data, dict):
            return _error("Antigravity Search returned invalid JSON")
        if data.get("type") == "error" or data.get("error") is not None:
            return _error("Antigravity Search returned an error")
        content = data.get("content")
        if not isinstance(content, list):
            return _error("Antigravity Search returned invalid JSON")

        raw_results = _search_results(content)
        if raw_results is None:
            return _error(
                "Antigravity Search did not perform a web search; "
                "check that the configured model supports Google Search grounding"
            )

        snippets = _snippets_by_url(content)
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_results:
            if not isinstance(item, dict) or item.get("type", "web_search_result") != (
                "web_search_result"
            ):
                continue
            url = _valid_url(item.get("url"))
            if url is None or url in seen:
                continue
            seen.add(url)
            rows.append(
                {
                    "title": str(item.get("title") or urlparse(url).netloc),
                    "url": url,
                    "description": snippets.get(url, ""),
                    "position": len(rows) + 1,
                }
            )
            if len(rows) >= limit:
                break

        answer = _answer_text(content)
        if not rows and not answer:
            return _error("Antigravity Search returned no results")
        result: dict[str, Any] = {"success": True, "data": {"web": rows}}
        if answer:
            result["output"] = answer
        return result

    def get_setup_schema(self) -> dict[str, Any]:
        return {
            "name": self.display_name,
            "badge": "grounded",
            "tag": "Google Search grounding through an Anthropic Messages gateway; search only.",
            "env_vars": [
                {
                    "key": "ANTIGRAVITY_SEARCH_BASE_URL",
                    "prompt": "Anthropic Messages gateway base URL",
                },
                {
                    "key": "ANTIGRAVITY_SEARCH_API_KEY",
                    "prompt": "Gateway API key",
                    "password": True,
                },
            ],
        }
