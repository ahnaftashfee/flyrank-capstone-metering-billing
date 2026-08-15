# Verification Evidence

All output below was produced locally on 2026-08-15. Dynamic UUIDs are shown as
generated; no screenshots or responses were fabricated.

## Automated tests

Command:

```bash
.venv/bin/pytest -q
```

Result:

```text
.................                                                        [100%]
17 passed, 1 warning in 0.31s
```

The warning is a dependency deprecation notice emitted by FastAPI's TestClient;
it is not an application failure.

## Docker Compose and PostgreSQL

`docker compose up --build -d` built the Python 3.12 image, applied Alembic
revision `0001`, started PostgreSQL 17, and reported both services healthy.

The demo seed reported:

```text
Seeded tenant 00000000-0000-0000-0000-000000000001 at 998/1000 API calls
```

## Idempotency and quota boundary

The first request using key `demo-999` returned `201`, event ID
`d03e8477-ca40-4a6e-a3ac-0bda98c594e9`, and:

```text
idempotent-replayed: false
api_calls_used: 999
```

An exact retry returned the same event ID and response with:

```text
idempotent-replayed: true
api_calls_used: 999
```

A new request using `demo-1000` returned `201` and reached exactly 1,000. The
next new key returned:

```http
HTTP/1.1 429 Too Many Requests
retry-after: 3600

{"error":{"code":"api_quota_exceeded","message":"Monthly API call quota exceeded"}}
```

## Persistence proof

After `docker compose restart` restarted both the app and database, `/usage`
still returned:

```json
{
  "tenant_id": "00000000-0000-0000-0000-000000000001",
  "period": "2026-08-01",
  "plan": "free",
  "subscription_status": "active",
  "api_calls": {"used": 1000, "limit": 1000, "remaining": 0},
  "ai_tokens": {"used": 30, "limit": 100000, "remaining": 99970},
  "cost_microcents": 2008438,
  "cost_cents": "2.008438"
}
```

Direct PostgreSQL output after restart contained one 998-call seed row plus the
two distinct one-call events. The retry did not create a fourth row.

## Background job

`POST /admin/jobs/reconcile` returned `202`. Its status endpoint then returned:

```json
{
  "job_id": "091db7ae-647e-4249-a4e8-1c65154bc75a",
  "job_type": "stripe_subscription_reconciliation",
  "status": "succeeded",
  "attempts": 1,
  "error_message": null
}
```

The failure path is deterministic in the automated test suite: a simulated
Stripe outage is attempted three times and saved as a failed job with its final
error message.

## Stripe boundary

Automated tests create a Checkout response through the provider boundary and
exercise the official Stripe SDK's real signature algorithm with a signed raw
payload. A forged signature returns `400`; replaying a verified event returns
`duplicate`; a live-mode event is rejected. Network Checkout requires the
reviewer's own Stripe sandbox credentials, so no secret or account-specific
artifact is committed.
