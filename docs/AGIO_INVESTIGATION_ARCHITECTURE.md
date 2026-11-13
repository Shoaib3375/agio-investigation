# AGIO Investigation Service — Architecture

**Repo:** `agio-investigation`
**Language:** Python 3.12 / 3.13
**Framework:** FastAPI + SQLAlchemy 2 + aio-pika + HTTPX
**Database:** PostgreSQL (`agio_investigation`) — separate from Laravel
**Milestone progress:** PY-M00 ✅ · PY-M01 ✅ · PY-M02 ✅ · PY-M03 ✅

---

## Directory Tree

```
agio-investigation/
│
├── app/                            # Application source
│   ├── main.py                     # FastAPI app factory + lifespan (DB ping, RabbitMQ start)
│   ├── __init__.py
│   │
│   ├── api/                        # HTTP layer
│   │   └── v1/
│   │       ├── health.py           # GET /api/v1/health  (liveness)
│   │       │                       # GET /api/v1/ready   (readiness — DB ping)
│   │       └── router.py           # Aggregates v1 routes
│   │
│   ├── core/                       # Cross-cutting concerns
│   │   ├── config.py               # Pydantic Settings (env / .env file)
│   │   └── logging.py              # Structlog configuration
│   │
│   ├── database/                   # Persistence layer
│   │   ├── base.py                 # SQLAlchemy DeclarativeBase
│   │   ├── session.py              # Async engine, session factory, ping_database()
│   │   └── repositories/
│   │       └── event_inbox_repository.py   # insert_if_not_exists, mark_*/get_pending
│   │
│   ├── models/                     # SQLAlchemy ORM models
│   │   └── event_inbox.py          # EventInbox — durable event store (UNIQUE event_id)
│   │                               # (investigations, evidence, findings, reports: PY-M04)
│   │
│   ├── schemas/                    # Inbound message validation
│   │   └── events.py               # EventEnvelope, Entity, WIRED_EVENT_TYPES (10 types)
│   │
│   ├── messaging/                  # RabbitMQ integration
│   │   ├── consumer.py             # InvestigationConsumer — robust connection, topology,
│   │   │                           #   passive-first declare, graceful shutdown
│   │   ├── handlers.py             # handle_message — parse → validate → DB insert → ACK
│   │   │                           #   (non-ACK on DB failure, NACK→DLQ on bad payload)
│   │   └── retry.py                # republish_to_retry — x-retry-count header tracking,
│   │                               #   bounded retries → NACK to DLQ
│   │
│   └── integrations/               # External service clients
│       └── laravel/
│           ├── client.py           # LaravelClient — HTTPX async, Bearer auth, retry,
│           │                       #   5 typed errors: NotFound/Auth/Server/Timeout/Unavailable
│           ├── schemas.py          # 20+ Pydantic models — one per API response shape
│           │                       #   all monetary fields: Decimal (never float)
│           │
│           ├── users.py            # /users/{id}/profile · activity · risk-summary
│           │                       #   orders · wallets · ledger · compliance
│           ├── orders.py           # /orders/{uuid} · /orders/{uuid}/timeline
│           ├── blockchain.py       # /orders/{uuid}/blockchain · /blockchain/deposits/{id}
│           │                       #   /deposits/{id} · /withdrawals/{id}
│           ├── providers.py        # /orders/{uuid}/provider · /providers/{id}
│           │                       #   /providers/{id}/executions
│           ├── ledger.py           # /orders/{uuid}/ledger · /users/{id}/ledger
│           │                       #   /ledger/journal/{uuid}
│           ├── reconciliation.py   # /orders/{uuid}/reconciliation
│           ├── risk.py             # /risk-cases/{uuid}
│           └── evidence_client.py  # EvidenceClient façade — composes all 8 modules,
│                                   #   returns None on 404, logs fetch timing
│
├── migrations/                     # Alembic schema migrations
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       ├── 0001_initial.py         # Placeholder (proves Alembic toolchain works)
│       └── 0002_event_inbox.py     # event_inbox table + 4 indexes
│
├── tests/
│   ├── conftest.py                 # Shared fixtures (FastAPI TestClient, DB session)
│   ├── test_health.py              # Liveness + readiness endpoint tests
│   ├── test_consumer.py            # EventEnvelope schema + DB idempotency tests
│   ├── test_laravel_schemas.py     # Contract tests — pure schema validation, no network
│   │                               #   (Decimal arithmetic, optional fields, required fields)
│   └── test_laravel_client.py      # Live integration tests — requires Laravel on :8000
│                                   #   (auth, all user endpoints, 404 handling, UUID guards)
│
├── docs/
│   ├── AGIO_INVESTIGATION_ARCHITECTURE.md      # This file
│   ├── AGIO_INVESTIGATION_DATA_CONTRACT.md     # Verified event + API contract
│   ├── AGIO_INVESTIGATION_READINESS_AUDIT.md   # PY-M00 audit findings
│   └── AGIO_PYTHON_IMPLEMENTATION_PLAN.md      # Full blueprint
│
├── Dockerfile
├── docker-compose.yml              # investigation-database (pg:5437) + investigation-api (:8001)
│                                   # joins external paymentexchange_payment-exchange network
├── alembic.ini
├── pyproject.toml                  # Pinned deps, pytest config, ruff config
├── .env / .env.example
└── README.md
```

---

## Runtime Architecture

```
Laravel PaymentExchange (:8000)
    │
    │  Transactional Outbox
    │  (outbox_events table)
    │
    ▼
RabbitMQ  (agio.events exchange)
    │  routing key = event_type
    │  queue: agio.investigation  (#)
    │
    ▼
 ┌────────────────────────────────────────────────────────┐
 │          AGIO Investigation Service (:8001)            │
 │                                                        │
 │  ┌─────────────────────────────────────────────────┐  │
 │  │  InvestigationConsumer  (aio-pika robust)        │  │
 │  │  → passive-first topology declare               │  │
 │  │  → prefetch 16                                  │  │
 │  │  → graceful shutdown                            │  │
 │  └──────────────┬──────────────────────────────────┘  │
 │                 │  IncomingMessage                     │
 │  ┌──────────────▼──────────────────────────────────┐  │
 │  │  handle_message                                  │  │
 │  │  1. JSON parse  (NACK→DLQ on failure)           │  │
 │  │  2. EventEnvelope validate  (NACK→DLQ)          │  │
 │  │  3. Unknown type?  → ACK skip                   │  │
 │  │  4. INSERT event_inbox ON CONFLICT DO NOTHING    │  │
 │  │  5. COMMIT                                      │  │
 │  │  6. ACK  (no-ack on DB failure → redelivery)    │  │
 │  └──────────────┬──────────────────────────────────┘  │
 │                 │                                      │
 │  ┌──────────────▼──────────────────────────────────┐  │
 │  │  event_inbox  (PostgreSQL)                       │  │
 │  │  UNIQUE event_id — idempotent delivery           │  │
 │  │  status: received → processing → processed       │  │
 │  │                               └→ failed          │  │
 │  └──────────────┬──────────────────────────────────┘  │
 │                 │  (PY-M04 picks up from here)         │
 │  ┌──────────────▼──────────────────────────────────┐  │
 │  │  EvidenceClient  (PY-M03)                        │  │
 │  │  LaravelClient (HTTPX async)                    │  │
 │  │   Bearer: service:internal Sanctum token        │  │
 │  │   Retry: 2x backoff on 5xx/network              │  │
 │  │   Returns None on 404 (missing evidence OK)     │  │
 │  │                                                  │  │
 │  │  20 endpoints across 8 modules:                 │  │
 │  │   users · orders · blockchain · providers        │  │
 │  │   ledger · reconciliation · risk                 │  │
 │  └─────────────────────────────────────────────────┘  │
 │                                                        │
 │  FastAPI  /api/v1/                                     │
 │   GET  /health    (liveness)                           │
 │   GET  /ready     (DB readiness)                       │
 │   (investigations · findings · reports: PY-M06)        │
 └────────────────────────────────────────────────────────┘
              │
              ▼
  agio_investigation  (PostgreSQL :5437)
  Tables built so far:
    event_inbox            ← M02
  Coming:
    investigations         ← M04
    evidence_records       ← M04
    investigation_findings ← M05
    investigation_reports  ← M05
    investigation_feedback ← M06
```

---

## Key Design Decisions

| Decision | Why |
|---|---|
| Separate `agio_investigation` database | No cross-service SQL joins; Python never touches Laravel's DB |
| DB insert **before** ACK | Durability-first — crash between insert and ACK → safe redelivery |
| `ON CONFLICT DO NOTHING` on `event_id` | At-least-once delivery is harmless; idempotent by design |
| `event_version = "1.0"` string (not int) | Matches actual Laravel `OutboxEventFactory` output (audit finding) |
| 10 `WIRED_EVENT_TYPES` only | Only subscribe to what actually exists; `order.status_changed` is the lifecycle umbrella |
| `Decimal` everywhere for money | Zero float rounding errors in financial comparisons |
| `EvidenceClient` returns `None` on 404 | Missing evidence is a valid investigable finding, not a crash |
| Passive-first topology declare | Laravel owns the RabbitMQ topology; Python doesn't clobber it |
| `service:internal` Sanctum ability | Actual auth ability (not `service:investigation:read` from blueprint) |

---

## Configuration (`core/config.py`)

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `INVESTIGATION_DATABASE_URL` | ✅ | — | asyncpg connection string |
| `RABBITMQ_URL` | optional | — | Consumer skipped if unset |
| `RABBITMQ_EXCHANGE` | — | `agio.events` | Topic exchange |
| `RABBITMQ_QUEUE` | — | `agio.investigation` | Main consumer queue |
| `RABBITMQ_DLX` | — | `agio.dlx` | Dead-letter exchange |
| `RABBITMQ_RETRY_EXCHANGE` | — | `agio.retry` | Retry exchange |
| `RABBITMQ_RETRY_QUEUE` | — | `agio.retry.60s` | 60s TTL retry queue |
| `RABBITMQ_MAX_RETRIES` | — | `3` | Consumer-side retry cap |
| `LARAVEL_INTERNAL_API_URL` | optional | — | Base URL for evidence API |
| `LARAVEL_SERVICE_TOKEN` | optional | — | Sanctum Bearer token |
| `LARAVEL_API_TIMEOUT_SECONDS` | — | `10` | Per-request timeout |
| `LARAVEL_API_MAX_RETRIES` | — | `2` | Retry on 5xx/network |
| `LLM_PROVIDER` | — | `disabled` | LLM disabled until M07 |

---

## Laravel Internal API — 20 Endpoints

All endpoints live at `http://app:8000/api/internal/investigation/v1/`.
Auth: `Authorization: Bearer <LARAVEL_SERVICE_TOKEN>` (Sanctum, ability `service:internal`).

| Module | Method | Endpoint | Schema |
|---|---|---|---|
| users | GET | `/users/{id}/profile` | `UserProfile` |
| users | GET | `/users/{id}/activity` | `UserActivityResponse` |
| users | GET | `/users/{id}/risk-summary` | `UserRiskSummaryResponse` |
| users | GET | `/users/{id}/orders` | `UserOrdersResponse` (paginated) |
| users | GET | `/users/{id}/wallets` | `UserWalletsResponse` |
| users | GET | `/users/{id}/ledger` | `UserLedgerResponse` |
| users | GET | `/users/{id}/compliance` | `UserComplianceResponse` |
| orders | GET | `/orders/{uuid}` | `OrderDetailResponse` |
| orders | GET | `/orders/{uuid}/timeline` | `OrderTimelineResponse` |
| blockchain | GET | `/orders/{uuid}/blockchain` | `OrderBlockchainResponse` |
| blockchain | GET | `/blockchain/deposits/{id}` | `BlockchainDepositDetailResponse` |
| blockchain | GET | `/deposits/{id}` | `DepositDetailResponse` |
| blockchain | GET | `/withdrawals/{id}` | `WithdrawalDetailResponse` |
| providers | GET | `/orders/{uuid}/provider` | `OrderProviderResponse` |
| providers | GET | `/providers/{identifier}` | `ProviderDetailResponse` |
| providers | GET | `/providers/{identifier}/executions` | `ProviderExecutionsResponse` |
| ledger | GET | `/orders/{uuid}/ledger` | `OrderLedgerResponse` |
| ledger | GET | `/users/{id}/ledger` | `UserLedgerResponse` |
| ledger | GET | `/ledger/journal/{uuid}` | `JournalEntryDetailResponse` |
| reconciliation | GET | `/orders/{uuid}/reconciliation` | `OrderReconciliationResponse` |
| risk | GET | `/risk-cases/{uuid}` | `RiskCaseDetailResponse` |

---

## Test Suite (45 tests)

| File | Type | Count | Requires |
|---|---|---|---|
| `test_health.py` | API | 2 | DB on :5437 |
| `test_consumer.py` | Schema + DB | 6 | DB on :5437 |
| `test_laravel_schemas.py` | Contract | 19 | Nothing — pure Python |
| `test_laravel_client.py` | Integration | 18 | Laravel on :8000 + token |

```bash
# All tests (live integration skipped if token absent)
pytest

# No external dependencies
pytest -m "not requires_laravel"

# Live integration only
LARAVEL_SERVICE_TOKEN=<token> pytest tests/test_laravel_client.py -v

# DB tests only
pytest -m requires_db
```

---

## Docker Services

| Service | Image | Port | Network |
|---|---|---|---|
| `investigation-database` | postgres:16-alpine | 5437 | default |
| `investigation-api` | local build | 8001 | default + `paymentexchange_payment-exchange` |

The `investigation-api` container joins Laravel's external Docker network so it can reach `rabbitmq` and `app` by container name. If the Laravel stack is not running the service still starts — RabbitMQ and the Laravel token are both optional at boot.

```bash
# Start the service and its database
docker compose up -d investigation-database investigation-api

# Health check
curl http://localhost:8001/api/v1/health
curl http://localhost:8001/api/v1/ready
```

---

## Milestone Status

| Milestone | Description | Status |
|---|---|---|
| PY-M00 | Repository & contract verification | ✅ Done |
| PY-M01 | Python service foundation (FastAPI + SQLAlchemy + Docker) | ✅ Done |
| PY-M02 | RabbitMQ consumer + durable event inbox | ✅ Done |
| PY-M03 | Laravel internal API client + evidence schemas | ✅ Done |
| PY-M04 | Investigation DB models + evidence collection | ✅ Done |
| PY-M05 | Deterministic rule engine | ✅ Done |
| PY-M06 | Admin-facing investigation API | ✅ Done |
| PY-M07 | LLM explanation layer | ✅ Done |
| PY-M08 | RAG knowledge base | 📋 Future -- optional, not required for operational service |
| PY-M09 | Production hardening | ✅ Done |

> **PY-M08 (RAG) decision:** not implemented. The investigation engine is fully operational without RAG.
> RAG will be re-evaluated once the service is running in production and there is evidence that
> retrieval of approved policies or historical cases would materially improve investigation quality.
> Prerequisites before PY-M08 is considered: pgvector enabled, embedding provider selected,
> knowledge base curated and approved, retrieval quality baseline established.
