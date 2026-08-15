# Usage metering and billing

FlyRank capstone project. The service records tenant usage, applies monthly
limits, calculates request cost, and updates Free/Pro access from Stripe test
webhooks.

Stack: FastAPI, PostgreSQL, SQLAlchemy, Alembic, Stripe SDK, Docker Compose.

## Setup

Docker Desktop needs to be running.

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec app python -m app.seed
curl http://localhost:8000/health
```

Swagger: <http://localhost:8000/docs>

The seed creates a Free tenant with 998 of 1,000 calls already used. Its local
API key is `demo-tenant-key-change-me`.

```bash
curl -i -X POST http://localhost:8000/generate \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: demo-tenant-key-change-me' \
  -H 'Idempotency-Key: demo-999' \
  -d '{"input_tokens":20,"cached_input_tokens":5,"output_tokens":10,"reasoning_tokens":2}'
```

Run that twice to check retry handling. The second response returns the same
event with `Idempotent-Replayed: true`; usage stays at 999. A new key reaches
1,000 and the key after that gets `429`.

Current usage:

```bash
curl http://localhost:8000/usage \
  -H 'X-API-Key: demo-tenant-key-change-me'
```

Stop without removing Postgres data:

```bash
docker compose down
```

## Routes

```text
GET  /health
POST /generate                 X-API-Key + Idempotency-Key
GET  /usage                    X-API-Key
POST /billing/checkout         X-API-Key
POST /webhooks/stripe          Stripe-Signature
POST /admin/jobs/reconcile     X-Admin-Key
GET  /admin/jobs/{job_id}      X-Admin-Key
```

## Rules

- Free: 1,000 calls and 100,000 AI tokens per month.
- Pro: 100,000 calls and 10,000,000 AI tokens per month.
- Reaching the exact limit is allowed. The next request is rejected.
- The same idempotency key and body returns the stored response.
- The same idempotency key with a different body returns `409`.
- `past_due`, `unpaid`, and `incomplete` subscriptions return `402`.
- All money calculations use integer microcents.

Cached tokens are part of `input_tokens`, and reasoning tokens are part of
`output_tokens`. Those categories are saved separately but are not added to the
totals twice. Project rates are pinned in `app/pricing.py`.

## Stripe test setup

Create a recurring Pro price in a Stripe sandbox and add these values to the
local `.env`:

```dotenv
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRO_PRICE_ID=price_...
```

Forward sandbox webhooks while testing Checkout:

```bash
stripe listen --forward-to localhost:8000/webhooks/stripe
```

The app reads the raw webhook body, verifies the signature, and saves the event
ID before changing subscription state. Replayed events are acknowledged without
running the update again. Live keys and live-mode events are rejected.

## Notes on the implementation

- Tenant identity comes from the API key, not a tenant ID in the request.
- API keys are stored as HMAC hashes.
- `(tenant_id, idempotency_key)` is unique in PostgreSQL.
- The quota check and insert share a transaction and tenant row lock.
- Usage lookup is indexed by tenant and billing period.
- The reconciliation job retries three times and records its final status.
- `.env` is ignored; `.env.example` only contains placeholders.

The schema is in `alembic/versions/0001_initial_schema.py`. More detail is in
[DESIGN.md](DESIGN.md), and the actual local run is recorded in
[EVIDENCE.md](EVIDENCE.md).

## Tests

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pytest -q
```

Or:

```bash
docker compose run --rm app pytest -q
```

The suite currently has 17 tests. It covers tenant isolation, retry behavior,
quota boundaries, pricing, signed and forged webhooks, event replay,
subscription cancellation, and reconciliation retries.
