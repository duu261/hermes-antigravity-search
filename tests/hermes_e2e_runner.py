"""Real Hermes runtime probe used by test_hermes_e2e.

Loads the plugin through Hermes' PluginManager in a disposable HERMES_HOME,
selects it as ``web.search_backend``, and dispatches the native ``web_search``
tool through the real registry. Only the plugin's HTTP transport is stubbed.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN = "web-antigravity"
ENV_KEYS = (
    "HERMES_HOME",
    "HERMES_BUNDLED_PLUGINS",
    "ANTIGRAVITY_SEARCH_BASE_URL",
    "ANTIGRAVITY_SEARCH_API_KEY",
)
PAYLOAD = {
    "type": "message",
    "content": [
        {"type": "server_tool_use", "id": "t1", "name": "web_search", "input": {"query": "q"}},
        {
            "type": "web_search_tool_result",
            "tool_use_id": "t1",
            "content": [
                {"type": "web_search_result", "title": "Example", "url": "https://example.com/a"}
            ],
        },
        {
            "type": "text",
            "text": "Grounded fact",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "cited_text": "Grounded fact",
                    "url": "https://example.com/a",
                    "title": "Example",
                }
            ],
        },
    ],
}


def _run_probe(home: Path) -> None:
    target = home / "plugins" / PLUGIN
    shutil.copytree(REPO, target, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    (home / "logs").mkdir()
    (home / "config.yaml").write_text(
        f"plugins:\n  enabled:\n    - {PLUGIN}\n"
        "web:\n  search_backend: antigravity\n  keyless_rescue: false\n",
        encoding="utf-8",
    )

    from hermes_cli.plugins import PluginManager
    from tools.registry import registry
    from tools.web_tools import _ensure_web_plugins_loaded

    manager = PluginManager()
    manager.discover_and_load()
    loaded = manager._plugins[PLUGIN]
    assert loaded.enabled and loaded.error is None, loaded.error
    _ensure_web_plugins_loaded()

    from agent.web_search_registry import get_provider

    provider = get_provider("antigravity")
    assert provider is not None and provider.is_available()
    module = sys.modules[type(provider).__module__]
    calls: list[dict] = []

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, limit=None):
            raw = json.dumps(PAYLOAD).encode("utf-8")
            return raw if limit is None else raw[:limit]

    def fake_open(request, timeout):
        calls.append(json.loads(request.data))
        return Response()

    original = module._open_request
    module._open_request = fake_open
    try:
        raw = registry.dispatch("web_search", {"query": "e2e probe query", "limit": 3})
        result = json.loads(raw)
        assert result["success"] is True, result
        row = result["data"]["web"][0]
        assert set(row) == {"title", "url", "description", "position"}, row
        assert row["url"] == "https://example.com/a"
        assert row["description"] == "Grounded fact"
        assert len(calls) == 1, calls
        assert calls[0]["tools"] == [{"type": "web_search_20250305", "name": "web_search"}]
        assert calls[0]["messages"][0]["content"] == "e2e probe query"
    finally:
        module._open_request = original
        manager.unload(PLUGIN)


def _run_child() -> None:
    previous = {key: os.environ.get(key) for key in ENV_KEYS}
    try:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bundled = root / "bundled"
            bundled.mkdir()
            os.environ.update(
                {
                    "HERMES_HOME": str(root / "hermes"),
                    "HERMES_BUNDLED_PLUGINS": str(bundled),
                    "ANTIGRAVITY_SEARCH_BASE_URL": "https://gateway.example",
                    "ANTIGRAVITY_SEARCH_API_KEY": "fixture",
                }
            )
            (root / "hermes").mkdir()
            _run_probe(root / "hermes")
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def main() -> None:
    """Run the probe in a disposable process so Hermes global state cannot leak."""
    child_env = os.environ.copy()
    for key in ENV_KEYS:
        child_env.pop(key, None)
    child_env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys([str(REPO), *sys.path]))
    completed = subprocess.run(
        [sys.executable, str(Path(__file__)), "--child"],
        cwd=REPO,
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        details = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
        raise RuntimeError(f"Hermes E2E child failed ({completed.returncode}):\n{details}")


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--child":
        _run_child()
    else:
        main()
