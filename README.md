# Hermes Antigravity Search

A search-only Hermes Agent backend that answers the native `web_search` tool
with Google Search grounding, reached through an Anthropic Messages-compatible
gateway that maps the Claude `web_search` server tool onto Gemini's
`googleSearch` grounding.

```text
Hermes web_search -> provider: antigravity -> POST /v1/messages
                     (tools: [web_search_20250305]) -> Google Search grounding
```

It is the Google-index counterpart of
[Hermes Codex Search](https://github.com/duu261/hermes-codex-search). Use it when
you want fresher results, regional or non-English coverage, or an alternative
index. It does not provide page extraction, browsing, `open`, `click`, or
screenshots.

## How it works

Each `web_search` call sends one Messages request whose only tool is
`web_search_20250305`. A compatible gateway turns that into a Gemini request
with Google Search grounding and returns Claude-shaped `web_search_tool_result`
blocks plus cited text. The plugin returns:

- `data.web[]`: `title`, `url`, `description`, `position` for each grounded
  source. `description` is the grounded sentence that cites that source, or
  empty when no sentence cites it.
- `output`: the model's grounded summary, when present.

Unlike a standalone search API, grounding runs a short model turn. Each search
costs a small number of model tokens and usually takes 3-8 seconds.

A response without a `web_search_tool_result` block is treated as a failure,
not as results, so a model that silently answers from memory is never passed
off as a search.

## Limitations: model-mediated search

This is not a deterministic search API. The search runs inside a model turn:

- The model chooses the actual search query and may rewrite, narrow, or
  strip accents from yours.
- `description` and `output` are model-generated text tied to a source, not
  text extracted from the page. They can be wrong while the URL is real.
- Titles are often bare domains, and the number of results varies per call.

If your agent treats tool output as ground truth, prefer a standalone search
backend and use this one only as a secondary, freshness-oriented source.
Verify claims by reading the returned URLs.

## Requirements

A gateway that implements the Claude `web_search` server tool over Google
Search grounding, and a model on that gateway that supports grounding. Typically
only models the upstream lists as web-search capable are translated into
grounding requests. A model without grounding support produces
an ordinary text answer, which this plugin rejects.

## Install

```bash
hermes plugins install https://github.com/duu261/hermes-antigravity-search --enable
```

Set the endpoint and credential in Hermes' private environment file:

```text
ANTIGRAVITY_SEARCH_BASE_URL=https://gateway.example
ANTIGRAVITY_SEARCH_API_KEY=replace-me
# Optional, default gemini-3.1-flash-lite:
ANTIGRAVITY_SEARCH_MODEL=gemini-3.1-flash-lite
```

The base URL may be the gateway root, `/v1`, or the full `/v1/messages` URL.
Never commit credentials.

Remote endpoints must use HTTPS. Plain HTTP is accepted only for `localhost`,
`127.0.0.1`, or `::1`. The adapter rejects embedded credentials, refuses
redirects, caps response bodies at 4 MiB, drops result rows without a valid
HTTP(S) URL, and returns only an HTTP status or a fixed error category on
failure.

## Select it for search

```yaml
web:
  search_backend: antigravity
  extract_backend: firecrawl
```

Restart the Hermes surface that should use it. A running gateway process does
not reload plugins.

## Trust and responsible use

- Queries are sent to the gateway you configure, and from there to Google.
  The gateway operator can log, retain, and bill for them.
- This project is not affiliated with Google, Anthropic, or any gateway
  project. Using third-party OAuth credentials against Google services may
  violate their terms and can lead to account restrictions. You are
  responsible for having authorization for the credentials and gateway you use.
- Grounded summaries can still be wrong. Cite the returned URLs, not the
  summary.

## Development

See [AGENTS.md](AGENTS.md).

## License

MIT
