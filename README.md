# LLM Gateway Using LiteLLM

An LLM Gateway is a centralized proxy layer between an application and multiple LLM providers that provides a unified API while managing model routing, authentication, retries and failover, usage monitoring, and cost optimization—without requiring changes to the application's core business logic.

## System Architecture

```mermaid
flowchart TD
    A["Client Applications<br/>Web Apps / AI Agents"] --> B["NexusAI API Gateway<br/>FastAPI"]

    B --> C["Authentication & Validation<br/>API Keys / Request Validation"]

    C --> D["Request Processing<br/>Model Selection / Routing / Rate Limits"]

    D --> E["LiteLLM Integration Layer<br/>Unified LLM Interface"]

    E --> F["LLM Providers"]
    F --> F1["OpenAI"]
    F --> F2["Google Gemini"]
    F --> F3["Anthropic"]
    F --> F4["Groq"]

    F1 --> G["Response Processing"]
    F2 --> G
    F3 --> G
    F4 --> G

    G --> H["Usage & Cost Tracking<br/>Tokens / Latency / Estimated Cost"]

    H --> I[("SQLite / PostgreSQL")]

    G --> J["Response to Client"]

    K["Observability Layer<br/>Metrics / Logging / Tracing"] -.-> B
    K -.-> D
    K -.-> E
    K -.-> G

    I --> L["NexusAI Analytics Dashboard<br/>Usage / Cost / Performance / Errors"]
    K --> L
```

## Project Structure

```text
llm-gateway/
├── app/
│   ├── routes/                  # FastAPI endpoint handlers
│   │   ├── __init__.py
│   │   ├── chat.py              # Chat completions
│   │   ├── embeddings.py        # Embeddings
│   │   ├── health.py            # Health check
│   │   ├── keys.py              # Admin API-key management
│   │   ├── models.py            # Available models
│   │   ├── metrics.py           # Optional Prometheus exposition endpoint
│   │   └── stats.py             # Admin analytics API
│   ├── static/                  # Dashboard files served by FastAPI
│   │   ├── dashboard.css        # Dashboard styles
│   │   ├── dashboard.js         # Dashboard behavior and API requests
│   │   └── index.html           # Dashboard page
│   ├── __init__.py
│   ├── auth.py                  # Gateway-key and admin authentication
│   ├── budget.py                # Monthly budget enforcement
│   ├── config.py                # Environment-based settings
│   ├── db.py                    # Async database engine and initialization
│   ├── engine.py                # LiteLLM provider integration
│   ├── main.py                  # FastAPI app and /dashboard route
│   ├── models.py                # SQLAlchemy database models
│   ├── observability/
│   │   ├── __init__.py          # Backward-compatible observability exports
│   │   ├── metrics.py           # Prometheus metrics and bounded label helpers
│   │   ├── logging.py           # Structured logging and secret redaction
│   │   ├── request_context.py   # Request ID and request completion context
│   │   └── middleware.py        # Request ID propagation middleware
│   ├── ratelimit.py             # Per-key rate limiting
│   ├── routing.py               # Retries, fallbacks, and request logging
│   └── schemas.py               # API request and response schemas
├── tests/
│   ├── __init__.py
│   ├── conftest.py              # Shared test fixtures and test database
│   ├── test_budget.py           # Budget tests
│   ├── test_config.py           # Configuration tests
│   ├── test_embeddings.py       # Embeddings tests
│   ├── test_engine.py           # Provider engine tests
│   ├── test_fallback_chains.py  # Fallback tests
│   ├── test_gateway.py          # API and analytics tests
│   ├── test_ratelimit.py        # Rate-limit tests
│   ├── test_tools_vision.py     # Tools and vision tests
│   └── test_z_observability.py  # Metrics, logging, and request-ID tests
├── secrets/                     # Docker-secret source files (not committed)
│   └── .gitignore
├── .env                         # Created locally; ignored by Git
├── .env.example                 # Example environment configuration
├── .gitignore                   # Files excluded from version control
├── CLAUDE.md                    # AI coding assistant project notes
├── Dockerfile                   # Container image definition
├── docker-compose.yml           # Container deployment settings
├── LICENSE                      # Project license
├── pyproject.toml               # Python project and dependency configuration
├── README.md                    # Project documentation
└── uv.lock                      # Locked Python dependency versions
```

The `.env` file and SQLite database (`gateway.db`) are local runtime files.
Create `.env` from `.env.example`; both `.env` and `gateway.db` are ignored by
Git. The `secrets/` directory is only needed when using Docker secrets, and its
real secret files are excluded from Git.

## Why Do We Need an LLM Gateway?

Direct LLM API calls may work for prototypes, but production applications require reliability, scalability, cost control, and observability etc .

## An LLM Gateway addresses these challenges through a centralized layer.

| Production Challenge | Problem Without a Gateway | How an LLM Gateway Helps |
|---|---|---|
| **Rate Limits (429)** | Requests fail when provider rate limits are exceeded. | Handles retries and configured fallback models. |
| **Provider Outages** | Applications become unavailable when a provider goes down. | Routes requests to alternative providers when configured. |
| **High Latency** | Slow responses can affect application performance. | Tracks response times and enforces request timeouts. |
| **Repeated Requests** | Identical requests can result in unnecessary API costs. | Supports caching to reduce redundant API calls. |
| **Provider Switching** | Changing providers requires modifying application code. | Provides a unified API across multiple LLM providers. |
| **Limited Visibility** | Difficult to monitor requests, failures, and usage. | Tracks requests, errors, token usage, and estimated costs. |
| **Cost Management** | Difficult to identify expensive models and application features. | Provides model-wise and feature-wise cost analytics. |




  ## Features

  - **One OpenAI-compatible API** — `POST /v1/chat/completions`, `POST /v1/embeddings`, `GET /v1/models`. Existing OpenAI clients work unchanged.
  - **Multi-provider** — OpenAI (`gpt-4o`…), Anthropic (`claude-…`), Gemini (`gemini-…`), and Groq (`groq/model-name`).
  - **Tools & vision** — function/tool calling and multimodal image inputs pass through to any provider that supports them.
  - **Usage tracking** — every call logs provider, model, tokens, **USD cost**, latency, and status.
  - **Dashboard** — `/dashboard`: request, token, cost, latency, and success summaries; cost and token trends; provider/model usage; and recent requests.
  - **Streaming (SSE)** — full token streaming with usage captured for cost.
  - **Gateway API keys** — issue/revoke client keys (stored as SHA-256 hashes); provider keys stay server-side.
  - **Per-key budgets** — optional monthly USD spend cap per key (UTC calendar month); over budget returns 402.
  - **Retries & fallback** — transient errors retried; persistent failures fall back to the next model.
  - **Observability** — opt-in Prometheus metrics, bounded model labels, structured logs, and request-ID propagation.
  - **Swappable storage** — SQLite by default, Postgres via one env var (SQLAlchemy async).

## Quickstart

### 1. Install prerequisites

Install **Python 3.10 or later** and
[`uv`](https://docs.astral.sh/uv/getting-started/installation/). Clone the
repository and open its folder in VS Code. Open **Terminal → New Terminal** and
use PowerShell for the commands below.

### 2. Configure your keys

Run these commands from the repository root:

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Copy the generated value into `.env`, replacing `change-me` on the
`ADMIN_API_KEY` line. Add at least one provider key for a provider you use:
`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, or
`DOUBLEWORD_API_KEY`. Provider credentials are optional individually, but a
provider key is needed to make successful upstream model calls. Keep `.env`
private; never commit or share it.

### 3. Install dependencies

```powershell
uv sync --all-extras --all-groups
```

This installs the gateway, optional PostgreSQL driver, and development tools.
SQLite is the default, so no database server is needed for local development.

### 4. Start the gateway

```powershell
uv run uvicorn app.main:app --reload
```

Keep this terminal open while using the gateway. The database file
`gateway.db` is created automatically in the project folder.

The dashboard is served by FastAPI at
<http://127.0.0.1:8000/dashboard>; there is no separate frontend server or npm
install/build step. Enter the `ADMIN_API_KEY` from `.env` when prompted. The
interactive API documentation is at <http://127.0.0.1:8000/docs>, and the
health check is at <http://127.0.0.1:8000/health>. If you change `.env`, stop
the server with **Ctrl+C** and restart it so the updated settings are loaded.

### 5. Create a client key and send a test request

Open a **second** PowerShell terminal in the project folder. Enter the admin
key when prompted, then create a gateway key:

```powershell
$adminKey = Read-Host "Enter the ADMIN_API_KEY from .env"
$headers = @{ Authorization = "Bearer $adminKey" }
$clientKey = (Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/admin/keys" `
  -Method Post `
  -Headers $headers `
  -ContentType "application/json" `
  -Body '{"name":"my-app"}').api_key
```

The new client key is returned once and stored in `$clientKey` for this
terminal session. Use a configured provider and a model it supports. For
example, `gpt-4o-mini` requires `OPENAI_API_KEY`:

```powershell
$clientHeaders = @{ Authorization = "Bearer $clientKey" }
$body = @{
  model = "gpt-4o-mini"
  messages = @(@{ role = "user"; content = "Hello!" })
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/v1/chat/completions" `
  -Method Post `
  -Headers $clientHeaders `
  -ContentType "application/json" `
  -Body $body
```

If you configured a different provider, choose one of its models instead.
Requests made with a valid client key appear in the dashboard after they are
logged by the gateway.

### Switch to PostgreSQL (optional)

The default SQLite setup is enough to run the project. To use PostgreSQL,
install the optional driver (already included in the all-extras command above)
and set `DATABASE_URL` in `.env`, for example:

```dotenv
DATABASE_URL=postgresql+asyncpg://gateway:your-password@localhost:5432/gateway
```

Start PostgreSQL separately, then restart the gateway. The Docker Compose
PostgreSQL profile is also documented below.

### Prometheus metrics (optional)

`GET /metrics` is disabled unless `METRICS_ENABLED=true`. When enabled, set
`METRICS_TOKEN` to require `Authorization: Bearer <token>` on scrapes. The
endpoint is excluded from OpenAPI. Metrics cover gateway request outcomes,
latency, streaming TTFT, tokens, estimated cost, retries, fallbacks, rate-limit
and budget rejections, and in-flight requests. Up to 49 normalized model names
from aliases, fallback chains, recent logs, or active requests receive distinct
labels; unrecognized names are grouped as `other`, keeping the total at 50
model-label values. Deploy one Uvicorn worker per container because metrics and
rate-limit state are process-local.

### Request IDs and logs

Every HTTP response includes `X-Request-ID`. The gateway accepts a safe
incoming ID or generates a UUID4, then includes it in one `request_completed`
structured log for each chat or embedding request. `LOG_LEVEL` controls the
standard-library logging level; `LOG_FORMAT` is `json` by default, or `text`
for local development. Logs include request outcome, provider/model, latency,
token counts, estimated cost, attempts, and gateway key ID, but never prompts,
completions, authorization headers, or configured credentials. Upstream error
details are logged for diagnosis; clients receive a generic error and the
request ID for correlation.

### Run the tests (optional)

In PowerShell:

```powershell
$env:PYTHONPATH = ""
uv run pytest
```

## Model naming

| You send | Routed to |
|---|---|
| `gpt-4o`, `o4-mini`, … | OpenAI |
| `claude-3-5-sonnet-20241022`, … | Anthropic (`anthropic/…`) |
| `gemini-1.5-flash`, … | Gemini (`gemini/…`) |
| `groq/llama-3.3-70b-versatile`, … | Groq |
| `anthropic/…`, `vertex_ai/…`, any explicit `provider/model` | passed through to LiteLLM |
| a configured alias (see `MODEL_ALIASES`) | its mapped target |

Per-request fallback: add `"fallback_models": ["gemini-1.5-flash"]` to a chat request;
on failure the gateway retries, then tries each fallback in order.

## Tools & vision

The gateway is a transparent passthrough for OpenAI-style **tool/function calling**
and **multimodal (image) inputs** — point a model that supports them at the
endpoint and they just work:

- Send `tools` / `tool_choice` (and optionally `parallel_tool_calls`); the
  assistant's `tool_calls` come back unchanged. On the next turn, send the
  assistant message with its `tool_calls` plus a `tool`-role message carrying the
  matching `tool_call_id` — both round-trip intact.
- Send `content` as a list of parts (`{"type":"text",...}`,
  `{"type":"image_url",...}`) for vision.

```python
client.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "What's in this image?"},
        {"type": "image_url", "image_url": {"url": "https://…/cat.png"}},
    ]}],
)
```

Only an allowlist of OpenAI params is forwarded to the provider; client-supplied
credential/endpoint overrides (`api_base`, `api_key`, …) are always dropped.

## Rate limiting

Per-key, sliding-window over the last 60 seconds, enforced on
`/v1/chat/completions`. The limit is a key's own `rate_limit_per_min` (set at
creation) if present, otherwise the global `RATE_LIMIT_PER_MINUTE`; `0` (from
either) means unlimited. Over the limit returns **429** with a `Retry-After`
header.

```bash
# A key capped at 60 requests/minute, regardless of the global default:
curl -s -X POST localhost:8000/admin/keys \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"my-app","rate_limit_per_min":60}'
```

State is in-memory per process, so multi-worker deployments enforce roughly
`limit × workers`; back it with Redis for a strict global limit (upgrade path,
not built).

## Budgets

Per-key **monthly spend cap** in USD, enforced on `/v1/chat/completions`. The cap
is a key's own `monthly_budget_usd` (set at creation) if present, otherwise the
global `MONTHLY_BUDGET_USD`; `0` (from either) means unlimited. Month-to-date
spend is the sum of the key's logged `cost_usd` since the start of the current
**UTC calendar month**; once it reaches the cap the request is rejected with
**402 Payment Required**.

```bash
# A key capped at $25/month, regardless of the global default:
curl -s -X POST localhost:8000/admin/keys \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"name":"my-app","monthly_budget_usd":25}'
```

The cap is checked *before* the upstream call (the cost of the crossing request
isn't known yet), so a key can overshoot by at most one request; for a strict
cap, back spend tracking with Redis (same upgrade path as rate limiting).

Limits can be changed later without re-issuing the key — `PATCH /admin/keys/{id}`
updates only the fields you send (`name`, `monthly_budget_usd`, `rate_limit_per_min`,
`active`); send an explicit `null` to clear a limit back to the global default:

```bash
# Raise this key's monthly cap to $100; leave its rate limit untouched:
curl -s -X PATCH localhost:8000/admin/keys/3 \
  -H "Authorization: Bearer $ADMIN_API_KEY" -H "Content-Type: application/json" \
  -d '{"monthly_budget_usd":100}'
```

## Configuration

All via env / `.env` (see `.env.example`):

| Var | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./gateway.db` | SQLite or `postgresql+asyncpg://…` |
| `ADMIN_API_KEY` | — | Guards key management + dashboard |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` / `GROQ_API_KEY` / `DOUBLEWORD_API_KEY` | — | Optional provider credentials |
| `REQUEST_TIMEOUT_SECONDS` | `120` | Upstream timeout |
| `MAX_RETRIES` | `2` | Retries per model on transient errors |
| `RATE_LIMIT_PER_MINUTE` | `0` | Default per-key req/min on `/v1/chat/completions` (0 = off) |
| `MONTHLY_BUDGET_USD` | `0` | Default per-key monthly USD spend cap (0 = unlimited) |
| `METRICS_ENABLED` | `false` | Enable the protected Prometheus `/metrics` endpoint |
| `METRICS_TOKEN` | — | Optional bearer token for Prometheus scrapes |
| `LOG_LEVEL` | `INFO` | Standard-library logging threshold |
| `LOG_FORMAT` | `json` | Log output format: `json` or `text` |
| `CORS_ORIGINS` | `*` | Comma-separated allowlist (lock down in prod) |
| `MODEL_ALIASES` | `{}` | JSON map of friendly name → litellm model |
| `FALLBACK_CHAINS` | `{}` | JSON map of model (or `provider/*`) → default fallback model(s), used when a request has no `fallback_models` |

## API

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/chat/completions` | gateway key | Chat completion (stream or not) |
| `POST` | `/v1/embeddings` | gateway key | Embeddings (retries + fallback + cost) |
| `GET` | `/v1/models` | gateway key | Models for configured providers |
| `POST` | `/admin/keys` | admin | Create a gateway key (returned once) |
| `GET` | `/admin/keys` | admin | List keys (prefixes only) |
| `PATCH` | `/admin/keys/{id}` | admin | Update name / budget / rate-limit / active (partial) |
| `DELETE` | `/admin/keys/{id}` | admin | Revoke a key |
| `GET` | `/api/stats?days=N` | admin | Usage aggregates (dashboard data) |
| `GET` | `/metrics` | optional bearer token | Prometheus metrics (disabled by default; omitted from OpenAPI) |
| `GET` | `/health` | none | Liveness + configured providers |

## Docker

The Compose configuration mounts the Doubleword key as a Docker secret by
default. Create `secrets\doubleword.key` with that provider key before starting
the container. The file is gitignored. In PowerShell:

```powershell
Copy-Item .env.example .env
New-Item -ItemType Directory -Force secrets
Set-Content -NoNewline secrets\doubleword.key "your-doubleword-api-key"
docker compose up --build
```

For PostgreSQL, set `DATABASE_URL` in `.env` to
`postgresql+asyncpg://gateway:gateway@db:5432/gateway`, then run
`docker compose --profile pg up --build`.

## Request Flow

```mermaid
flowchart TD
    A["Client Application<br/>OpenAI-Compatible Request"]
    B["FastAPI Gateway<br/>/v1/chat/completions"]
    C["Authentication<br/>Gateway API Key"]
    D["routing.py<br/>Model Selection & Fallback"]
    E["engine.py<br/>LiteLLM Integration"]
    F["LLM Providers<br/>OpenAI / Anthropic / Gemini"]
    G["Response Processing<br/>Usage / Tokens / Latency / Cost"]
    H[("RequestLog<br/>SQLite / PostgreSQL")]
    I["/api/stats"]
    J["/dashboard"]
    K["Client Response"]

    A --> B --> C --> D --> E --> F
    F --> G
    G --> K
    G --> H
    H --> I --> J
```

```

## Development

```bash
uv sync
uv run pytest          # litellm calls are stubbed (no network)
uv run ruff check app tests
```

## Roadmap (post-v1)

OpenTelemetry tracing · React dashboard · Redis-backed rate-limit & budget
counters for strict multi-worker limits.

## License

MIT — see [LICENSE](LICENSE).
