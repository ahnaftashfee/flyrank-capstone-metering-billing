from dataclasses import dataclass
from datetime import date

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, joinedload

from app.models import JobRun, Subscription, Tenant, UsageEvent


@dataclass(frozen=True)
class UsageTotals:
    api_calls: int
    ai_tokens: int
    cost_microcents: int


class TenantRepository:
    def __init__(self, database: Session) -> None:
        self.database = database

    def find_by_api_key_hash(self, api_key_hash: str) -> Tenant | None:
        return self.database.scalar(
            select(Tenant)
            .options(joinedload(Tenant.plan))
            .where(Tenant.api_key_hash == api_key_hash)
        )

    def get_with_plan(self, tenant_id: str, *, lock: bool = False) -> Tenant | None:
        statement: Select[tuple[Tenant]] = (
            select(Tenant)
            .options(joinedload(Tenant.plan))
            .where(Tenant.id == tenant_id)
        )
        if lock:
            # Lock only the tenant row. PostgreSQL rejects a blanket FOR UPDATE
            # when joinedload adds the plan as the nullable side of an outer join.
            statement = statement.with_for_update(of=Tenant)
        return self.database.scalar(statement)


class MeteringRepository:
    def __init__(self, database: Session) -> None:
        self.database = database

    def find_by_idempotency_key(
        self, tenant_id: str, idempotency_key: str
    ) -> UsageEvent | None:
        return self.database.scalar(
            select(UsageEvent).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.idempotency_key == idempotency_key,
            )
        )

    def totals(self, tenant_id: str, period_start: date) -> UsageTotals:
        row = self.database.execute(
            select(
                func.coalesce(func.sum(UsageEvent.api_calls), 0),
                func.coalesce(func.sum(UsageEvent.ai_tokens), 0),
                func.coalesce(func.sum(UsageEvent.cost_microcents), 0),
            ).where(
                UsageEvent.tenant_id == tenant_id,
                UsageEvent.period_start == period_start,
            )
        ).one()
        return UsageTotals(int(row[0]), int(row[1]), int(row[2]))

    def add(self, event: UsageEvent) -> None:
        self.database.add(event)


class SubscriptionRepository:
    def __init__(self, database: Session) -> None:
        self.database = database

    def find_by_tenant(self, tenant_id: str) -> Subscription | None:
        return self.database.scalar(
            select(Subscription).where(Subscription.tenant_id == tenant_id)
        )

    def find_by_stripe_id(self, stripe_subscription_id: str) -> Subscription | None:
        return self.database.scalar(
            select(Subscription).where(
                Subscription.stripe_subscription_id == stripe_subscription_id
            )
        )

    def list_with_stripe_ids(self) -> list[Subscription]:
        return list(
            self.database.scalars(
                select(Subscription).where(
                    Subscription.stripe_subscription_id.is_not(None)
                )
            )
        )


class JobRepository:
    def __init__(self, database: Session) -> None:
        self.database = database

    def get(self, job_id: str) -> JobRun | None:
        return self.database.get(JobRun, job_id)
