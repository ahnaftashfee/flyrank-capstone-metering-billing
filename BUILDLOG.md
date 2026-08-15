# Build Log

## 2026-08-15 — design and schema

- Converted the assignment brief into a one-page design before implementation.
- Selected FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Stripe's Python SDK, and
  pytest.
- Modeled plans, tenants, subscriptions, immutable usage events, processed
  Stripe events, and background job runs.
- Added tenant-period lookup indexing and database uniqueness for exactly-once
  usage.

## 2026-08-15 — metering and pricing

- Added API-key authentication with HMAC-hashed keys.
- Implemented request-body hashing, stored-response replay, and `409` when an
  idempotency key is reused with different input.
- Enforced quotas while holding a tenant row lock in the same transaction as
  the usage insert.
- Kept money as integer microcents. Corrected the initial pricing sketch so
  cached tokens are removed from normal input and reasoning tokens are not
  added on top of output.

## 2026-08-15 — Stripe and background work

- Added test-mode Checkout Session creation through a provider protocol.
- Verified webhook signatures against the unmodified raw body before parsing.
- Added event-ID deduplication, plan upgrades, status updates, and cancellation
  downgrade behavior.
- Added an authenticated reconciliation job with persisted state, three retry
  attempts, increasing delay, and a final error alert.

## 2026-08-15 — validation and correction

- Built deterministic SQLite tests for business rules and Stripe boundaries.
- Replaced a deprecated Stripe object conversion helper with a recursive
  conversion over the SDK's mapping interface.
- The first real PostgreSQL request exposed a lock error: eager loading produced
  an outer join, and PostgreSQL would not apply `FOR UPDATE` to its nullable
  side. Corrected the query to `FOR UPDATE OF tenants`, rebuilt, and reran the
  full acceptance flow.
- Verified 17 automated tests, Docker health checks, idempotent retry, exact
  quota boundary, `429`, usage rollup, background success, and persistence after
  restarting both containers.

## Assistance disclosure

The project was developed with AI coding assistance for requirement extraction,
implementation, review, and documentation. Runtime claims in `EVIDENCE.md` were
copied from actual local commands. No production keys, live payments, or
fabricated screenshots are included.

Before submission, the repository history was grouped into the same build
phases listed above after the GitHub requirements were reviewed again. The
commits were created on 2026-08-15 and were not backdated.
