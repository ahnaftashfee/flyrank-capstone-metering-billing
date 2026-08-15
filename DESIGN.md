# Design: Usage Metering and Billing Engine

## Problem

A multi-tenant SaaS needs one trustworthy answer for usage, quota access, and cost. Network retries must not create duplicate usage, quota checks must be exact at the monthly boundary, and subscription state must change only after a verified Stripe event.

## Data model

- `plans`: the Free and Pro quota definitions.
- `tenants`: customer organizations, API-key hashes, current plan, and subscription status.
- `subscriptions`: the tenant-to-Stripe customer/subscription mapping.
- `usage_events`: immutable billable facts with a per-tenant idempotency key, request hash, integer cost, and monthly partition key.
- `stripe_events`: processed Stripe event IDs for webhook deduplication.
- `job_runs`: reconciliation job status, attempts, and final failure message.

Every customer-owned row carries a tenant ID. The tenant is resolved from an API key at the HTTP boundary; callers never supply a tenant ID for metering or usage reads.

## API surface

- `POST /generate`: authorize tenant, validate token categories, enforce quota, and create one idempotent usage event.
- `GET /usage`: return the authenticated tenant's current-month used, limit, and integer cost totals.
- `POST /billing/checkout`: create a Stripe test-mode Pro subscription Checkout Session.
- `POST /webhooks/stripe`: verify the signature, deduplicate the event, and synchronize plan/status.
- `POST /admin/jobs/reconcile`: enqueue subscription reconciliation outside the request path.
- `GET /admin/jobs/{job_id}`: inspect completion, attempts, or a final failure alert.

## Idempotency strategy

`usage_events` has a database unique constraint on `(tenant_id, idempotency_key)`. The service hashes the normalized request body. A retry with the same key and hash returns the stored response; the same key with a different body returns `409`. The quota check and insert run in one transaction while locking the tenant row in PostgreSQL.

Stripe webhook IDs are also primary keys. A verified replay returns success without applying the plan change twice.

## Layer sketch

```text
HTTP / authorization / validation
        |                  |
MeteringService       BillingService       ReconciliationJob
        |                  |                      |
PricingPolicy         StripeProvider        StripeProvider
        |                  |                      |
SQLAlchemy repositories and transaction boundary
        |
PostgreSQL (SQLite only for deterministic tests)
```

## Explicit non-goal

The core does not generate invoices, bill overages, calculate proration, or call an AI model. Token counts are simulated inputs; Stripe remains the subscription source of truth in test mode only.
