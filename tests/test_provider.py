import http.client
import importlib.util
import io
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def manifest():
    """Parse the flat plugin.yaml fields tests need without a YAML dependency."""
    data = {"requires_env": [], "provides_web_providers": []}
    section = None
    for line in (ROOT / "plugin.yaml").read_text().splitlines():
        if not line.startswith(" ") and ":" in line:
            key, _, value = line.partition(":")
            section = key
            if value.strip():
                data[key] = value.strip().strip('"')
        elif line.strip().startswith("- name:"):
            data[section].append({"name": line.split(":", 1)[1].strip()})
        elif line.strip().startswith("- "):
            data[section].append(line.strip()[2:])
    return data


def load_module(name, path):
    agent = types.ModuleType("agent")
    base = types.ModuleType("agent.web_search_provider")

    class WebSearchProvider:
        pass

    base.WebSearchProvider = WebSearchProvider
    sys.modules.setdefault("agent", agent)
    sys.modules["agent.web_search_provider"] = base
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


provider = load_module("antigravity_search_provider", ROOT / "provider.py")

ENV = {
    "ANTIGRAVITY_SEARCH_BASE_URL": "https://gateway.example",
    "ANTIGRAVITY_SEARCH_API_KEY": "fixture",
}

GROUNDED = {
    "id": "msg_1",
    "type": "message",
    "role": "assistant",
    "model": "gemini-3.1-flash-lite",
    "content": [
        {
            "type": "server_tool_use",
            "id": "srvtoolu_1",
            "name": "web_search",
            "input": {"query": "python 3.14 release"},
        },
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "title": "python.org",
                    "url": "https://www.python.org/downloads/release/python-3140/",
                    "page_age": None,
                },
                {
                    "type": "web_search_result",
                    "title": "peps.python.org",
                    "url": "https://peps.python.org/pep-0745/",
                    "page_age": None,
                },
            ],
        },
        {"type": "text", "text": "Python 3.14 was released "},
        {
            "type": "text",
            "text": "on October 7, 2025",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "cited_text": "on October 7, 2025",
                    "url": "https://www.python.org/downloads/release/python-3140/",
                    "title": "python.org",
                }
            ],
        },
        {"type": "text", "text": "."},
    ],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 70, "output_tokens": 40},
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=None):
        body = json.dumps(self.payload).encode()
        return body if limit is None else body[:limit]


def run_search(payload, query="python 3.14 release", limit=5, env=None):
    with (
        patch.dict("os.environ", env or ENV, clear=True),
        patch.object(provider, "_open_request", return_value=FakeResponse(payload)) as transport,
    ):
        result = provider.AntigravityWebSearchProvider().search(query, limit)
    return result, transport


class RequestTests(unittest.TestCase):
    def test_sends_single_web_search_tool_request_with_plugin_identity(self):
        _, transport = run_search(GROUNDED)
        request = transport.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "https://gateway.example/v1/messages")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.get_header("Authorization"), "Bearer fixture")
        self.assertEqual(request.get_header("Anthropic-version"), "2023-06-01")
        self.assertEqual(body["model"], provider.DEFAULT_MODEL)
        self.assertIs(body["stream"], False)
        self.assertEqual(body["messages"], [{"role": "user", "content": "python 3.14 release"}])
        # The gateway only builds a grounding request when web_search is the sole tool.
        self.assertEqual(body["tools"], [{"type": "web_search_20250305", "name": "web_search"}])
        self.assertNotIn("tool_choice", body)
        self.assertNotIn("system", body)

    def test_user_agent_matches_manifest_version(self):
        manifest_data = manifest()
        _, transport = run_search(GROUNDED)
        request = transport.call_args.args[0]
        self.assertEqual(
            request.get_header("User-agent"),
            f"hermes-antigravity-search/{manifest_data['version']}",
        )

    def test_model_override(self):
        env = dict(ENV, ANTIGRAVITY_SEARCH_MODEL="gemini-3.8-flash")
        _, transport = run_search(GROUNDED, env=env)
        self.assertEqual(json.loads(transport.call_args.args[0].data)["model"], "gemini-3.8-flash")

    def test_base_url_forms_resolve_to_messages_endpoint(self):
        for base in (
            "https://gateway.example",
            "https://gateway.example/",
            "https://gateway.example/v1",
            "https://gateway.example/v1/messages",
        ):
            with self.subTest(base=base):
                _, transport = run_search(GROUNDED, env=dict(ENV, ANTIGRAVITY_SEARCH_BASE_URL=base))
                self.assertEqual(
                    transport.call_args.args[0].full_url, "https://gateway.example/v1/messages"
                )


class NormalizationTests(unittest.TestCase):
    def test_returns_native_shape_with_grounded_snippets(self):
        result, _ = run_search(GROUNDED)
        self.assertTrue(result["success"])
        rows = result["data"]["web"]
        self.assertEqual([row["position"] for row in rows], [1, 2])
        self.assertEqual(rows[0]["url"], "https://www.python.org/downloads/release/python-3140/")
        self.assertEqual(rows[0]["title"], "python.org")
        self.assertEqual(rows[0]["description"], "on October 7, 2025")
        self.assertEqual(rows[1]["description"], "")
        for row in rows:
            self.assertEqual(set(row), {"title", "url", "description", "position"})
        self.assertEqual(result["output"], "Python 3.14 was released on October 7, 2025.")

    def test_limit_dedupe_and_malformed_rows(self):
        payload = json.loads(json.dumps(GROUNDED))
        payload["content"][1]["content"] = [
            None,
            "bad",
            {"type": "web_search_result", "title": "A", "url": "https://a.example"},
            {"type": "web_search_result", "title": "A again", "url": "https://a.example"},
            {"type": "web_search_result", "title": "no url"},
            {"type": "web_search_result", "title": "ftp", "url": "ftp://b.example"},
            {"type": "web_search_result", "title": "creds", "url": "https://u:p@c.example"},
            {"type": "web_search_tool_result_error", "error_code": "unavailable"},
            {"type": "web_search_result", "url": "https://d.example/x"},
            {"type": "web_search_result", "title": "E", "url": "https://e.example"},
        ]
        result, _ = run_search(payload, limit=2)
        rows = result["data"]["web"]
        self.assertEqual([row["url"] for row in rows], ["https://a.example", "https://d.example/x"])
        self.assertEqual([row["position"] for row in rows], [1, 2])
        self.assertEqual(rows[1]["title"], "d.example")

    def test_ungrounded_answer_is_rejected(self):
        payload = {"type": "message", "content": [{"type": "text", "text": "From memory."}]}
        result, _ = run_search(payload)
        self.assertFalse(result["success"])
        self.assertIn("did not perform a web search", result["error"])

    def test_error_and_empty_envelopes_fail(self):
        cases = [
            {"type": "error", "error": {"type": "overloaded_error", "message": "AUDIT_MARKER"}},
            {"error": "AUDIT_MARKER"},
            {"type": "message"},
            [],
            {
                "type": "message",
                "content": [{"type": "web_search_tool_result", "content": []}],
            },
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                result, _ = run_search(payload)
                self.assertFalse(result["success"])
                self.assertNotIn("AUDIT_MARKER", json.dumps(result))

    def test_empty_results_with_grounded_answer_still_succeed(self):
        payload = {
            "type": "message",
            "content": [
                {"type": "web_search_tool_result", "content": []},
                {"type": "text", "text": "Answer"},
            ],
        }
        result, _ = run_search(payload)
        self.assertEqual(result, {"success": True, "data": {"web": []}, "output": "Answer"})


class SafetyTests(unittest.TestCase):
    def test_missing_configuration(self):
        for env, needle in (
            ({}, "ANTIGRAVITY_SEARCH_BASE_URL"),
            ({"ANTIGRAVITY_SEARCH_BASE_URL": "https://g.example"}, "ANTIGRAVITY_SEARCH_API_KEY"),
        ):
            with self.subTest(env=env), patch.dict("os.environ", env, clear=True):
                result = provider.AntigravityWebSearchProvider().search("test")
                self.assertFalse(result["success"])
                self.assertIn(needle, result["error"])

    def test_empty_query_makes_no_request(self):
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch.object(provider, "_open_request") as transport,
        ):
            result = provider.AntigravityWebSearchProvider().search("  ")
        self.assertFalse(result["success"])
        transport.assert_not_called()

    def test_invalid_configuration_is_rejected_without_exposing_values(self):
        cases = [
            ("ANTIGRAVITY_SEARCH_API_KEY", "fixture\nAUDIT_MARKER"),
            ("ANTIGRAVITY_SEARCH_API_KEY", "fixture\x7fAUDIT_MARKER"),
            ("ANTIGRAVITY_SEARCH_BASE_URL", "https://user:AUDIT_MARKER@gateway.example"),
            ("ANTIGRAVITY_SEARCH_BASE_URL", "https://gateway.example:AUDIT_MARKER"),
            ("ANTIGRAVITY_SEARCH_BASE_URL", "https://[AUDIT_MARKER"),
            ("ANTIGRAVITY_SEARCH_BASE_URL", "https://gateway.example/bad path/AUDIT_MARKER"),
            ("ANTIGRAVITY_SEARCH_BASE_URL", "http://gateway.example/AUDIT_MARKER"),
        ]
        for field, value in cases:
            with self.subTest(field=field, case=cases.index((field, value))):
                env = dict(ENV, **{field: value})
                with (
                    patch.dict("os.environ", env, clear=True),
                    patch.object(provider, "_open_request") as transport,
                ):
                    try:
                        result = provider.AntigravityWebSearchProvider().search("test")
                    except (ValueError, http.client.InvalidURL) as exc:
                        self.fail(f"configuration escaped as {type(exc).__name__}")
                self.assertFalse(result["success"])
                self.assertNotIn("AUDIT_MARKER", json.dumps(result))
                transport.assert_not_called()

    def test_request_errors_do_not_expose_values(self):
        errors = [
            ValueError("AUDIT_MARKER"),
            http.client.InvalidURL("AUDIT_MARKER"),
            UnicodeEncodeError("latin-1", "AUDIT_MARKER", 0, 1, "fixture"),
        ]
        for stage in ("Request", "_open_request"):
            owner = provider.urllib.request if stage == "Request" else provider
            for error in errors:
                with (
                    self.subTest(stage=stage, error=type(error).__name__),
                    patch.dict("os.environ", ENV, clear=True),
                    patch.object(owner, stage, side_effect=error),
                ):
                    result = provider.AntigravityWebSearchProvider().search("test")
                self.assertEqual(
                    result,
                    {
                        "success": False,
                        "error": "Antigravity Search request configuration is invalid",
                    },
                )

    def test_http_error_returns_status_only(self):
        error = provider.urllib.error.HTTPError(
            "https://gateway.example/v1/messages",
            429,
            "Too Many Requests",
            {},
            io.BytesIO(b"AUDIT_MARKER"),
        )
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch.object(provider, "_open_request", side_effect=error),
        ):
            result = provider.AntigravityWebSearchProvider().search("test")
        error.close()
        self.assertEqual(
            result, {"success": False, "error": "Antigravity Search returned HTTP 429"}
        )

    def test_network_failure_is_generic(self):
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch.object(
                provider, "_open_request", side_effect=provider.urllib.error.URLError("AUDIT")
            ),
        ):
            result = provider.AntigravityWebSearchProvider().search("test")
        self.assertEqual(result, {"success": False, "error": "Could not reach Antigravity Search"})

    def test_response_size_is_bounded(self):
        response = FakeResponse({})
        response.read = lambda limit=None: b"x" * (provider.MAX_RESPONSE_BYTES + 1)
        with (
            patch.dict("os.environ", ENV, clear=True),
            patch.object(provider, "_open_request", return_value=response),
        ):
            result = provider.AntigravityWebSearchProvider().search("test")
        self.assertEqual(
            result, {"success": False, "error": "Antigravity Search response is too large"}
        )

    def test_local_http_allowed_and_redirects_refused(self):
        env = dict(ENV, ANTIGRAVITY_SEARCH_BASE_URL="http://127.0.0.1:8080")
        result, _ = run_search(GROUNDED, env=env)
        self.assertTrue(result["success"])
        handler = provider._NoRedirectHandler()
        self.assertIsNone(
            handler.redirect_request(None, None, 302, "Found", {}, "https://other.example")
        )

    def test_provider_contract(self):
        search = provider.AntigravityWebSearchProvider()
        self.assertEqual(search.name, "antigravity")
        self.assertTrue(search.supports_search())
        self.assertFalse(search.supports_extract())
        keys = {item["key"] for item in search.get_setup_schema()["env_vars"]}
        manifest_data = manifest()
        self.assertEqual(keys, {item["name"] for item in manifest_data["requires_env"]})
        self.assertEqual(manifest_data["provides_web_providers"], [search.name])
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(search.is_available())
        with patch.dict("os.environ", ENV, clear=True):
            self.assertTrue(search.is_available())


if __name__ == "__main__":
    unittest.main()
