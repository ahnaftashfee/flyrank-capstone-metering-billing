import hashlib
import hmac
import json
import time

import pytest
from sqlalchemy import func, select

from app.billing import BillingService, StripeBillingProvider
from app.config import Settings
from app.errors import ServiceError
from app.jobs import enqueue_reconciliation, run_reconciliation
from app.models import JobRun, StripeEvent, Subscription, Tenant
from tests.conftest import TENANT_A_ID, TENANT_A_KEY


def stripe_event(event_id: str = "evt_checkout") -> dict:
    return {
        "id": event_id,
        "type": "checkout.session.completed",
        "livemode": False,
        "data": {
            "object": {
                "id": "cs_test_capstone",
                "client_reference_id": TENANT_A_ID,
                "metadata": {"tenant_id": TENANT_A_ID},
                "customer": "cus_test_capstone",
                "subscription": "sub_test_capstone",
            }
        },
    }


def test_checkout_returns_test_session_for_authenticated_tenant(client) -> None:
    response = client.post(
        "/billing/checkout",
        headers={"X-API-Key": TENANT_A_KEY},
    )
    assert response.status_code == 200
    assert response.json()["session_id"].startswith("cs_test_")
    assert response.json()["checkout_url"].startswith("https://")


def test_checkout_webhook_flips_plan_and_duplicate_is_ignored(
    client, session_factory
) -> None:
    payload = stripe_event()
    first = client.post(
        "/webhooks/stripe",
        headers={"Stripe-Signature": "valid-signature"},
        content=json.dumps(payload),
    )
    second = client.post(
        "/webhooks/stripe",
        headers={"Stripe-Signature": "valid-signature"},
        content=json.dumps(payload),
    )

    assert first.status_code == 200
    assert first.json()["status"] == "processed"
    assert second.status_code == 200
    assert second.json()["status"] == "duplicate"
    with session_factory() as database:
        tenant = database.get(Tenant, TENANT_A_ID)
        assert tenant.plan_slug == "pro"
        assert tenant.subscription_status == "active"
        assert database.scalar(select(func.count()).select_from(StripeEvent)) == 1


def test_forged_webhook_is_400_and_changes_nothing(client, session_factory) -> None:
    response = client.post(
        "/webhooks/stripe",
        headers={"Stripe-Signature": "forged"},
        content=json.dumps(stripe_event("evt_forged")),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_webhook_signature"
    with session_factory() as database:
        tenant = database.get(Tenant, TENANT_A_ID)
        assert tenant.plan_slug == "free"
        assert database.scalar(select(func.count()).select_from(StripeEvent)) == 0


def test_live_mode_webhook_is_rejected(client, session_factory) -> None:
    payload = stripe_event("evt_live")
    payload["livemode"] = True
    response = client.post(
        "/webhooks/stripe",
        headers={"Stripe-Signature": "valid-signature"},
        content=json.dumps(payload),
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "live_mode_rejected"
    with session_factory() as database:
        assert database.scalar(select(func.count()).select_from(StripeEvent)) == 0


def test_deleted_subscription_returns_tenant_to_free(session_factory) -> None:
    checkout = stripe_event("evt_create")
    deleted = {
        "id": "evt_delete",
        "type": "customer.subscription.deleted",
        "livemode": False,
        "data": {
            "object": {
                "id": "sub_test_capstone",
                "customer": "cus_test_capstone",
                "status": "canceled",
                "metadata": {"tenant_id": TENANT_A_ID},
            }
        },
    }
    with session_factory() as database:
        service = BillingService(database)
        assert service.process_event(checkout)[0] == "processed"
        assert service.process_event(deleted)[0] == "processed"
        tenant = database.get(Tenant, TENANT_A_ID)
        assert tenant.plan_slug == "free"
        assert tenant.subscription_status == "canceled"


def test_real_stripe_signature_verifier_accepts_valid_and_rejects_forged() -> None:
    secret = "whsec_capstone_test_secret"
    payload = json.dumps(stripe_event()).encode("utf-8")
    timestamp = int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    provider = StripeBillingProvider(
        Settings(stripe_webhook_secret=secret)
    )

    verified = provider.verify_webhook(payload, f"t={timestamp},v1={digest}")
    assert verified["id"] == "evt_checkout"
    with pytest.raises(ServiceError) as error:
        provider.verify_webhook(payload, f"t={timestamp},v1=forged")
    assert error.value.status_code == 400


class AlwaysFailProvider:
    def retrieve_subscription(self, _subscription_id: str) -> dict:
        raise RuntimeError("simulated Stripe outage")


class SuccessfulProvider:
    def retrieve_subscription(self, subscription_id: str) -> dict:
        return {
            "id": subscription_id,
            "customer": "cus_job",
            "status": "active",
            "metadata": {"tenant_id": TENANT_A_ID},
        }


def test_background_reconciliation_retries_and_records_failure(
    session_factory,
) -> None:
    with session_factory() as database:
        database.add(
            Subscription(
                tenant_id=TENANT_A_ID,
                stripe_customer_id="cus_job",
                stripe_subscription_id="sub_job",
                status="active",
            )
        )
        database.commit()
        job = enqueue_reconciliation(database)

    run_reconciliation(
        job.id,
        AlwaysFailProvider(),
        session_factory,
        max_attempts=3,
        retry_delay_seconds=0,
        sleep=lambda _seconds: None,
    )

    with session_factory() as database:
        result = database.get(JobRun, job.id)
        assert result.status == "failed"
        assert result.attempts == 3
        assert result.error_message == "simulated Stripe outage"


def test_background_reconciliation_records_success(session_factory) -> None:
    with session_factory() as database:
        database.add(
            Subscription(
                tenant_id=TENANT_A_ID,
                stripe_customer_id="cus_job",
                stripe_subscription_id="sub_job",
                status="inactive",
            )
        )
        database.commit()
        job = enqueue_reconciliation(database)

    run_reconciliation(
        job.id,
        SuccessfulProvider(),
        session_factory,
        retry_delay_seconds=0,
        sleep=lambda _seconds: None,
    )

    with session_factory() as database:
        result = database.get(JobRun, job.id)
        tenant = database.get(Tenant, TENANT_A_ID)
        assert result.status == "succeeded"
        assert result.attempts == 1
        assert result.error_message is None
        assert tenant.plan_slug == "pro"
