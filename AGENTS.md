# Hermes Antigravity Search - Agent Instructions

## Scope

Standalone Hermes backend plugin. It registers web-search provider
`antigravity` for the native `web_search` tool. It sends one Anthropic Messages
request per search with the Claude `web_search_20250305` server tool as the
only tool, and normalizes the gateway's Google-grounded response.

Search only. Do not add extraction, browsing, continuation state, a new model
tool, or a generic Messages client without an explicit product decision.

## Repository map

- `provider.py` - validation, payload, HTTP transport, normalization
- `__init__.py` - `register_web_search_provider`
- `plugin.yaml` - manifest; version must equal `provider.VERSION`
- `tests/test_provider.py` - deterministic unit and transport tests
- `tests/test_hermes_e2e.py`, `tests/hermes_e2e_runner.py` - real Hermes
  plugin load plus native `web_search` dispatch, in a child process
- `tests/test_live.py` - opt-in authenticated gateway test

## Runtime path

```text
AIAgent -> tool executor -> registry.dispatch("web_search")
  -> tools.web_tools.web_search_tool -> web.search_backend = antigravity
  -> AntigravityWebSearchProvider.search(query, limit)
```

## Contracts

- `web_search` must be the only tool, with no `system` prompt and no
  `tool_choice`. Gateways build a grounding request only for that shape.
- Return the native shape: `data.web[]` rows with exactly `title`, `url`,
  `description`, `position`; optional top-level `output`.
- No `web_search_tool_result` block means no search happened. Fail; never
  return an ungrounded answer as results.
- Treat HTTP 200 error envelopes and empty results with no answer as failures.
- Never return credentials, headers, request bodies, endpoint URLs, raw
  upstream bodies, or traces. HTTP errors return the status code only.
- HTTPS only except loopback. Refuse redirects. Cap response size.
- Keep the User-Agent version equal to the manifest version.

## Verification

```bash
hermes --run-module unittest discover -s tests -v   # includes real Hermes E2E
python -m unittest discover -s tests -v             # E2E skips without Hermes
ruff format --check . && ruff check .
hermes plugins doctor . --ci
git diff --check
```

Live testing consumes gateway quota and runs only when enabled:

```bash
ANTIGRAVITY_SEARCH_LIVE_TESTS=1 \
ANTIGRAVITY_SEARCH_BASE_URL=https://gateway.example \
ANTIGRAVITY_SEARCH_API_KEY=... \
python -m unittest tests.test_live -v
```

Pass the key from an already-exported variable. Never print secrets, prompts,
or raw responses. One transient retry (408/429/5xx/network) is allowed.

## Installation verification

Clean-install the exact pushed SHA with
`hermes plugins install <url> --ref <sha> --enable` in disposable `HOME` and
`HERMES_HOME` under a scratch directory, with default scanning on. Read back
the installed HEAD and enabled state, and run Plugin Doctor on it.

## Operations

Installing into a live profile, changing `web.search_backend`, and restarting
the Hermes gateway are operator actions.

Conventional Commits, imperative subject of 50 characters or fewer. Keep the
repository publish-safe: no private hosts, account names, or keys.
