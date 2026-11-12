# AGIO Investigation Data Contract
**Version:** 1.1  
**Date:** 2026-11-12  
**Scope:** Defines every API endpoint, event schema, database field, and data permission the future Python investigation engine may consume from the AGIO PaymentExchange Laravel backend.

**Changelog from v1.0:**
- Broker changed from AWS EventBridge → RabbitMQ (self-hosted, local + VPS)
- Outbox write rule corrected: same `DB::transaction()` as business record (not after commit)
- Python findings schema added with evidence provenance fields
- Financial data boundary rules made explicit
- Event envelope marked broker-independent
- RabbitMQ topology defined (exchange, queues, routing keys, DLX, retry)
- All fields verified against actual migrations and models (see gap report)
- 8 missing M17 fields documented

---

## Three Governing Principles

1. **Python is a read-only consumer.** It never writes to Laravel's database.
2. **RabbitMQ is the streaming channel. Internal APIs are the pull channel.** Python uses events for real-time awareness and APIs for pulling additional evidence.
3. **The double-entry ledger (`journal_entries` / `ledger_postings`) is the financial source of truth.** The investigation engine must never compute or assert wallet balances.

---

## Readiness Score (verified against codebase)

| Category | Status | Score |
|----------|--------|-------|
| Core Architecture | EXISTS | ✅ |
| Transaction Lifecycle (Orders — 17 states) | EXISTS | ✅ |
| Wallet & Double-Entry Ledger | EXISTS | ✅ |
| Blockchain Deposits & Payouts | EXISTS | ✅ |
| Exchange & Provider Records | EXISTS | ✅ |
| Admin Audit Logs (Spatie + order_events) | EXISTS | ✅ |
| Reconciliation & Recovery | EXISTS | ✅ |
| Risk Case Management | EXISTS | ✅ |
| Outbox table + model | EXISTS | ✅ |
| OutboxEvent writes in services | **MISSING** | ❌ |
| RabbitMQ package + config + Docker service | **MISSING** | ❌ |
| PublishOutboxEvents — race-safe + confirms | **MISSING** | ❌ |
| PublishOutboxEvents scheduled | **MISSING** | ❌ |
| Failed Login Recording (`login_attempts`) | **MISSING** | ❌ |
| IP Address on Orders (`created_from_ip`) | **MISSING** | ❌ |
| `from_address` on blockchain_deposits | **MISSING** | ❌ |
| Internal Service Auth + API route group | **MISSING** | ❌ |
| `phone_verified_at` / `account_type` on users | **MISSING** | ❌ |
| KycController — DB::transaction() wrapper | **MISSING** | ❌ |
| WalletWithdrawalController — DB::transaction() | **MISSING** | ❌ |

**Overall: 8 of 20 items ready. The financial data model is excellent. The event pipeline and investigation API do not exist yet.**

---

## Part 1 — Data the Engine May Consume

### 1A. User & Account Data

**Source table:** `users` (all fields verified against migration `0001_01_01_000000`)

| Field | Column | Verified | Notes |
|-------|--------|----------|-------|
| `user_id` | `users.id` | ✅ | Integer PK |
| `account_status` | `users.status` | ✅ | `active / suspended / banned / pending_activation` |
| `created_at` | `users.created_at` | ✅ | UTC timestamp |
| `email_verified_at` | `users.email_verified_at` | ✅ | Null if unverified |
| `kyc_status` | `users.kyc_status` | ✅ | `none / pending / approved / rejected` |
| `country_code` | `users.country` | ✅ | Free-text — normalize to ISO 3166-1 alpha-2 (M17) |
| `two_factor_enabled` | `users.two_factor_enabled_at` | ✅ | Null if disabled |
| `phone_verified_at` | — | ❌ M17 | Column missing; needs migration |
| `account_type` | — | ❌ M17 | `individual / business` ENUM; needs migration |

**Source table:** `user_sessions` (verified against migration)

| Field | Column | Verified | Notes |
|-------|--------|----------|-------|
| Login IP address | `user_sessions.ip_address` | ✅ | Per successful session |
| Device / User-Agent | `user_sessions.device` | ✅ | Raw User-Agent string |
| Login timestamp | `user_sessions.created_at` | ✅ | — |
| Session revocation | `user_sessions.revoked_at` | ✅ | Null if active |
| Failed login result | — | ❌ M17 | `login_attempts` table does not exist |

**Source table:** `login_attempts` (M17 — does not exist yet)

| Field | Column | Notes |
|-------|--------|-------|
| email | `login_attempts.email` | Attempted email |
| IP address | `login_attempts.ip_address` | — |
| User agent | `login_attempts.user_agent` | — |
| Failure reason | `login_attempts.failure_reason` | `invalid_credentials / account_locked / unverified` |
| Attempted at | `login_attempts.attempted_at` | UTC timestamp |

**What to never send to Python:** `password`, `remember_token`, personal access tokens, 2FA secret (`user_two_factor_authentications.secret`), recovery codes, password reset tokens.

---

### 1B. Transaction (Order) Data

**Source table:** `orders` — all 17 fields verified against migration `2026_08_31_120000` and `Order.php` `$fillable`.

| Contract Field | Column | Verified | Notes |
|----------------|--------|----------|-------|
| `transaction_id` | `orders.uuid` | ✅ | Public UUID — always use this, never `id` |
| `user_id` | `orders.user_id` | ✅ | — |
| `sell_amount` | `orders.sell_amount` | ✅ | Decimal string |
| `buy_amount` | `orders.buy_amount` | ✅ | Decimal string (locked from quote) |
| `sell_currency` | via `sell_currency_id` | ✅ | Join to `currencies.code` |
| `buy_currency` | via `buy_currency_id` | ✅ | Join to `currencies.code` |
| `status` | `orders.status` | ✅ | 17-state machine |
| `risk_status` | `orders.risk_status` | ✅ | `pending / cleared / flagged / rejected` |
| `rate` | `orders.rate` | ✅ | — |
| `fee_amount` | `orders.fee_amount` | ✅ | — |
| `created_at` | `orders.created_at` | ✅ | — |
| `completed_at` | `orders.completed_at` | ✅ | Null if not completed |
| `provider_execution_id` | `orders.provider_execution_id` | ✅ | — |
| `deposit_detected_at` | `orders.deposit_detected_at` | ✅ | — |
| `deposit_confirmed_at` | `orders.deposit_confirmed_at` | ✅ | — |
| `provider_executed_at` | `orders.provider_executed_at` | ✅ | — |
| `idempotency_key` | `orders.idempotency_key` | ✅ | Deduplication key |
| `created_from_ip` | — | ❌ M17 | Column missing; needs migration |

**Order status state machine — all 17 values verified in `Order::STATUSES`:**

```
awaiting_deposit → deposit_detected → deposit_confirmed
  → risk_review (optional)
  → provider_execution_pending → provider_executed
  → settlement_pending → payout_pending → payout_verified
  → completed                              [TERMINAL]

  → failed                                 [TERMINAL]
  → cancelled → refund_pending → refunded  [TERMINAL]

  Hold states (non-terminal, non-progressing):
    provider_hold | amount_mismatch | reconciliation_required
```

**Terminal statuses:** `completed | failed | refunded`  
**Hold statuses:** `provider_hold | amount_mismatch | reconciliation_required`

**Source table:** `order_events` — append-only transition log. All 7 fields verified.

| Field | Column | Verified |
|-------|--------|----------|
| Event ID | `order_events.id` | ✅ |
| Order ID | `order_events.order_id` | ✅ |
| From status | `order_events.from_status` | ✅ |
| To status | `order_events.to_status` | ✅ |
| Actor type | `order_events.actor_type` | ✅ — `user / admin / system / provider` |
| Actor ID | `order_events.actor_id` | ✅ |
| Reason | `order_events.reason` | ✅ |
| Metadata | `order_events.metadata` | ✅ — JSON (tx hashes, provider responses, risk notes) |
| Timestamp | `order_events.created_at` | ✅ |

---

### 1C. Wallet & Ledger Data

**Double-entry ledger is the financial source of truth.**

**Source tables:** `ledger_accounts`, `journal_entries`, `ledger_postings`, `balance_holds` — all fields verified.

| Contract Field | Column | Verified | Notes |
|----------------|--------|----------|-------|
| Ledger account ID | `ledger_accounts.id` | ✅ | — |
| Account owner | `ledger_accounts.owner_type / owner_id` | ✅ | Morphic FK |
| Account type | `ledger_accounts.account_type` | ✅ | `customer_asset / customer_liability / platform_treasury / provider_receivable / provider_payable / fee_revenue / network_expense / provider_expense / refund_liability / suspense` |
| Currency | `ledger_accounts.currency_id` | ✅ | — |
| Journal entry UUID | `journal_entries.uuid` | ✅ | — |
| Reference type/ID | `journal_entries.reference_type / reference_id` | ✅ | Links entry to order/deposit/withdrawal |
| Status | `journal_entries.status` | ✅ | `pending / posted / reversed` |
| Reversal of | `journal_entries.reversal_of` | ✅ | FK to reversed entry |
| Debit | `ledger_postings.debit` | ✅ | Decimal; zero if credit side |
| Credit | `ledger_postings.credit` | ✅ | Decimal; zero if debit side |
| Posting timestamp | `ledger_postings.created_at` | ✅ | Immutable — no UPDATE/DELETE permitted |
| Balance hold | `balance_holds.*` | ✅ | Frozen amounts with reason + reference |

**Rule:** Use ledger postings for financial verification. Use `wallets.balance` only for current operational state.

---

### 1D. Blockchain Data

**Source table:** `blockchain_deposits` — all fields verified against migration `2026_09_29_000003`.

| Field | Column | Verified | Notes |
|-------|--------|----------|-------|
| ID | `blockchain_deposits.id` | ✅ | — |
| Order ID | `blockchain_deposits.order_id` | ✅ | — |
| Network | `blockchain_deposits.network_identifier` | ✅ | `TRC20 / ERC20 / SOL / BSC / POLYGON / BTC` |
| TX hash | `blockchain_deposits.tx_hash` | ✅ | — |
| `from_address` | — | ❌ M17 | Sender address not persisted; exists in `NormalizedTransaction::fromAddress` and `raw_data` JSON |
| `to_address` | `blockchain_deposits.deposit_address` | ✅ | Platform receive address |
| Asset | `blockchain_deposits.currency_code` | ✅ | — |
| Received amount | `blockchain_deposits.received_amount` | ✅ | — |
| Expected amount | `blockchain_deposits.expected_amount` | ✅ | — |
| Amount difference | `blockchain_deposits.amount_difference` | ✅ | — |
| Confirmations | `blockchain_deposits.confirmations` | ✅ | — |
| Required confirmations | `blockchain_deposits.required_confirmations` | ✅ | — |
| Status | `blockchain_deposits.status` | ✅ | `detected / confirming / confirmed / mismatch / overpaid / underpaid` |
| Block number/slot | — | ❌ M17 | Only in `raw_data` JSON; needs dedicated column |
| Detected at | `blockchain_deposits.detected_at` | ✅ | — |
| Confirmed at | `blockchain_deposits.confirmed_at` | ✅ | — |

**Source table:** `blockchain_payouts` — all fields verified against migration `2026_09_29_000004`.

| Field | Column | Verified | Notes |
|-------|--------|----------|-------|
| TX hash | `blockchain_payouts.tx_hash` | ✅ | — |
| Payout address | `blockchain_payouts.payout_address` | ✅ | — |
| Expected address | `blockchain_payouts.expected_address` | ✅ | — |
| Expected memo | `blockchain_payouts.expected_memo` | ✅ | — |
| Received / expected amounts | Both columns | ✅ | — |
| 5 verification flags | boolean columns | ✅ | — |
| Status | `blockchain_payouts.status` | ✅ | `pending / detected / confirming / verified / mismatch / amount_low / failed` |
| Verification notes | `blockchain_payouts.verification_notes` | ✅ | — |
| Block number/slot | — | ❌ M17 | Needs dedicated column |

---

### 1E. Exchange & Liquidity Provider Data

All fields verified against `orders`, `quotes`, `provider_executions`, `liquidity_providers` migrations.

| Field | Table/Column | Verified | Notes |
|-------|-------------|----------|-------|
| Exchange order UUID | `orders.uuid` | ✅ | — |
| Source / destination amounts | `orders.sell_amount`, `buy_amount` | ✅ | — |
| Actual destination | `provider_executions.executed_buy_amount` | ✅ | — |
| Rate | `orders.rate` | ✅ | — |
| Platform / provider / network fees | `quotes.*_fee` | ✅ | — |
| Provider identifier | `liquidity_providers.identifier` | ✅ | `changenow / simpleswap / fixedfloat / changelly` |
| Provider order ID | `provider_executions.provider_execution_id` | ✅ | — |
| Execution status | `provider_executions.status` | ✅ | `pending / executed / settlement_pending / completed / failed / cancelled` |
| Quote expiration | `quotes.expires_at` | ✅ | — |
| Providers tried | `quotes.providers_tried` | ✅ | JSON array |
| Raw responses | `provider_quotes.raw_response`, `provider_executions.raw_response` | ✅ | — |
| Failure reason | — | ❌ M17 | Only in `raw_response` JSON; needs `provider_executions.failure_reason` column |

---

### 1F. Compliance, Risk & Screening Data

All fields verified against `risk_cases` and `screening_results` migrations.

| Field | Column | Verified | Notes |
|-------|--------|----------|-------|
| Risk case UUID | `risk_cases.uuid` | ✅ | — |
| Severity | `risk_cases.severity` | ✅ | `low / medium / high / critical` |
| Status | `risk_cases.status` | ✅ | `open / investigating / customer_contacted / escalated / cleared / rejected` |
| Risk score | `screening_results.risk_score` | ✅ schema | Always `0.00` until real AML provider wired |
| Check type | `screening_results.check_type` | ✅ | `sanctions / pep / adverse_media / kyc_document` |
| Jurisdiction rules | `jurisdiction_rules.*` | ✅ | Fully operational |

---

### 1G. Admin Audit Log Data

| Data | Source | Verified |
|------|--------|----------|
| All model changes | `activity_log` (Spatie) | ✅ |
| Before/after values | `activity_log.attribute_changes` | ✅ |
| Causer (which admin) | `activity_log.causer_type / causer_id` | ✅ |
| Order state transitions | `order_events` | ✅ |
| Reconciliation problems | `reconciliation_cases` | ✅ |
| Recovery actions taken | `recovery_actions` | ✅ |
| Provider webhooks received | `webhook_events` | ✅ |

---

## Part 2 — Python Investigation Findings Schema

Python creates its own findings — it **never modifies** Laravel financial records.

**Fields Python must never overwrite:**
- `orders.status` / `orders.risk_status`
- `wallets.balance` / `ledger_postings.*`
- `blockchain_deposits.status` / `blockchain_payouts.status`
- `withdraw_requests.status` / `kyc_documents.status`
- `users.kyc_status`

### Finding record (Python-side PostgreSQL)

```json
{
  "finding_id":           "F-10001",
  "case_uuid":            "CASE-10001",
  "finding_type":         "unusual_transaction_velocity",
  "severity":             "high",
  "explanation":          "5 orders placed within 3 minutes from different IPs sharing the same destination address.",
  "requires_human_review": true,
  "evidence_refs": [
    { "type": "order", "id": "ord-uuid-1" },
    { "type": "order", "id": "ord-uuid-2" }
  ],

  "provenance": {
    "source_system":       "agio-paymentexchange",
    "source_record_type":  "order",
    "source_record_id":    "ord-uuid-1",
    "source_version":      "1.0",
    "retrieved_at":        "2026-11-12T03:00:00.000Z",
    "content_hash":        "sha256:abc123...",
    "is_verified":         true
  },

  "created_at":  "2026-11-12T03:00:05.000Z",
  "created_by":  "agio-investigation-engine/v1"
}
```

### Provenance fields (required on every finding)

| Field | Purpose |
|-------|---------|
| `source_system` | Which system the evidence came from |
| `source_record_type` | `order / ledger / risk_case / blockchain_deposit / ...` |
| `source_record_id` | The original record's UUID/ID |
| `source_version` | Event schema version or API response version |
| `retrieved_at` | When Python fetched this evidence from Laravel |
| `content_hash` | SHA-256 of the raw evidence payload at retrieval time |
| `is_verified` | Whether the source record was accessible and consistent |

---

## Part 3 — RabbitMQ Topology

### Infrastructure

| Component | Value |
|-----------|-------|
| Package (Laravel) | `php-amqplib/php-amqplib` (publisher confirms) |
| Docker image | `rabbitmq:3.13-management-alpine` |
| AMQP port | `5672` |
| Management UI | `15672` |
| Vhost | `agio` (configurable via `RABBITMQ_VHOST`) |

### Exchange topology

| Exchange | Type | Durable | Purpose |
|----------|------|---------|---------|
| `agio.events` | `topic` | yes | Main event bus |
| `agio.dlx` | `direct` | yes | Dead-letter exchange |
| `agio.retry` | `topic` | yes | Retry with TTL |

### Queue topology

| Queue | Bound to | Routing Key | Purpose |
|-------|----------|-------------|---------|
| `agio.investigation` | `agio.events` | `#` | Python consumer — all events |
| `agio.dead_letters` | `agio.dlx` | `#` | Undeliverable / max-retry messages |
| `agio.retry.60s` | `agio.retry` | `#` | Retry queue (60s TTL, re-publishes to `agio.events`) |

### Routing key convention

```
{entity}.{action}

Examples:
  order.created
  order.status_changed
  deposit.detected
  deposit.confirmed
  withdrawal.submitted
  kyc.approved
  risk_case.opened
  provider.circuit_opened
```

### Delivery guarantees

| Mechanism | Implementation |
|-----------|---------------|
| Publisher confirms | `channel->confirm_select()` + `wait_for_pending_acks()` before marking `published_at` |
| Durable queues | `durable: true` on all queues |
| Persistent messages | `delivery_mode: 2` on all published messages |
| Consumer ACK | Manual ACK only after event is safely stored in Python's DB |
| Retry on consumer failure | Re-queue to `agio.retry.60s`, max 3 attempts, then `agio.dead_letters` |
| Idempotency | `event_id` is the deduplication key — both publisher and consumer deduplicate |

---

## Part 4 — Event Contract

### Standard event envelope (broker-independent)

```json
{
  "event_id":       "evt_<uuid4>",
  "event_type":     "order.status_changed",
  "event_version":  "1.0",
  "occurred_at":    "2026-11-12T03:00:00.000Z",
  "producer":       "agio-paymentexchange",
  "correlation_id": "corr_<uuid4>",
  "causation_id":   "evt_<parent_event_id>",
  "entity": {
    "type": "order",
    "id":   "<order.uuid>"
  },
  "data": { }
}
```

**Additional fields added in v1.1:**
- `causation_id` — the `event_id` of the event that caused this one (e.g. a `deposit.confirmed` event causes `order.status_changed`). Null for user-initiated events.

**Rules:**
- All monetary amounts as **decimal strings** — never floats.
- All timestamps as **ISO 8601 UTC**.
- `event_id` is the idempotency key. Both publisher and consumer must deduplicate.
- Never include passwords, secrets, API keys, tokens, or OTPs.
- The envelope is broker-independent — the same schema works on RabbitMQ or EventBridge.

### Outbox write rule

> Write `OutboxEvent::create()` **inside the same `DB::transaction()`** as the business record it describes. Never write the outbox event after the transaction commits.

```php
DB::transaction(function () use ($order, $newStatus, $fromStatus) {
    // 1. Business record write
    $order->update(['status' => $newStatus]);
    OrderEvent::create([...]);

    // 2. Outbox event — SAME transaction
    OutboxEvent::create([
        'event_id'   => (string) Str::uuid(),
        'event_type' => 'order.status_changed',
        'payload'    => [
            'event_id'       => (string) Str::uuid(),
            'event_type'     => 'order.status_changed',
            'event_version'  => '1.0',
            'occurred_at'    => now()->toIso8601ZuluString(),
            'producer'       => 'agio-paymentexchange',
            'correlation_id' => (string) Str::uuid(),
            'entity'         => ['type' => 'order', 'id' => $order->uuid],
            'data'           => [
                'order_uuid'  => $order->uuid,
                'user_id'     => $order->user_id,
                'from_status' => $fromStatus,
                'to_status'   => $newStatus,
            ],
        ],
    ]);
});
```

### Event catalogue

#### Order events

| Event type | Trigger | Key payload fields |
|-----------|---------|-------------------|
| `order.created` | `OrderService::createFromQuote()` | `order_uuid, user_id, sell_amount, sell_currency, buy_amount, buy_currency, provider_id, status` |
| `order.status_changed` | `OrderService::transition()` — all 15+ transitions | `order_uuid, user_id, from_status, to_status, actor_type, actor_id, reason` |
| `order.completed` | `transition()` → `completed` | `order_uuid, user_id, sell_amount, buy_amount, rate, provider_id, completed_at` |
| `order.failed` | `transition()` → `failed` | `order_uuid, user_id, reason, provider_id` |
| `order.refunded` | `transition()` → `refunded` | `order_uuid, user_id, refund_amount, currency` |

#### Blockchain events

| Event type | Trigger | Key payload fields |
|-----------|---------|-------------------|
| `deposit.detected` | Via `OrderService::transition()` → `deposit_detected` | `order_uuid, tx_hash, network, currency, received_amount, expected_amount` |
| `deposit.confirmed` | Via `OrderService::transition()` → `deposit_confirmed` | `order_uuid, tx_hash, network, confirmed_at, confirmations` |
| `deposit.mismatch` | Via `OrderService::transition()` → `amount_mismatch` | `order_uuid, tx_hash, received_amount, expected_amount, difference` |
| `payout.verified` | Via `OrderService::transition()` → `payout_verified` | `order_uuid, tx_hash, network, payout_address, verified_at` |
| `payout.failed` | Via `OrderService::transition()` → `failed` (payout context) | `order_uuid, reason` |

#### Withdrawal events

| Event type | Trigger | Key payload fields |
|-----------|---------|-------------------|
| `withdrawal.submitted` | `WalletWithdrawalController` (after M17 tx wrap) | `withdrawal_id, user_id, amount, currency` |
| `withdrawal.approved` | `Admin\WithdrawRequestController::approve()` | `withdrawal_id, user_id, amount, currency, approved_by` |
| `withdrawal.rejected` | `Admin\WithdrawRequestController::reject()` | `withdrawal_id, user_id, reason` |
| `withdrawal.completed` | `Admin\WithdrawRequestController` → `completed` | `withdrawal_id, user_id, amount, tx_hash` |

#### Compliance & KYC events

| Event type | Trigger | Key payload fields |
|-----------|---------|-------------------|
| `kyc.approved` | `Admin\KycController::approve()` (after M17 tx wrap) | `user_id, document_type, reviewed_by` |
| `kyc.rejected` | `Admin\KycController::reject()` (after M17 tx wrap) | `user_id, document_type, rejection_reason` |
| `risk_case.opened` | `ScreeningService::openRiskCase()` | `case_uuid, user_id, order_id, reason, severity` |
| `risk_case.resolved` | Admin resolves case | `case_uuid, user_id, resolution_status, reviewer_id` |

#### Account events

| Event type | Trigger | Key payload fields |
|-----------|---------|-------------------|
| `user.registered` | `AuthController::register()` | `user_id, country_code` |
| `user.login.succeeded` | `AuthController::login()` | `user_id, ip_address, timestamp` |
| `user.login.failed` | `AuthController::login()` failure | `email_attempted, ip_address, failure_reason, timestamp` |

#### Provider & system events

| Event type | Trigger | Key payload fields |
|-----------|---------|-------------------|
| `provider.circuit_opened` | `LiquidityProvider::recordFailure()` | `provider_id, identifier, consecutive_failures` |
| `provider.circuit_closed` | Circuit reset | `provider_id, identifier` |
| `reconciliation_case.opened` | `SwapReconciliationService` | `case_uuid, order_uuid, case_type, severity` |

---

## Part 5 — Internal APIs (Laravel → Python Pull)

**Base path:** `/api/internal/investigation/v1/`  
**Authentication:** Sanctum personal access token with `service:internal` ability, issued via `php artisan investigation:issue-token`.  
**Middleware:** `auth.internal` — validates the token ability.  
**Method:** Read-only GET only. Python never POSTs to these endpoints.

> These endpoints do not exist yet. They are the M16 implementation target.

### Account intelligence

```
GET /users/{id}/profile
    → user fields: id, status, kyc_status, country, email_verified_at,
      two_factor_enabled_at, account_type, created_at
    → (never: password, tokens, secrets)

GET /users/{id}/activity
    → user_sessions: ip_address, device, created_at, revoked_at
    → login_attempts (M17): ip_address, failure_reason, attempted_at
    → activity_log entries for this user

GET /users/{id}/risk-summary
    → risk_cases linked to this user
    → screening_results for this user
    → orders.risk_status history
```

### Transaction evidence

```
GET /orders/{uuid}
    → full order record + quote snapshot

GET /orders/{uuid}/timeline
    → order_events array (all transitions, append-only)

GET /orders/{uuid}/blockchain
    → blockchain_deposits + blockchain_payouts linked to this order

GET /orders/{uuid}/provider
    → provider_executions + provider_quotes for this order

GET /orders/{uuid}/ledger
    → journal_entries where reference_type='order' AND reference_id=order.id
    → ledger_postings for each entry

GET /users/{id}/orders
    → paginated order list
    → params: status, date_from, date_to, currency
```

### Financial verification

```
GET /users/{id}/wallets
    → wallet balances per currency (operational view)

GET /users/{id}/ledger
    → all ledger accounts owned by this user
    → computed balances (from LedgerAccount::computeBalance())

GET /ledger/journal/{uuid}
    → journal entry + all postings (full double-entry record)
```

### Deposits & withdrawals

```
GET /deposits/{id}
    → deposit_request record

GET /withdrawals/{id}
    → withdraw_request record

GET /blockchain/deposits/{id}
    → blockchain_deposit with order context
```

### Compliance & risk

```
GET /risk-cases/{uuid}
    → risk_case + all screening_results linked to it

GET /users/{id}/compliance
    → all risk_cases + jurisdiction rule evaluation for this user

GET /orders/{uuid}/reconciliation
    → reconciliation_cases + recovery_actions linked to this order
```

### Provider intelligence

```
GET /providers/{identifier}
    → liquidity_provider: circuit state, risk_rating, on_time_rate

GET /providers/{identifier}/executions
    → paginated provider_executions
    → params: status, date_from, date_to
```

---

## Part 6 — Data the Python Service Must NOT Receive

| Category | Examples |
|----------|---------|
| Credentials | `password`, `remember_token`, API keys, webhook secrets |
| Authentication material | Sanctum tokens, OTP codes, 2FA secrets (`secret`, `recovery_codes`) |
| Provider credentials | `provider_accounts.credentials_ref` and what it references |
| Private keys | Tatum API key, wallet private keys |
| Production DB dumps | Never export raw DB to Python |
| Excessive PII combination | Full name + exact DOB + national ID number in one payload |

---

## Part 7 — Python Service Architecture Constraints

1. **Decimal strings, never floats.** Use Python `Decimal` for all financial arithmetic.
2. **Order UUIDs are the external reference.** Integer `order.id` is internal to Laravel. Always use `uuid`.
3. **`order_events` is the timeline.** Reconstruct order history from `order_events` ordered by `created_at`. Never infer history from `status` alone.
4. **Events may arrive out of order and may be duplicated.** Deduplicate on `event_id` before processing. Store `received_at` separately from `occurred_at`.
5. **Ledger balances are computed, not stored.** Call the internal API; never sum postings independently.
6. **Blockchain confirmation state is managed by Laravel.** Python consumes `deposit.confirmed` events; it does not independently track confirmations.
7. **Provider identifiers are strings.** Use `liquidity_providers.identifier` (`"changenow"`) as the cross-system reference, not integer `id`.
8. **`risk_status` (on order) and `severity` (on risk case) are separate.** An order can be `risk_status=flagged` with no associated `risk_case`.
9. **Stub screening results are not AML clearances.** `screening_results.result = 'clear'` from `internal_stub` provider has no AML significance. Flag it in findings metadata.
10. **`received_at` ≠ `occurred_at`.** Record both: `occurred_at` from the event envelope (when it happened in Laravel), `received_at` from the Python consumer (when Python ingested it).

---

## Part 8 — Implementation Sequence

```
M15 — Transactional Outbox + RabbitMQ Publisher
├── composer require php-amqplib/php-amqplib
├── Add RabbitMQ service to docker-compose.yml
├── Create config/rabbitmq.php
├── Add RABBITMQ_* vars to .env.example
├── Wire OutboxEvent::create() into:
│     OrderService::createFromQuote()          ← safe (already in tx)
│     OrderService::transition()               ← safe (already in tx)
│     ScreeningService::openRiskCase()         ← safe (already in tx)
│     Admin\WithdrawRequestController          ← safe (already in tx)
├── Rewrite PublishOutboxEvents:
│     SELECT FOR UPDATE SKIP LOCKED (race-safe)
│     RabbitMQ publisher confirms
│     Mark published_at ONLY after broker ACK
├── Schedule PublishOutboxEvents every minute
│     with withoutOverlapping(1)
└── Add outbox event tests

M16 — Internal Investigation API + Service Auth
├── Create VerifyInternalServiceToken middleware
├── Create investigation:issue-token Artisan command
├── Add /api/internal/investigation/v1/ route group
├── Build controllers for all endpoints in §Part 5
└── Create lean InternalAPI resource classes

M17 — Missing Data Collection
├── Migration: orders.created_from_ip
├── Migration: blockchain_deposits.from_address + block_number
├── Migration: blockchain_payouts.block_number
├── Migration: provider_executions.failure_reason
├── Migration: users.phone_verified_at + account_type
├── Migration: create login_attempts table
├── Wire created_from_ip into OrderController::store()
├── Wire from_address + block_number into RecordBlockchainDeposit job
├── Wrap KycController approve/reject in DB::transaction()
├── Wrap WalletWithdrawalController in DB::transaction()
├── Add OutboxEvent writes to KycController (after tx wrap)
├── Add OutboxEvent writes to WalletWithdrawalController (after tx wrap)
└── Add OutboxEvent writes to AuthController (best-effort login events)

M19+ — Python Service
  (Build only after M15–M17 are complete and events are flowing)
```

---

*Version 1.1 — Generated from verified codebase analysis (agio-m15-gap-report.md). All field names, table names, and status values verified against live migrations and model files. Update this document when schema changes.*
