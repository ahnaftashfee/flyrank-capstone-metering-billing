from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

import stripe
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ServiceError
from app.models import StripeEvent, Subscription, Tenant
from app.repositories import SubscriptionRepository, TenantRepository


class BillingProvider(Protocol):
    def create_checkout(self, tenant_id: str) -> dict[str, str]: ...

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]: ...

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]: ...


class StripeBillingProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def _client(self) -> stripe.StripeClient:
        if not self.settings.stripe_secret_key.startswith("sk_test_"):
            raise ServiceError(
                503,
                "Stripe test-mode credentials are not configured",
                "stripe_not_configured",
            )
        return stripe.StripeClient(
            self.settings.stripe_secret_key,
            max_network_retries=1,
        )

    def create_checkout(self, tenant_id: str) -> dict[str, str]:
        if not self.settings.stripe_pro_price_id.startswith("price_"):
            raise ServiceError(
                503,
                "Stripe Pro price is not configured",
                "stripe_not_configured",
            )
        session = self._client().v1.checkout.sessions.create(
            {
                "mode": "subscription",
                "line_items": [
                    {"price": self.settings.stripe_pro_price_id, "quantity": 1}
                ],
                "client_reference_id": tenant_id,
                "metadata": {"tenant_id": tenant_id},
                "subscription_data": {"metadata": {"tenant_id": tenant_id}},
                "success_url": (
                    f"{self.settings.app_base_url}/billing/success"
                    "?session_id={CHECKOUT_SESSION_ID}"
                ),
                "cancel_url": f"{self.settings.app_base_url}/billing/cancel",
            }
        )
        if not session.url:
            raise ServiceError(
                502,
                "Stripe did not return a Checkout URL",
                "stripe_checkout_failed",
            )
        return {"session_id": session.id, "checkout_url": session.url}

    def verify_webhook(self, payload: bytes, signature: str) -> dict[str, Any]:
        if not self.settings.stripe_webhook_secret.startswith("whsec_"):
            raise ServiceError(
                503,
                "Stripe webhook secret is not configured",
                "stripe_not_configured",
            )
        try:
            event = stripe.Webhook.construct_event(
                payload,
                signature,
                self.settings.stripe_webhook_secret,
            )
        except (ValueError, stripe.SignatureVerificationError) as error:
            raise ServiceError(
                400,
                "Invalid Stripe webhook signature",
                "invalid_webhook_signature",
            ) from error
        return self._plain_dict(event)

    def retrieve_subscription(self, subscription_id: str) -> dict[str, Any]:
        subscription = self._client().v1.subscriptions.retrieve(subscription_id)
        return self._plain_dict(subscription)

    @classmethod
    def _plain_dict(cls, value: Any) -> Any:
        """Convert Stripe mapping objects to regular Python containers."""
        if isinstance(value, Mapping):
            return {str(key): cls._plain_dict(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._plain_dict(item) for item in value]
        return value


class BillingService:
    HANDLED_EVENTS = {
        "checkout.session.completed",
        "customer.subscription.updated",
        "customer.subscription.deleted",
    }

    def __init__(self, database: Session) -> None:
        self.database = database
        self.tenants = TenantRepository(database)
        self.subscriptions = SubscriptionRepository(database)

    def process_event(self, event: dict[str, Any]) -> tuple[str, str]:
        event_id = str(event.get("id", ""))
        event_type = str(event.get("type", ""))
        if not event_id or not event_type:
            raise ServiceError(400, "Malformed Stripe event", "invalid_webhook")
        if event.get("livemode") is True:
            raise ServiceError(
                400,
                "Live-mode Stripe events are not accepted",
                "live_mode_rejected",
            )
        if self.database.get(StripeEvent, event_id) is not None:
            return "duplicate", event_id

        self.database.add(StripeEvent(event_id=event_id, event_type=event_type))
        try:
            self.database.flush()
        except IntegrityError:
            self.database.rollback()
            return "duplicate", event_id

        if event_type in self.HANDLED_EVENTS:
            stripe_object = event.get("data", {}).get("object", {})
            if not isinstance(stripe_object, dict):
                raise ServiceError(400, "Malformed Stripe event", "invalid_webhook")
            if event_type == "checkout.session.completed":
                self._apply_checkout(stripe_object)
            else:
                self.sync_subscription(stripe_object, deleted=event_type.endswith("deleted"))

        self.database.commit()
        return ("processed" if event_type in self.HANDLED_EVENTS else "ignored"), event_id

    def _apply_checkout(self, session: dict[str, Any]) -> None:
        tenant_id = self._tenant_id_from(session)
        tenant = self.tenants.get_with_plan(tenant_id, lock=True) if tenant_id else None
        if tenant is None:
            raise ServiceError(
                400,
                "Stripe event does not map to a known tenant",
                "unknown_tenant",
            )

        subscription_id = self._object_id(session.get("subscription"))
        customer_id = self._object_id(session.get("customer"))
        subscription = self.subscriptions.find_by_tenant(tenant.id)
        if subscription is None:
            subscription = Subscription(tenant_id=tenant.id)
            self.database.add(subscription)
        subscription.stripe_subscription_id = subscription_id
        subscription.stripe_customer_id = customer_id
        subscription.status = "active"
        tenant.plan_slug = "pro"
        tenant.subscription_status = "active"

    def sync_subscription(
        self,
        stripe_subscription: dict[str, Any],
        *,
        deleted: bool = False,
    ) -> None:
        subscription_id = str(stripe_subscription.get("id", ""))
        tenant_id = self._tenant_id_from(stripe_subscription)
        existing = (
            self.subscriptions.find_by_stripe_id(subscription_id)
            if subscription_id
            else None
        )
        if existing is not None:
            tenant_id = existing.tenant_id
        tenant = self.tenants.get_with_plan(tenant_id, lock=True) if tenant_id else None
        if tenant is None:
            raise ServiceError(
                400,
                "Stripe subscription does not map to a known tenant",
                "unknown_tenant",
            )

        subscription = existing or self.subscriptions.find_by_tenant(tenant.id)
        if subscription is None:
            subscription = Subscription(tenant_id=tenant.id)
            self.database.add(subscription)

        status = "canceled" if deleted else str(
            stripe_subscription.get("status", "inactive")
        )
        subscription.stripe_subscription_id = subscription_id or (
            subscription.stripe_subscription_id
        )
        subscription.stripe_customer_id = self._object_id(
            stripe_subscription.get("customer")
        ) or subscription.stripe_customer_id
        subscription.status = status
        subscription.current_period_end = self._period_end(stripe_subscription)
        tenant.subscription_status = status
        tenant.plan_slug = "pro" if status in {"active", "trialing", "past_due"} else "free"

    @staticmethod
    def _tenant_id_from(value: dict[str, Any]) -> str:
        metadata = value.get("metadata") or {}
        if isinstance(metadata, dict) and metadata.get("tenant_id"):
            return str(metadata["tenant_id"])
        return str(value.get("client_reference_id") or "")

    @staticmethod
    def _object_id(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict) and value.get("id"):
            return str(value["id"])
        return None

    @staticmethod
    def _period_end(value: dict[str, Any]) -> datetime | None:
        timestamp = value.get("current_period_end")
        if isinstance(timestamp, int):
            return datetime.fromtimestamp(timestamp, UTC)
        return None
