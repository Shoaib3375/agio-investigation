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
| PY-M00 | done | Repository & contract verification (read-only audit) |
| PY-M01 | done | Python service foundation (FastAPI + SQLAlchemy + Alembic + Docker) |
| PY-M02 | pending | RabbitMQ event consumer + durable event inbox |
| PY-M03 | pending | Laravel internal API client |
| PY-M04 | pending | Investigation database + evidence collection |
| PY-M05 | pending | Deterministic investigation engine |
| PY-M06 | pending | Investigation API (admin-facing) |
| PY-M07 | pending | LLM explanation layer |
| PY-M08 | pending | RAG knowledge base |
| PY-M09 | pending | Production hardening |

## Documentation

- `docs/AGIO_PYTHON_IMPLEMENTATION_PLAN.md` — full architecture & implementation blueprint
- `docs/AGIO_INVESTIGATION_READINESS_AUDIT.md` — PY-M00 verification of Laravel integration points
- `docs/AGIO_INVESTIGATION_DATA_CONTRACT.md` — the data contract (source of truth for fields/events)

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
