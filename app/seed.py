from app.config import get_settings
from app.database import SessionLocal
from app.models import Plan, Tenant, UsageEvent
from app.pricing import calculate_cost_microcents
from app.security import hash_api_key
from app.metering import current_period_start


DEMO_TENANT_ID = "00000000-0000-0000-0000-000000000001"
DEMO_USAGE_ID = "00000000-0000-0000-0000-000000000002"


def seed() -> None:
    settings = get_settings()
    database = SessionLocal()
    try:
        if database.get(Plan, "free") is None:
            raise RuntimeError("Run Alembic migrations before seeding")

        tenant = database.get(Tenant, DEMO_TENANT_ID)
        if tenant is None:
            tenant = Tenant(
                id=DEMO_TENANT_ID,
                name="Demo Tenant Near Quota",
                api_key_hash=hash_api_key(
                    settings.demo_tenant_api_key,
                    settings.tenant_api_key_pepper,
                ),
                plan_slug="free",
                subscription_status="active",
            )
            database.add(tenant)

        if database.get(UsageEvent, DEMO_USAGE_ID) is None:
            api_calls = 998
            event_cost = calculate_cost_microcents(
                api_calls=api_calls,
                input_tokens=0,
                cached_input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
            )
            database.add(
                UsageEvent(
                    id=DEMO_USAGE_ID,
                    tenant_id=DEMO_TENANT_ID,
                    idempotency_key="seed-near-quota",
                    request_hash="seed",
                    usage_type="seed",
                    api_calls=api_calls,
                    input_tokens=0,
                    cached_input_tokens=0,
                    output_tokens=0,
                    reasoning_tokens=0,
                    ai_tokens=0,
                    cost_microcents=event_cost,
                    response_payload={"seeded": True},
                    period_start=current_period_start(),
                )
            )

        database.commit()
        print(f"Seeded tenant {DEMO_TENANT_ID} at 998/1000 API calls")
    finally:
        database.close()


if __name__ == "__main__":
    seed()
