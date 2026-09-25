"""Opt-in authenticated test against a real gateway.

Consumes upstream quota. Enable with:

    ANTIGRAVITY_SEARCH_LIVE_TESTS=1 \
    ANTIGRAVITY_SEARCH_BASE_URL=https://gateway.example \
    ANTIGRAVITY_SEARCH_API_KEY=... \
    python -m unittest tests.test_live -v

Prints only status, counts, and hostnames. Never prints credentials or bodies.
"""

import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
LIVE = os.getenv("ANTIGRAVITY_SEARCH_LIVE_TESTS") == "1"
TRANSIENT = ("HTTP 408", "HTTP 429", "HTTP 5", "Could not reach")


def load_provider():
    if "agent.web_search_provider" not in sys.modules:
        agent = types.ModuleType("agent")
        base = types.ModuleType("agent.web_search_provider")
        base.WebSearchProvider = type("WebSearchProvider", (), {})
        sys.modules.setdefault("agent", agent)
        sys.modules["agent.web_search_provider"] = base
    spec = importlib.util.spec_from_file_location("antigravity_live", ROOT / "provider.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@unittest.skipUnless(LIVE, "set ANTIGRAVITY_SEARCH_LIVE_TESTS=1 to run")
class LiveSearchTests(unittest.TestCase):
    def search(self, query):
        provider = load_provider().AntigravityWebSearchProvider()
        result = provider.search(query, 5)
        error = str(result.get("error", ""))
        if not result.get("success") and any(
            error.startswith(f"Antigravity Search returned {t}") or error.startswith(t)
            for t in TRANSIENT
        ):
            result = provider.search(query, 5)
        return result

    def test_returns_grounded_results(self):
        result = self.search("Python 3.14 release date")
        self.assertTrue(result.get("success"), result.get("error"))
        rows = result["data"]["web"]
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertIn(urlparse(row["url"]).scheme, {"http", "https"})
        print(
            f"\nlive: {len(rows)} rows, output={bool(result.get('output'))}, "
            f"hosts={[urlparse(r['url']).hostname for r in rows]}"
        )


if __name__ == "__main__":
    unittest.main()
