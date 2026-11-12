# AGIO Investigation Readiness Audit (PY-M00)

**Audit date:** 2026-11-12
**Scope:** Verify the post-M15/M16/M17 state of the Laravel PaymentExchange implementation against the Python service blueprint (`AGIO_PYTHON_IMPLEMENTATION_PLAN.md`) and the data contract (`.agents/tasks/agio-investigation-data-contract.md` v1.1).
**Mode:** READ-ONLY. No application code, migrations, configuration, Docker Compose, or database records were modified.

---

## 1. Executive Summary

The Laravel side of the integration is **ready** and internally consistent. M15 (transactional outbox + RabbitMQ publisher), M16 (internal investigation API + service auth), and M17 (missing data fields) are all implemented and committed. The actual implementation matches the v1.1 data contract almost exactly.

**However, the Python implementation blueprint makes several assumptions that directly contradict the actual implementation.** The blueprint is a higher-level design document whose RabbitMQ queue names, event-version type, event-type catalogue, and service-token ability string are all wrong relative to what Laravel actually declares and emits. If the Python service is built against the blueprint without these corrections, it will bind to non-existent queues, fail to authenticate, and parse event envelopes incorrectly.

### Headline verdicts

| Area | Blueprint claim | Actual | Verdict |
|---|---|---|---|
| RabbitMQ main queue | `investigation.events` | `agio.investigation` | **CONTRADICTED** |
| RabbitMQ retry queue | `investigation.retry` | `agio.retry.60s` | **CONTRADICTED** |
| RabbitMQ DLQ queue | `investigation.dlq` | `agio.dead_letters` | **CONTRADICTED** |
| DLX / retry exchange names | implied `investigation.*` | `agio.dlx` / `agio.retry` | **CONTRADICTED** (blueprint omits real names) |
| Event `event_version` type | integer `1` | string `"1.0"` | **CONTRADICTED** |
| Event-type catalogue | ~22 independent event types | only 10 emitted; lifecycle collapses into `order.status_changed` | **CONTRADICTED / PARTIAL** |
| Service token ability | `service:investigation:read` | `service:internal` | **CONTRADICTED** |
| Internal API base path | `/api/internal/investigation/v1/` | matches | VERIFIED |
| Internal API endpoints | ~20 listed | 20 registered; 2 blueprint endpoints missing, 2 use a different path param | PARTIAL |
| M17 data fields | 8 fields/tables needed | all 8 present (migration + fillable) | VERIFIED |
| Outbox publisher reliability | race-safe, confirms, scheduled | confirmed (one minor: `lockForUpdate` not `SKIP LOCKED`) | VERIFIED (with note) |
| Docker Compose RabbitMQ | service + app env | present, `RABBITMQ_HOST=rabbitmq` | VERIFIED |
| Docker Compose investigation DB | none expected | none present | VERIFIED |

**Counts:** VERIFIED ≈ 18 distinct items; CONTRADICTED ≈ 7 (the four RabbitMQ queue/exchange names, `event_version` type, the event-type catalogue, and the auth ability string); PARTIAL ≈ 2 (API endpoint coverage, `causation_id`); MISSING/NOT WIRED = 16 of 26 catalogued event types.

**Bottom line for PY-M01+:** Build against the **v1.1 data contract and this audit**, not the blueprint's section 5.1/6.1/7.7 literals. The contract is correct; the blueprint's concrete literals are not.

---

## 2. Section A — RabbitMQ Topology (actual vs blueprint)

### Actual topology declared by Laravel

Declared in `app/Services/OutboxPublisherService.php::declareTopology()` (lines ~106–181), driven by `config/rabbitmq.php`.

**Exchanges** (`OutboxPublisherService.php` lines ~107–132):

| Exchange | Type | Durable | Source |
|---|---|---|---|
| `agio.events` | topic | yes | `config/rabbitmq.php` `'exchange'` |
| `agio.dlx` | direct | yes | `config/rabbitmq.php` `'dlx'` |
| `agio.retry` | topic | yes | `config/rabbitmq.php` `'retry_exchange'` |

**Queues** (`OutboxPublisherService.php` lines ~136–181):

| Queue | Bound to | Routing key | Arguments | Purpose |
|---|---|---|---|---|
| `agio.investigation` | `agio.events` | `#` | `x-dead-letter-exchange=agio.dlx` | Main consumer queue |
| `agio.dead_letters` | `agio.dlx` | `#` | (none) | Dead-letter queue |
| `agio.retry.60s` | `agio.retry` | `#` | `x-message-ttl=60000`, `x-dead-letter-exchange=agio.events` | Retry queue, 60s TTL, republishes to `agio.events` |

Binding keys are all `#` (catch-all). The published routing key is the literal `event_type` string (`OutboxPublisherService.php` line ~75: `$routingKey = $envelope['event_type']`), so individual routing keys like `order.created` are set on the message, but the queue is bound with `#` and therefore receives every event.

### Blueprint vs actual

The blueprint (section 5.1) proposes:
- Exchange `agio.events` — **matches**.
- Main queue `investigation.events` — **does NOT match** (actual `agio.investigation`).
- Retry queue `investigation.retry` — **does NOT match** (actual `agio.retry.60s`).
- Dead-letter queue `investigation.dlq` — **does NOT match** (actual `agio.dead_letters`).
- Blueprint does not name the DLX/retry *exchanges*; actual are `agio.dlx` (direct) and `agio.retry` (topic), not `investigation.*`.

### CRITICAL correction for the Python consumer

The Python service MUST NOT assume queues named `investigation.events` / `investigation.retry` / `investigation.dlq`. Those queues do not exist and are never declared.

Two valid strategies for the Python consumer:

1. **Consume the Laravel-declared queue directly.** Laravel already declares and binds `agio.investigation` to `agio.events` with `#`. The Python consumer can consume `agio.investigation` as-is and will receive every event. This is the simplest path and what the verified end-to-end test (Laravel outbox → `agio.investigation`) demonstrated.

2. **Declare its own queue.** The Python service may declare its own durable queue (any name) and bind it to `agio.events` with routing key `#` (or a curated set of `event_type` routing keys). It will then get its own independent copy of every matching event. This is the recommended approach for an independent service so Laravel's publisher does not have to know about it.

For retry/DLQ semantics, the Python service should model its own consumer-side retry. The Laravel side only provides the *publisher* path plus the three queues above; the retry/DLX topology is sized for a single shared `agio.investigation` consumer, not for arbitrary Python consumer queues.

**Verdict:** RabbitMQ topology — **CONTRADICTED** on queue/exchange names; the exchange `agio.events` is correct, everything else in the blueprint's section 5.1 is wrong and must be replaced with the names above.

---

## 3. Section B — Event Envelope Schema (actual vs blueprint)

Actual envelope is produced by `OutboxEventFactory::envelope()` (`app/Services/OutboxEventFactory.php` lines ~233–251). Top-level keys, in order:

```
event_id        => (string) Str::uuid()
event_type      => string  (e.g. "order.status_changed")
event_version   => "1.0"               <-- STRING, not integer
occurred_at     => now()->toIso8601ZuluString()   <-- e.g. "2026-11-12T03:00:00Z"
producer        => "agio-paymentexchange"
correlation_id  => (string) Str::uuid()
causation_id    => null                 <-- always null in the current factory
entity          => [ "type" => <string>, "id" => <string> ]   <-- NESTED object
data            => [ ... event-specific ... ]
```

The envelope is stored verbatim as the `payload` JSON column on `outbox_events` (`database/migrations/2026_11_12...`/`2026_10_18_181524_create_outbox_events_table.php`; `app/Models/OutboxEvent.php` casts `payload` to array) and published unchanged to RabbitMQ (`OutboxPublisherService::buildMessage()` lines ~184–194 sets AMQP `message_id=event_id`, `type=event_type`, `content_type=application/json`, `delivery_mode=2` persistent).

### Blueprint vs actual (section 6.1 example envelope)

| Field | Blueprint example | Actual | Verdict |
|---|---|---|---|
| `event_version` | integer `1` | string `"1.0"` | **CONTRADICTED** |
| `entity` | (blueprint implies nested object) | nested `{type, id}` | VERIFIED |
| `producer` | (blueprint implies present) | `"agio-paymentexchange"` | VERIFIED |
| `correlation_id` | present | present (uuid string) | VERIFIED |
| `causation_id` | present, may be set | present but **always null** (hardcoded in factory line ~247) | **PARTIAL** |
| `occurred_at` | ISO-8601 | `toIso8601ZuluString()` → ISO-8601 UTC, e.g. `2026-11-12T03:00:00Z` | VERIFIED |
| monetary values | (rule: decimal strings) | all cast `(string)` in factory | VERIFIED |

### Findings

- **`event_version` is the string `"1.0"`, NOT the integer `1`.** The blueprint's section 6.1 example shows `'event_version': 1` (integer). The Python schema MUST declare this as a string and compare against `"1.0"`. An integer comparison will silently mismatch.
- **`entity` is a nested object** `{ "type": ..., "id": ... }`. There are no flat `entity_type` / `entity_id` keys. The Python consumer must read `entity.type` and `entity.id`.
- **`causation_id` is always `null`** in the current implementation. The data contract (v1.1) describes it as potentially holding a parent `event_id`, but `OutboxEventFactory::envelope()` hardcodes `'causation_id' => null` and no call site overrides it. The Python consumer should treat `causation_id` as nullable/always-null for now; do not build causal-chain logic expecting it to be populated.
- **`correlation_id` is a fresh UUID per event**, not a propagated trace ID. It is not a meaningful correlation across events; treat it as opaque.
- **`occurred_at`** uses Laravel's `toIso8601ZuluString()`, which produces `YYYY-MM-DDTHH:MM:SSZ` (no fractional seconds, Z suffix). The Python parser should accept ISO-8601 with a trailing `Z`.

**Verdict:** Event envelope — **CONTRADICTED** on `event_version` type; **PARTIAL** on `causation_id`; otherwise VERIFIED.

---

## 4. Section C — Event Producer Wiring Map

The factory (`OutboxEventFactory.php`) defines builders for 14 event types, but only the ones actually invoked by `OutboxEvent::create(...)` in a transaction are ever published. I grepped the whole `app/` tree for `OutboxEvent::create` and `OutboxEventFactory::`, and for each catalogued event_type string.

**WIRED = 10** (an `OutboxEvent::create()` with that `event_type` exists in `app/`):

| Event type | Wired where | File:method (approx. line) |
|---|---|---|
| `order.created` | Order creation from accepted quote | `app/Services/OrderService.php::createFromQuote` (~121) |
| `order.status_changed` | Every order transition | `app/Services/OrderService.php::transition` (~179) |
| `risk_case.opened` | Risk case opening | `app/Services/ScreeningService.php::openRiskCase` (~146) |
| `user.login.succeeded` | Successful login | `app/Http/Controllers/Api/AuthController.php::login` (~164) |
| `user.login.failed` | Failed login (bad creds + inactive) | `app/Http/Controllers/Api/AuthController.php::login` (~89, ~117) |
| `withdrawal.submitted` | User-initiated withdrawal | `app/Http/Controllers/Api/WalletWithdrawalController.php::submitWithdrawal` (~202) |
| `withdrawal.approved` | Admin approves withdrawal | `app/Http/Controllers/Api/Admin/WithdrawRequestController.php::approve` (~138) |
| `withdrawal.rejected` | Admin rejects withdrawal | `app/Http/Controllers/Api/Admin/WithdrawRequestController.php::reject` (~263) |
| `kyc.approved` | Admin approves KYC doc | `app/Http/Controllers/Api/Admin/KycController.php::approve` (~99) |
| `kyc.rejected` | Admin rejects KYC doc | `app/Http/Controllers/Api/Admin/KycController.php::reject` (~134) |

**NOT WIRED = 16** (no `OutboxEvent::create()` produces these):

Factory method exists but is **never called** (4):

| Event type | Status | Note |
|---|---|---|
| `deposit.detected` | NOT WIRED | `OutboxEventFactory::depositDetected` (line ~78) exists but is never invoked. |
| `deposit.confirmed` | NOT WIRED | `OutboxEventFactory::depositConfirmed` (line ~98) exists but never invoked. |
| `deposit.mismatch` | NOT WIRED | `OutboxEventFactory::depositMismatch` (line ~116) exists but never invoked. |
| `payout.verified` | NOT WIRED | `OutboxEventFactory::payoutVerified` (line ~135) exists but never invoked. |

No factory method and no wiring (12):

| Event type | Status | Note |
|---|---|---|
| `order.completed` | NOT WIRED | Emitted as `order.status_changed` with `to_status=completed`. |
| `order.failed` | NOT WIRED | Emitted as `order.status_changed` with `to_status=failed`. |
| `order.refunded` | NOT WIRED | Emitted as `order.status_changed` with `to_status=refunded`. |
| `payout.failed` | NOT WIRED | Emitted as `order.status_changed` with `to_status=failed` (payout context). |
| `withdrawal.completed` | NOT WIRED | `Admin\WithdrawRequestController::complete` (line ~190) does NOT write an outbox event. |
| `kyc.submitted` | NOT WIRED | No factory method; KYC submission does not emit. |
| `risk_case.resolved` | NOT WIRED | No factory method; admin case resolution does not emit. |
| `user.registered` | NOT WIRED | No factory method; `AuthController::register` does not emit. |
| `user.kyc_status_changed` | NOT WIRED | No factory method; no wiring. |
| `provider.circuit_opened` | NOT WIRED | `LiquidityProvider::recordFailure` only does `Log::critical('provider.circuit_opened')` (~203) and `OperatorAlert::critical` (~211) — a log/alert, NOT an outbox event. |
| `provider.circuit_closed` | NOT WIRED | The code uses the key `provider.circuit_recovered` (line ~240), not `provider.circuit_closed`, and it is a log/alert, not an outbox event. |
| `reconciliation_case.opened` | NOT WIRED | No factory method; `SwapReconciliationService` does not emit. |

### CRITICAL: `order.status_changed` is the umbrella lifecycle event

This is the single most important wiring finding. `OrderService::transition()` is the one method that emits order lifecycle events, and it always emits `order.status_changed` with `data.from_status` / `data.to_status`. The specific transitions (`markDepositDetected`, `markDepositConfirmed`, `complete`, `fail`, `cancel`, `markRefunded`, `markPayoutPending`, `markPayoutVerified`, `markProviderHold`, `markAmountMismatch`, `markReconciliationRequired`, `resumeFromHold`) ALL route through `transition()`.

Therefore:
- Deposit detection/confirmation is signaled by `order.status_changed` with `to_status = deposit_detected` / `deposit_confirmed` — **not** by separate `deposit.detected` / `deposit.confirmed` events. Those factory methods are dead code today.
- Order completion, failure, and refund are signaled by `order.status_changed` with `to_status = completed` / `failed` / `refunded` — **not** by `order.completed` / `order.failed` / `order.refunded`.
- Payout verification is signaled by `order.status_changed` with `to_status = payout_verified` — **not** by `payout.verified`.

The blueprint lists ~22 event types as if they were all independently emitted. The Python consumer must instead:
- Bind to / handle `order.status_changed` as the primary transaction lifecycle signal.
- Parse `data.from_status`, `data.to_status`, `data.reason`, `data.actor_type`, `data.actor_id` to classify the transition.
- Expect only the 10 wired event types above; ignore (or log-but-skip) the others the blueprint mentions — they will never arrive.

**Verdict:** Event producers — **CONTRADICTED/PARTIAL**. 10 of 26 catalogued event types are actually wired; the lifecycle is collapsed into `order.status_changed`. The Python consumer's routing/filtering logic must be built on the 10 wired types and the `to_status` field, not on the blueprint's expanded catalogue.

---

## 5. Section D — Internal API Endpoint Map

### Actual registered internal routes

`routes/api.php` lines ~474–500 (outside the top-level `v1` prefix group, so the full path is `/api/internal/investigation/v1/...`):

```
Route::prefix('internal/investigation/v1')
    ->middleware(['throttle:api', 'auth.internal'])
    ->group(function () {
        GET users/{id}/profile
        GET users/{id}/activity
        GET users/{id}/risk-summary
        GET users/{id}/orders
        GET users/{id}/wallets
        GET users/{id}/ledger
        GET users/{id}/compliance
        GET orders/{uuid}
        GET orders/{uuid}/timeline
        GET orders/{uuid}/blockchain
        GET orders/{uuid}/provider
        GET orders/{uuid}/ledger
        GET orders/{uuid}/reconciliation
        GET ledger/journal/{uuid}
        GET deposits/{id}
        GET withdrawals/{id}
        GET blockchain/deposits/{id}
        GET risk-cases/{uuid}
        GET providers/{identifier}
        GET providers/{identifier}/executions
    });
```

Controller: `app/Http/Controllers/Api/Internal/InternalInvestigationController.php` (20 methods, all read-only GET).

### Blueprint endpoints vs actual

**Account intelligence** — all exist:

| Blueprint | Actual | Verdict |
|---|---|---|
| `/users/{id}/profile` | `GET users/{id}/profile` → `userProfile` | EXISTS |
| `/users/{id}/activity` | `GET users/{id}/activity` → `userActivity` (includes `user_sessions`, Spatie `activity_log`, `login_attempts`) | EXISTS |
| `/users/{id}/risk-summary` | `GET users/{id}/risk-summary` → `userRiskSummary` | EXISTS |
| `/users/{id}/orders` | `GET users/{id}/orders` → `userOrders` (paginated, params `status/date_from/date_to/currency`) | EXISTS |

**Transaction evidence** — all exist:

| Blueprint | Actual | Verdict |
|---|---|---|
| `/orders/{uuid}` | `GET orders/{uuid}` → `orderDetail` (`InternalOrderResource`) | EXISTS |
| `/orders/{uuid}/timeline` | `GET orders/{uuid}/timeline` → `orderTimeline` | EXISTS |
| `/orders/{uuid}/blockchain` | `GET orders/{uuid}/blockchain` → `orderBlockchain` | EXISTS |
| `/orders/{uuid}/provider` | `GET orders/{uuid}/provider` → `orderProvider` | EXISTS |
| `/orders/{uuid}/ledger` | `GET orders/{uuid}/ledger` → `orderLedger` | EXISTS |
| `/orders/{uuid}/reconciliation` | `GET orders/{uuid}/reconciliation` → `orderReconciliation` (includes `recovery_actions`) | EXISTS |

**Financial verification** — all exist:

| Blueprint | Actual | Verdict |
|---|---|---|
| `/users/{id}/wallets` | `GET users/{id}/wallets` → `userWallets` | EXISTS |
| `/users/{id}/ledger` | `GET users/{id}/ledger` → `userLedger` | EXISTS |
| `/ledger/journal/{uuid}` | `GET ledger/journal/{uuid}` → `journalEntry` | EXISTS |

**Blockchain / deposits & withdrawals** — partial:

| Blueprint | Actual | Verdict |
|---|---|---|
| `/deposits/{id}` | `GET deposits/{id}` → `depositDetail` (loads `DepositRequest`) | EXISTS |
| `/deposits/{id}/blockchain` | (no such route) | **MISSING** |
| `/blockchain/deposits/{id}` | `GET blockchain/deposits/{id}` → `blockchainDepositDetail` (loads `BlockchainDeposit`) | EXISTS |
| `/withdrawals/{id}` | `GET withdrawals/{id}` → `withdrawalDetail` (loads `WithdrawRequest`) | EXISTS |

**Compliance & risk** — partial:

| Blueprint | Actual | Verdict |
|---|---|---|
| `/risk-cases/{uuid}` | `GET risk-cases/{uuid}` → `riskCaseDetail` | EXISTS |
| `/users/{id}/compliance` | `GET users/{id}/compliance` → `userCompliance` | EXISTS |
| `/screening/{id}` | (no such route) | **MISSING** |
| `/reconciliation/cases/{uuid}` | (no standalone route; reconciliation is reachable only via `/orders/{uuid}/reconciliation`) | **MISSING** (as standalone) |

**Provider intelligence** — different path param:

| Blueprint | Actual | Verdict |
|---|---|---|
| `/providers/{id}` | `GET providers/{identifier}` → `providerDetail` (looks up by `LiquidityProvider::identifier`, e.g. `"changenow"`) | **DIFFERENT_PATH** (keyed by string `identifier`, not numeric `id`) |
| `/providers/{id}/executions` | `GET providers/{identifier}/executions` → `providerExecutions` (paginated, params `status/date_from/date_to`) | **DIFFERENT_PATH** (same) |

### Endpoints that would block PY-M03

- **`/deposits/{id}/blockchain`** — MISSING. The blueprint assumes a per-deposit blockchain detail endpoint. It does not exist. To get blockchain data for a deposit, the Python client should use `/orders/{uuid}/blockchain` (which returns both `blockchain_deposits` and `blockchain_payouts` for the order) or `/blockchain/deposits/{id}` (which takes a `BlockchainDeposit` integer id).
- **`/screening/{id}`** — MISSING. There is no standalone screening-result-by-id endpoint. Screening results are returned inside `/users/{id}/risk-summary`, `/users/{id}/compliance`, and `/risk-cases/{uuid}`. The Python client cannot fetch a single `ScreeningResult` by id directly.
- **`/reconciliation/cases/{uuid}`** — MISSING as a standalone route. Reconciliation cases are returned by `/orders/{uuid}/reconciliation`. There is no route to fetch a `ReconciliationCase` by its own UUID. (Note: `RecoveryAction` mentions `reconciliation_case_id`/`reconciliation_case_uuid`, but no internal route exposes a case by UUID.)
- **`/providers/{id}`** — the blueprint assumes a numeric `id`. The actual route is `/providers/{identifier}` keyed by the provider's string `identifier` (`"changenow"`, `"simpleswap"`, `"fixedfloat"`, `"changelly"`). This matches the data contract's constraint #7 ("Provider identifiers are strings"). The Python client must use the string identifier.

**Verdict:** Internal API — **PARTIAL**. 18 of 22 blueprint endpoints EXIST at the same path; 2 are MISSING (`/deposits/{id}/blockchain`, `/screening/{id}`), 1 is MISSING as a standalone (`/reconciliation/cases/{uuid}`), and 2 are DIFFERENT_PATH (`/providers/{id}` → `/providers/{identifier}`). Base path `/api/internal/investigation/v1/` matches.

---

## 6. Section E — Internal API Auth (the CRITICAL ability-string correction)

### Actual mechanism

- Middleware: `app/Http/Middleware/VerifyInternalServiceToken.php` (registered as the `auth.internal` alias in `bootstrap/app.php` line 38). Applied to the whole internal route group (`routes/api.php` line ~477).
- Token check (`VerifyInternalServiceToken.php`):
  - Reads the bearer token via `$request->bearerToken()` (i.e. `Authorization: Bearer <plainTextToken>`).
  - Resolves it via `Laravel\Sanctum\PersonalAccessToken::findToken($bearerToken)`.
  - Missing token → 401 `{"code":"UNAUTHORIZED","message":"Missing service token."}`.
  - Invalid token → 401 `{"code":"UNAUTHORIZED","message":"Invalid service token."}`.
  - Valid token **without** the required ability → 403 `{"code":"FORBIDDEN","message":"Token does not have the required ability."}`.
  - The required ability is **`service:internal`** (line ~19: `if (! $token->can('service:internal'))`).
  - On success: `auth()->setUser($token->tokenable)` and proceeds.
- Token issuance: `app/Console/Commands/IssueInternalServiceToken.php`, signature `investigation:issue-token {--name=agio-investigation-service}`. It `firstOrCreate`s a service user (`service-investigation@internal.agio`) and calls `$serviceUser->createToken($name, ['service:internal'])` — i.e. it grants exactly the `service:internal` ability (line ~28).

### Blueprint vs actual

| Blueprint (section 7.7) | Actual | Verdict |
|---|---|---|
| Ability `service:investigation:read` | `service:internal` | **CONTRADICTED** |

### CRITICAL correction

The Python client MUST request and present a token with the **`service:internal`** ability — not `service:investigation:read`. A token granted `service:investigation:read` will be rejected with 403 by the middleware, because the middleware checks `$token->can('service:internal')` and nothing else.

The exact header format is:

```
Authorization: Bearer <plainTextToken>
```

The plain-text token is produced once by `php artisan investigation:issue-token` (option `--name=agio-investigation-service`) and is a standard Sanctum personal access token (`<id>|<hash>` form). The Python client sends it verbatim in the `Authorization` header.

**Verdict:** Auth — **CONTRADICTED**. Use ability `service:internal`, Bearer header.

---

## 7. Section F — M17 Data Fields Verification

All 8 M17 fields/tables exist in the migrations dated `2026_11_12_*` AND in the corresponding model `$fillable`.

| Field / table | Migration | Model `$fillable` | Verdict |
|---|---|---|---|
| `orders.created_from_ip` | `2026_11_12_000001_add_created_from_ip_to_orders_table.php` (nullable string after `idempotency_key`) | `app/Models/Order.php` includes `'created_from_ip'` | VERIFIED |
| `blockchain_deposits.from_address` | `2026_11_12_000002_add_blockchain_fields_to_blockchain_deposits_table.php` (nullable string after `deposit_address`) | `app/Models/BlockchainDeposit.php` includes `'from_address'` | VERIFIED |
| `blockchain_deposits.block_number` | same migration (nullable `unsignedBigInteger` after `from_address`) | `app/Models/BlockchainDeposit.php` includes `'block_number'` | VERIFIED |
| `blockchain_payouts.block_number` | `2026_11_12_000003_add_block_number_to_blockchain_payouts_table.php` (nullable `unsignedBigInteger` after `raw_data`) | `app/Models/BlockchainPayout.php` includes `'block_number'` | VERIFIED |
| `provider_executions.failure_reason` | `2026_11_12_000004_add_failure_reason_to_provider_executions_table.php` (nullable string after `executed_at`) | `app/Models/ProviderExecution.php` includes `'failure_reason'` | VERIFIED |
| `users.phone_verified_at` | `2026_11_12_000005_add_account_fields_to_users_table.php` (nullable timestamp after `phone`) | `app/Models/User.php` includes `'phone_verified_at'` | VERIFIED |
| `users.account_type` | same migration — `enum('account_type', ['individual','business'])` default `'individual'` after `status` | `app/Models/User.php` includes `'account_type'` | VERIFIED |
| `login_attempts` table | `2026_11_12_000006_create_login_attempts_table.php` (columns: `email, ip_address, user_agent, failure_reason, attempted_at`, indexes on `(email, attempted_at)` and `ip_address`) | `app/Models/LoginAttempt.php` `$fillable` = `email, ip_address, user_agent, failure_reason, attempted_at` | VERIFIED |

Wiring confirmation:
- `created_from_ip` is written in `OrderService::createFromQuote` (`'created_from_ip' => $data['created_from_ip'] ?? null`) and surfaced in `InternalOrderResource` (`'created_from_ip' => $this->created_from_ip ?? null`).
- `from_address` and `block_number` are written in `app/Jobs/RecordBlockchainDeposit.php` (~lines 95–99) from `rawData['fromAddress']`/`rawData['from_address']` and `rawData['blockNumber']`/`rawData['block_number']`.
- `account_type` and `phone_verified_at` are surfaced in `InternalUserProfileResource` (`account_type`, `phone_verified_at`).
- `login_attempts` is written by `AuthController::login` for both success (`failure_reason => null`) and failure, and read by `InternalInvestigationController::userActivity`.

`account_type` allowed values: `'individual'` and `'business'`, default `'individual'`.

**Verdict:** M17 fields — all 8 **VERIFIED** (migration + model fillable, and wired into read paths).

---

## 8. Section G — Outbox Publisher Reliability

Read `app/Console/Commands/PublishOutboxEvents.php`.

| Question | Finding | Verdict |
|---|---|---|
| Uses `lockForUpdate` / `SELECT FOR UPDATE SKIP LOCKED` for race safety? | Uses `->lockForUpdate()` inside `DB::transaction` (~line 30), which emits `SELECT ... FOR UPDATE`. It does **NOT** use `SKIP LOCKED`. | PARTIAL — race-safe via row locks + scheduler overlap guard, but not `SKIP LOCKED`. See note. |
| Marks `published_at` only AFTER broker ACK (publisher confirms)? | Yes. `OutboxPublisherService::connect()` calls `confirm_select()`; `publishBatch()` calls `wait_for_pending_acks(30.0)` and throws on NACK. `published_at` is set only in the success branch (~lines 55–62). | VERIFIED |
| Scheduled in `routes/console.php`? | Yes — `Schedule::command('app:publish-outbox-events')->everyMinute()->withoutOverlapping(1)->runInBackground()` (last entry in `routes/console.php`). | VERIFIED (every minute, `withoutOverlapping(1)`, backgrounded) |
| RabbitMQ unavailable behavior | `connect()` is wrapped in try/catch for `BrokerUnavailableException` (~lines 38–42); on failure it logs a warning and `return 1` (graceful exit). No events are marked published; `published_at` stays NULL. | VERIFIED (exits gracefully, does not mark published, does not throw) |
| Failure accounting | On batch publish failure, every event in the batch gets `attempts = attempts + 1` and `last_error = $e->getMessage()` (~lines 64–73). Events with `attempts < 5` are retried on the next run. | VERIFIED |
| Outbox table columns | `outbox_events` has `event_id` (unique), `event_type`, `payload` (json), `published_at`, `attempts` (default 0), `last_error`, index on `(published_at, created_at)`. | VERIFIED |

Notes / minor observations (not blockers):
- **No `SKIP LOCKED`.** Because the command runs with `withoutOverlapping(1)`, only one instance runs at a time, so the absence of `SKIP LOCKED` is acceptable in practice. If the Python side ever runs multiple publisher workers, switching to `lockForUpdate()->skipLocked()` (Laravel's `skipLocked()`) would be advisable. The data contract (v1.1 Part 8) mentions `SELECT FOR UPDATE SKIP LOCKED` as the intended pattern; the implementation uses `FOR UPDATE` only. This is a **PARTIAL** match to the contract, not a correctness bug under the current single-publisher scheduler.
- **`attempts` increments on success too.** The success branch sets `attempts = attempts + 1` alongside `published_at`. This is cosmetic (attempts is not used for published events) but deviates from the usual "attempts counts failures" convention.
- **Batch granularity.** Up to 100 events are published as one batch and acknowledged as one batch. A single NACK fails the whole batch and increments all 100 attempts. There is no per-event publish/ACK. Acceptable for current volumes.
- The `OutboxEventTest.php` feature suite (6 tests) covers: order creation → `order.created`, order transition → `order.status_changed`, risk case → `risk_case.opened`, successful login → `user.login.succeeded`, withdrawal → `withdrawal.submitted`, and rollback atomicity (outbox event NOT created when the transaction rolls back). It does not test the publisher's confirm path or the RabbitMQ-unavailable path.

**Verdict:** Outbox publisher — **VERIFIED** for correctness (confirms-based `published_at`, graceful broker-unavailable exit, scheduled every minute with overlap guard). **PARTIAL** vs the contract's `SKIP LOCKED` wording, which is a non-issue under the single-publisher scheduler.

---

## 9. Section H — Docker Compose RabbitMQ

Read `docker-compose.yml`.

| Question | Finding | Verdict |
|---|---|---|
| `rabbitmq` service present? | Yes. Image `rabbitmq:3.13-management-alpine` (management UI included). Container `payment-exchange-rabbitmq`. | VERIFIED |
| Ports 5672 / 15672? | Yes — `${RABBITMQ_PORT:-5672}:5672` and `${RABBITMQ_MGMT_PORT:-15672}:15672`. | VERIFIED |
| Credentials / vhost? | `RABBITMQ_DEFAULT_USER=${RABBITMQ_USER:-agio}`, `RABBITMQ_DEFAULT_PASS=${RABBITMQ_PASS:-agio_secret}`, `RABBITMQ_DEFAULT_VHOST=${RABBITMQ_VHOST:-agio}`. Persistent volume `rabbitmq_data`. Healthcheck `rabbitmq-diagnostics ping`. | VERIFIED |
| App receives `RABBITMQ_*` env vars? | Yes, the `app` service block sets `RABBITMQ_HOST: rabbitmq` (the compose service name), plus `RABBITMQ_PORT/USER/PASS/VHOST/EXCHANGE/DLX/RETRY_EXCHANGE/RETRY_TTL_MS`. | VERIFIED (`RABBITMQ_HOST` correctly set to the service name `rabbitmq`) |
| `.env.example` documents the vars? | Yes — `.env.example` lines ~205–214 list all `RABBITMQ_*` vars with the same defaults (host `127.0.0.1` for local non-Docker use, exchange `agio.events`, dlx `agio.dlx`, retry `agio.retry`, ttl `60000`). | VERIFIED |
| Separate investigation-database service? | **No** — compose defines `db` (primary), `db-replica-1`, `db-replica-2`, `redis`, `rabbitmq`, `app`, `mailpit`, `frontend`. No investigation database service. | VERIFIED (matches expectation — Python will add its own DB in its own repo/compose) |

**Verdict:** Docker Compose RabbitMQ — **VERIFIED**. The Python service's own compose (in its separate repo) should connect to this `rabbitmq` service / its `agio` vhost, or run its own RabbitMQ for development. No investigation DB is provisioned by Laravel, as expected.

---

## 10. Section I — Discrepancy Summary Table

Every place the blueprint's assumption differs from the actual implementation, with the exact correction the Python service must adopt.

| # | Blueprint assumption | Actual implementation | Exact correction for the Python service |
|---|---|---|---|
| 1 | Main queue `investigation.events` (`OutboxPublisherService` declares `agio.investigation`) | `agio.investigation`, bound to `agio.events` with `#` | Consume the existing `agio.investigation` queue, OR declare your own durable queue bound to `agio.events` with `#`. Never expect `investigation.events`. |
| 2 | Retry queue `investigation.retry` | `agio.retry.60s` (TTL 60000ms, republishes to `agio.events`) | If you want Laravel-style retry, use `agio.retry` exchange + a TTL queue patterned on `agio.retry.60s`. Preferably manage retry on the consumer side. Do not use `investigation.retry`. |
| 3 | Dead-letter queue `investigation.dlq` | `agio.dead_letters`, bound to `agio.dlx` | DLQ is `agio.dead_letters`; the dead-letter *exchange* is `agio.dlx` (direct). Do not use `investigation.dlq`. |
| 4 | (Implied) `investigation.*` exchange names | `agio.events` (topic), `agio.dlx` (direct), `agio.retry` (topic) | The only exchange the Python consumer needs is `agio.events`. DLX/retry exchanges are `agio.dlx` / `agio.retry`. |
| 5 | `event_version` is integer `1` | `event_version` is string `"1.0"` | Declare/parse `event_version` as a **string**; compare against `"1.0"`. |
| 6 | ~22 independent event types emitted | Only 10 are wired; order lifecycle collapses into `order.status_changed` | Bind to/handle the 10 wired event types. Treat `order.status_changed` as the lifecycle signal and classify by `data.to_status`. Do not expect `order.completed/failed/refunded`, `deposit.detected/confirmed/mismatch`, `payout.verified/failed`, `withdrawal.completed`, `kyc.submitted`, `risk_case.resolved`, `user.registered`, `user.kyc_status_changed`, `provider.circuit_*`, `reconciliation_case.opened` as distinct events. |
| 7 | Service token ability `service:investigation:read` | Ability `service:internal` | Request and present a Sanctum token with the `service:internal` ability (via `php artisan investigation:issue-token`). Send as `Authorization: Bearer <token>`. |
| 8 | `/providers/{id}` (numeric) | `/providers/{identifier}` (string identifier) | Call `/api/internal/investigation/v1/providers/{identifier}` with the string identifier (e.g. `changenow`), not a numeric id. |
| 9 | `/providers/{id}/executions` | `/providers/{identifier}/executions` | Same — use the string identifier. |
| 10 | `/deposits/{id}/blockchain` | Does not exist | Use `/orders/{uuid}/blockchain` or `/blockchain/deposits/{id}` instead. |
| 11 | `/screening/{id}` | Does not exist | Get screening results via `/users/{id}/risk-summary`, `/users/{id}/compliance`, or `/risk-cases/{uuid}`. No single-screening-result fetch. |
| 12 | `/reconciliation/cases/{uuid}` (standalone) | Does not exist as a standalone route | Get reconciliation cases via `/orders/{uuid}/reconciliation` (returns `reconciliation_cases` + `recovery_actions`). |
| 13 | (Data contract) `causation_id` may carry a parent event id | `causation_id` is hardcoded `null` and never set | Treat `causation_id` as always-null for now; do not build causal-chain logic on it. |

All other items (exchange `agio.events` name/type, `entity` nested-object shape, `producer`, `correlation_id`, decimal-string monetary values, ISO-8601 `occurred_at`, internal API base path `/api/internal/investigation/v1/`, the 18 matching endpoints, all 8 M17 fields, publisher confirms + scheduling + graceful broker-unavailable handling, Docker RabbitMQ service + app env) are **VERIFIED** and need no correction.

---

## 11. Implementation Sequence Recommendation (PY-M01 onward, adjusted to actual state)

Given the actual implementation, the Python milestones should deviate from the blueprint's literal assumptions in these ways:

1. **PY-M01 (service skeleton) — no change needed.** Standard FastAPI/SQLAlchemy/Alembic/aio-pika scaffold in a separate repo. Add a dedicated `agio_investigation` Postgres database in the Python repo's own compose; do not touch Laravel's compose.

2. **PY-M02 (RabbitMQ consumer) — apply corrections 1–4 and 6.**
   - Connect to `agio.events` (topic) on the `agio` vhost, credentials `agio`/`agio_secret` (or env-configured), host `rabbitmq` in Docker / `127.0.0.1` locally.
   - Either consume the pre-declared `agio.investigation` queue, or declare your own durable queue bound with `#`. Use `aio-pika`'s robust connection + manual ack after persisting the event.
   - Implement the envelope parser around the **actual** schema: `event_version` is the string `"1.0"`; `entity` is nested `{type, id}`; `causation_id` is nullable/usually null; `occurred_at` is ISO-8601 Zulu.
   - Build the event dispatcher on the **10 wired event types**, with `order.status_changed` as the central lifecycle handler that fans out on `data.to_status` (`deposit_detected`, `deposit_confirmed`, `completed`, `failed`, `refunded`, `payout_pending`, `payout_verified`, `amount_mismatch`, `provider_hold`, `reconciliation_required`, etc.). Store `received_at` separately from `occurred_at`; deduplicate on `event_id`.
   - Do NOT implement handlers expecting the 16 unwired event types; log-and-skip them if they ever appear.

3. **PY-M03 (Laravel API client) — apply corrections 7–12.**
   - Use a Sanctum token with the `service:internal` ability (issued via `php artisan investigation:issue-token --name=agio-investigation-service`), sent as `Authorization: Bearer <token>` against the `auth.internal` middleware.
   - Target `/api/internal/investigation/v1/`. All 20 actual GET endpoints are available.
   - For provider lookups use the string `identifier` path. For blockchain deposit detail use `/blockchain/deposits/{id}` (BlockchainDeposit id) or `/orders/{uuid}/blockchain`. There is no `/deposits/{id}/blockchain`, `/screening/{id}`, or standalone `/reconciliation/cases/{uuid}` — retrieve that data through the order- or user-scoped endpoints.

4. **PY-M04 (evidence collection + DB) — no change.** The 8 M17 fields are present and surfaced through the internal resources (`InternalOrderResource`, `InternalUserProfileResource`) and the activity/timeline endpoints. Build evidence snapshots with the provenance fields from data contract Part 2 (`content_hash`, `retrieved_at`, `is_verified`).

5. **PY-M05 (deterministic rules) — ground rules in the 10 wired events + `order_events` timeline.** Reconstruct order history from `order_events` (via `/orders/{uuid}/timeline`), never from `orders.status` alone. Use `account_type`, `created_from_ip`, `from_address`, `login_attempts`, `block_number`, and `failure_reason` which are now all available.

6. **PY-M06–M09 — no contract-level change.** Admin-facing API, LLM explanation, RAG, and hardening proceed per the blueprint; just keep the corrected event/endpoint/ability facts above.

7. **Optional Laravel follow-ups (not required for Python to start, but recommended):** wire `withdrawal.completed`, `user.registered`, `risk_case.resolved`, `kyc.submitted`, and the `deposit.*` / `payout.verified` factory methods into the outbox if distinct events are genuinely wanted; until then the Python service must rely on `order.status_changed` and the pull API. These are enhancement tasks for the Laravel repo, explicitly out of scope for the Python service's first milestones.

---

*End of PY-M00 audit. Generated by read-only inspection of the PaymentExchange repository on 2026-11-12. No files outside `docs/investigation/AGIO_INVESTIGATION_READINESS_AUDIT.md` were created or modified.*
