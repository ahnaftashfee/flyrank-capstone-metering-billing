import json
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.database import Base, get_db
from app.errors import ServiceError
from app.main import app
from app.models import Plan, Tenant
from app.security import hash_api_key


TENANT_A_ID = "10000000-0000-0000-0000-000000000001"
TENANT_B_ID = "20000000-0000-0000-0000-000000000002"
TENANT_A_KEY = "tenant-a-test-key"
TENANT_B_KEY = "tenant-b-test-key"
PEPPER = "local-development-pepper"


class FakeBillingProvider:
    def __init__(self) -> None:
        self.subscriptions: dict[str, dict] = {}

    def create_checkout(self, tenant_id: str) -> dict[str, str]:
        return {
            "session_id": "cs_test_capstone",
            "checkout_url": f"https://checkout.stripe.test/{tenant_id}",
        }

    def verify_webhook(self, payload: bytes, signature: str) -> dict:
        if signature != "valid-signature":
            raise ServiceError(
                400,
                "Invalid Stripe webhook signature",
                "invalid_webhook_signature",
            )
        return json.loads(payload)

    def retrieve_subscription(self, subscription_id: str) -> dict:
        return self.subscriptions[subscription_id]


@pytest.fixture
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    database_path = tmp_path / "test.db"
    engine = create_engine(
        f"sqlite:///{database_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as database:
        database.add_all(
            [
                Plan(
                    slug="free",
                    display_name="Free",
                    api_call_limit=1_000,
                    ai_token_limit=100_000,
                ),
                Plan(
                    slug="pro",
                    display_name="Pro",
                    api_call_limit=100_000,
                    ai_token_limit=10_000_000,
                ),
                Tenant(
                    id=TENANT_A_ID,
                    name="Tenant A",
                    api_key_hash=hash_api_key(TENANT_A_KEY, PEPPER),
                    plan_slug="free",
                    subscription_status="active",
                ),
                Tenant(
                    id=TENANT_B_ID,
                    name="Tenant B",
                    api_key_hash=hash_api_key(TENANT_B_KEY, PEPPER),
                    plan_slug="free",
                    subscription_status="active",
                ),
            ]
        )
        database.commit()
    yield factory
    engine.dispose()


@pytest.fixture
def fake_provider() -> FakeBillingProvider:
    return FakeBillingProvider()


@pytest.fixture
def client(
    session_factory: sessionmaker[Session],
    fake_provider: FakeBillingProvider,
) -> Generator[TestClient, None, None]:
    def override_database() -> Generator[Session, None, None]:
        database = session_factory()
        try:
            yield database
        finally:
            database.close()

    app.dependency_overrides[get_db] = override_database
    app.state.billing_provider = fake_provider
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
