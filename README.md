# AGIO Intelligent Transaction Investigation

Independent Python microservice that investigates suspicious, failed, delayed, mismatched, or unusual transactions in the [AGIO PaymentExchange](https://github.com/Shoaib3375/PaymentExchange) Laravel platform.

## What this service does

- Consumes business events from RabbitMQ (published by Laravel's transactional outbox).
- Retrieves **read-only** evidence from Laravel's internal investigation API.
- Reconstructs transaction timelines from authoritative `order_events`.
- Runs **deterministic rules** to detect financial, blockchain, provider, ledger, and account inconsistencies.
- (Later) Uses RAG to retrieve approved policies and historical cases.
- (Later) Uses an LLM to **explain** findings — never to assert financial facts.
- Produces structured investigation reports for human admin review.

## What this service never does

It never approves/rejects withdrawals, executes/refunds exchanges, modifies balances, changes ledger entries, alters blockchain/KYC/AML status, freezes accounts, or makes final compliance decisions. **Laravel remains the source of truth for all financial operations.**

## Architecture

```
Laravel PaymentExchange  →  Transactional Outbox  →  RabbitMQ (agio.events)
                                                         |
                                                         v
                                              Python Investigation Service
                                                  (this repo)
                                                         |
                          Consumer · Evidence Collector · Rule Engine
                                                         |
                                              Investigation Database
                                              (agio_investigation, separate)
                                                         |
                                              Admin-facing Investigation API
```

## Milestones

| Milestone | Status | Description |
|-----------|--------|-------------|
| PY-M00 | ✅ done | Repository & contract verification (read-only audit) |
| PY-M01 | ✅ done | Python service foundation (FastAPI + SQLAlchemy + Alembic + Docker) |
| PY-M02 | ✅ done | RabbitMQ event consumer + durable event inbox |
| PY-M03 | ✅ done | Laravel internal API client |
| PY-M04 | ✅ done | Investigation database + evidence collection |
| PY-M05 | ✅ done | Deterministic investigation engine (26 rules across 5 analyzers) |
| PY-M06 | ✅ done | Investigation API (admin-facing, 9 endpoints) |
| PY-M07 | ✅ done | LLM explanation layer (OpenAI, injection-safe, structured output) |
| PY-M08 | 📋 future | RAG knowledge base — optional, not required for operational service |
| PY-M09 | ✅ done | Production hardening (metrics, middleware, retention, Dockerfile) |

> **PY-M08 (RAG) is documented as a future optional milestone.**
> It will not be implemented until the core investigation engine is operational
> and there is a confirmed retrieval use case justified by real investigation
> patterns. Enabling pgvector and an embedding pipeline before that point
> introduces infrastructure cost with no measurable benefit.
> The LLM layer (PY-M07) already explains findings without RAG.

## Production hardening (PY-M09)

### Metrics
Prometheus metrics are exposed at `GET /api/v1/metrics` (no auth required for scraping).

Metric families:
- `agio_investigation_events_total{status}` — received / processed / duplicate / failed / dlq
- `agio_investigation_investigations_total{status}` — lifecycle counts
- `agio_investigation_evidence_records_total{result}` — stored / missing / api_error
- `agio_investigation_findings_total{severity}` — per-severity finding counts
- `agio_investigation_llm_requests_total{result}` — success / disabled / failed / timeout
- `agio_investigation_llm_duration_seconds` — histogram
- `agio_investigation_processing_duration_seconds` — histogram

Scrape config (prometheus.yml):
```yaml
- job_name: agio-investigation
  static_configs:
    - targets: ['investigation-api:8000']
  metrics_path: /api/v1/metrics
```

### Readiness check
`GET /api/v1/ready` checks all dependencies:
- **database** — critical (503 if down)
- **rabbitmq** — non-critical (degrades but still serves)
- **laravel** — non-critical (degrades but still serves)

### Request logging
Every HTTP request is logged with `correlation_id`, `method`, `path`, `status`, `latency_ms`.
The `X-Request-ID` header is echoed in responses (or generated if absent).

### Retention
The retention worker runs every 6 hours and deletes:
- `event_inbox` rows with `status='processed'` older than `RETENTION_EVENT_INBOX_DAYS` (default: 30)
- `investigations` with `status='completed'` older than `RETENTION_INVESTIGATION_DAYS` (default: 90), excluding those with `needs_more_evidence` feedback

Configure via environment variables:
```
RETENTION_EVENT_INBOX_DAYS=30
RETENTION_INVESTIGATION_DAYS=90
RETENTION_INTERVAL_SECONDS=21600
```

### Dockerfile
- Two-stage build: builder + runtime (smaller image)
- Pinned base image: `python:3.12.9-slim`
- Runs as non-root user (`appuser`, UID 1001)
- Built-in `HEALTHCHECK` using the liveness endpoint
- uvloop + httptools for performance

### Admin API
`ADMIN_API_KEY` must be set before exposing admin endpoints. Generate with:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```
Set in `.env` and ensure it is rotated periodically.

### Issue service token
The Laravel internal API token must be issued once:
```bash
docker exec payment-exchange-app php artisan investigation:issue-token
```
Store the printed token in `LARAVEL_SERVICE_TOKEN`. It is never shown again.

### LLM (optional)
Set `LLM_PROVIDER=openai` and `LLM_API_KEY=sk-...` to enable LLM explanations.
Defaults to `disabled` — the service operates fully without an LLM.

## Documentation

- `docs/AGIO_INVESTIGATION_ARCHITECTURE.md` — current architecture (auto-updated)
- `docs/AGIO_PYTHON_IMPLEMENTATION_PLAN.md` — full implementation blueprint
- `docs/AGIO_INVESTIGATION_READINESS_AUDIT.md` — PY-M00 verification findings
- `docs/AGIO_INVESTIGATION_DATA_CONTRACT.md` — verified event + API contract

## Local development

### Prerequisites
- Python 3.12+ (developed against 3.13)
- Docker (for the database and the containerised service)

### Quick start (Docker)
1. Copy the environment template: `cp .env.example .env`
2. Start the service and its own Postgres database:
   `docker compose up -d investigation-database investigation-api`
3. Health check: `curl http://localhost:8001/api/v1/health`

### Native (non-Docker) development
1. Create a venv and install with dev extras:
   `python -m venv .venv`, then activate it, then `pip install -e ".[dev]"`
   (Windows: `.venv\Scripts\activate`; macOS/Linux: `source .venv/bin/activate`)
2. Start the database only: `docker compose up -d investigation-database`
3. Run migrations: `alembic upgrade head`
4. Run the API: `uvicorn app.main:app --reload --port 8001`
5. Run tests: `pytest`

### Laravel network reachability
The `investigation-api` container joins the external Docker network
`paymentexchange_payment-exchange` so it can reach Laravel's `rabbitmq` and
`app` services. That network is created by Laravel's compose stack; if it is
not running, create it first (`docker network create
paymentexchange_payment-exchange`) or start Laravel's `docker compose up -d
rabbitmq app` before bringing up `investigation-api`. If the Laravel stack is
not running the API still starts — it simply cannot reach RabbitMQ or the
Laravel internal API until those services are available. RabbitMQ and the
Laravel service token are optional, so the service boots without them.

### What connects to what
- **RabbitMQ** — same broker as Laravel (vhost `agio`, consumes from queue `agio.investigation`)
- **Laravel internal API** — `http://app:8000/api/internal/investigation/v1` (auth via `service:internal` Sanctum token)
- **Investigation PostgreSQL** — separate database `agio_investigation` (never touches Laravel's DB)

## License

Proprietary — AGIO.
