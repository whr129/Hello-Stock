# Stock/news harness

The Telegram graph, CLI, and daily report use one typed capability registry and executor.
`research.report` returns the same structured daily candidates, evidence and alert context
for a fixed `RunContext.as_of`; profiles control permissions and memory. `/research` keeps
its existing ordered extraction/scoring transaction before report retrieval.

Set `HARNESS_CONFIG_FILE=config/harness.example.toml` to enable the example. The file is
TOML with `schema_version = 1`. Defaults retain the existing tools. Unknown keys, unknown
capabilities, invalid model references and delegation cycles fail validation. The optional
`settings` table and each profile's `settings` table accept existing Settings field names
or environment aliases, including source/evidence/report policy settings.

Precedence is application defaults, shared TOML settings, profile settings, then explicit
Settings values and environment/.env values. Process environment overrides `.env`. Harness
execution limits also support `HARNESS_MAX_PARALLEL_CALLS`, `HARNESS_MAX_CALLS_PER_RUN`,
`HARNESS_MAX_AGENT_DEPTH`, `HARNESS_MAX_TURNS`, `HARNESS_MAX_TOKENS`, and
`HARNESS_DEADLINE_SECONDS`. Their values use JSON number syntax. Profile limits can narrow
root budgets. Secrets stay in `.env` or process environment; TOML only names secret variables.
The harness snapshots that environment privately without modifying process-global variables.

## CLI and Python

```bash
news-agent-harness inspect
news-agent-harness research.report --text
news-agent-harness chat --arguments '{"message_text":"Research NVDA"}' --text
news-agent-harness command.research --arguments '{"command":"/signals","args":["NVDA"]}' --text
```

The equivalent module entry point is `python -m news_agent.harness.cli`. CLI identities are
ephemeral and memory is disabled by default. An explicit `--principal` selects an enrolled
OAuth identity without enabling conversation memory. Scheduled runs likewise need no
Telegram user/chat ID. `inspect` returns the config revision, validated configuration,
allowed capability IDs and MCP readiness; resolved credentials and conversation state are
never included.

Python extensions register `CapabilitySpec(id=..., input_model=..., handler=...)` through
`StockNewsHarness(..., registrations=[spec])`. A handler receives a Pydantic argument model
and trusted `RunContext`; register effects (`read`, `cache`, `write`, `delivery`) explicitly.
Configured agents use `agent.NAME` capability IDs, a model registration, prompt, tools and
optional `delegates`. Agent names `main` and `research` configure the built-in model loops.
Other names create bounded delegates. The caller's allowlist is always the ceiling.

Independent reads run concurrently. Writes form ordered barriers. Shared call, turn,
token and deadline budgets include nested agents; model output limits reserve budget before
parallel requests. Provider usage settles those reservations; missing usage conservatively
consumes the reserved amount. Unknown write outcomes are never automatically retried.
Capabilities persist sanitized status, duration, revision and parent-call metadata through
runtime tracing. A trace failure does not replay domain work.

## Remote MCP and OAuth

Remote tools use the official pinned MCP SDK over Streamable HTTP. Configure an exact
`allowed_tools` list and trusted `tool_policy` for every tool. Tool names become namespaced
IDs such as `mcp.market.quote`; the registry generates provider-safe aliases. Add the ID to
the profile's `allowed_capabilities`, model-visible `exposed_tools`, and relevant agent tool
list. Discovery runs once per harness run, outside model iterations; connections close at
run completion or cancellation. Required server failures fail readiness; optional failures
appear in inspection. `max_response_bytes` bounds bytes before SDK parsing, and compressed
responses are refused. Schema/result limits apply separately. Unsupported schema constructs
are disabled with diagnostics rather than fetched or executed.

For OAuth, set the MCP auth table to:

```toml
mode = "oauth"
encryption_key_env = "HARNESS_OAUTH_ENCRYPTION_KEY"
storage_directory = ".state/mcp-oauth"
scope = "read"
redirect_uri = "http://127.0.0.1:8766/callback"
```

Generate a Fernet key with `cryptography.fernet.Fernet.generate_key()`, save its decoded
value in the named environment variable, and keep it backed up securely. Enroll explicitly:

```bash
news-agent-harness oauth-enroll --server market --principal service:daily-report
news-agent-harness oauth-revoke --server market --principal service:daily-report
```

Enrollment opens the authorization URL in the browser and asks for the final redirect URL
using hidden input. The official SDK handles discovery, consent state, code exchange,
resource binding and refresh. Headless runs never open a login browser. Tokens, client
credentials and discovered issuer metadata are encrypted, stored with mode 0600, and keyed
by principal, exact server endpoint and requested scope. Storage is gitignored. Local
revocation removes that enrollment; revoke provider-side consent through its account
settings as well. Already issued provider tokens may remain valid until revoked or expired.

Local HTTP/SDK fixtures verify pagination, mixed local/remote calls, malformed schemas,
401 responses, token refresh, issuer persistence, principal/scope isolation, response bounds,
cancellation and shutdown. Real provider consent and live authorization remain deployment
checks requiring that provider's credentials and supported OAuth registration flow.
