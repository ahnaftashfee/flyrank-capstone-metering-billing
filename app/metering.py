import hashlib
import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import ServiceError
from app.models import Tenant, UsageEvent, new_id
from app.pricing import calculate_cost_microcents, display_cents
from app.repositories import MeteringRepository, TenantRepository
from app.schemas import GenerateRequest


def current_period_start(now: datetime | None = None) -> date:
    timestamp = now or datetime.now(UTC)
    return date(timestamp.year, timestamp.month, 1)


def request_hash(payload: GenerateRequest) -> str:
    normalized = json.dumps(
        payload.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class MeteringService:
    def __init__(self, database: Session) -> None:
        self.database = database
        self.tenants = TenantRepository(database)
        self.events = MeteringRepository(database)

    def record(
        self,
        tenant_id: str,
        idempotency_key: str,
        payload: GenerateRequest,
    ) -> tuple[dict[str, Any], bool]:
        payload_hash = request_hash(payload)
        duplicate = self.events.find_by_idempotency_key(
            tenant_id, idempotency_key
        )
        if duplicate is not None:
            return self._replay(duplicate, payload_hash), True

        tenant = self.tenants.get_with_plan(tenant_id, lock=True)
        if tenant is None:
            raise ServiceError(401, "Invalid tenant API key", "invalid_api_key")

        duplicate = self.events.find_by_idempotency_key(
            tenant_id, idempotency_key
        )
        if duplicate is not None:
            return self._replay(duplicate, payload_hash), True

        if tenant.subscription_status in {"past_due", "unpaid", "incomplete"}:
            raise ServiceError(
                402,
                "Subscription payment is required before more usage is allowed",
                "payment_required",
            )

        period_start = current_period_start()
        totals = self.events.totals(tenant.id, period_start)
        requested_api_calls = 1
        requested_ai_tokens = payload.input_tokens + payload.output_tokens
        next_api_calls = totals.api_calls + requested_api_calls
        next_ai_tokens = totals.ai_tokens + requested_ai_tokens

        self._enforce_quota(
            tenant,
            next_api_calls=next_api_calls,
            next_ai_tokens=next_ai_tokens,
        )

        event_cost = calculate_cost_microcents(
            api_calls=requested_api_calls,
            input_tokens=payload.input_tokens,
            cached_input_tokens=payload.cached_input_tokens,
            output_tokens=payload.output_tokens,
            reasoning_tokens=payload.reasoning_tokens,
        )
        event_id = new_id()
        response_payload = {
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "plan": tenant.plan_slug,
            "api_calls_used": next_api_calls,
            "api_calls_limit": tenant.plan.api_call_limit,
            "ai_tokens_used": next_ai_tokens,
            "ai_tokens_limit": tenant.plan.ai_token_limit,
            "event_cost_microcents": event_cost,
            "message": "Usage recorded",
        }
        event = UsageEvent(
            id=event_id,
            tenant_id=tenant.id,
            idempotency_key=idempotency_key,
            request_hash=payload_hash,
            usage_type="generate",
            api_calls=requested_api_calls,
            input_tokens=payload.input_tokens,
            cached_input_tokens=payload.cached_input_tokens,
            output_tokens=payload.output_tokens,
            reasoning_tokens=payload.reasoning_tokens,
            ai_tokens=requested_ai_tokens,
            cost_microcents=event_cost,
            response_payload=response_payload,
            period_start=period_start,
        )
        self.events.add(event)

        try:
            self.database.commit()
        except IntegrityError:
            self.database.rollback()
            concurrent_duplicate = self.events.find_by_idempotency_key(
                tenant_id, idempotency_key
            )
            if concurrent_duplicate is None:
                raise
            return self._replay(concurrent_duplicate, payload_hash), True

        return response_payload, False

    def usage(self, tenant_id: str) -> dict[str, Any]:
        tenant = self.tenants.get_with_plan(tenant_id)
        if tenant is None:
            raise ServiceError(401, "Invalid tenant API key", "invalid_api_key")
        period_start = current_period_start()
        totals = self.events.totals(tenant.id, period_start)
        return {
            "tenant_id": tenant.id,
            "period": period_start.isoformat(),
            "plan": tenant.plan_slug,
            "subscription_status": tenant.subscription_status,
            "api_calls": {
                "used": totals.api_calls,
                "limit": tenant.plan.api_call_limit,
                "remaining": max(tenant.plan.api_call_limit - totals.api_calls, 0),
            },
            "ai_tokens": {
                "used": totals.ai_tokens,
                "limit": tenant.plan.ai_token_limit,
                "remaining": max(tenant.plan.ai_token_limit - totals.ai_tokens, 0),
            },
            "cost_microcents": totals.cost_microcents,
            "cost_cents": display_cents(totals.cost_microcents),
        }

    @staticmethod
    def _replay(event: UsageEvent, payload_hash: str) -> dict[str, Any]:
        if event.request_hash != payload_hash:
            raise ServiceError(
                409,
                "Idempotency key was already used with a different request",
                "idempotency_conflict",
            )
        return dict(event.response_payload)

    @staticmethod
    def _enforce_quota(
        tenant: Tenant,
        *,
        next_api_calls: int,
        next_ai_tokens: int,
    ) -> None:
        if next_api_calls > tenant.plan.api_call_limit:
            raise ServiceError(
                429,
                "Monthly API call quota exceeded",
                "api_quota_exceeded",
                headers={"Retry-After": "3600"},
            )
        if next_ai_tokens > tenant.plan.ai_token_limit:
            raise ServiceError(
                429,
                "Monthly AI token quota exceeded",
                "token_quota_exceeded",
                headers={"Retry-After": "3600"},
            )
