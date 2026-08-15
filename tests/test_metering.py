from datetime import date

from sqlalchemy import func, select

from app.metering import current_period_start
from app.models import Tenant, UsageEvent, new_id
from app.pricing import calculate_cost_microcents
from tests.conftest import TENANT_A_ID, TENANT_A_KEY, TENANT_B_KEY


def headers(key: str, idempotency_key: str) -> dict[str, str]:
    return {"X-API-Key": key, "Idempotency-Key": idempotency_key}


def add_usage(
    session_factory,
    *,
    api_calls: int = 0,
    ai_tokens: int = 0,
    tenant_id: str = TENANT_A_ID,
) -> None:
    with session_factory() as database:
        database.add(
            UsageEvent(
                id=new_id(),
                tenant_id=tenant_id,
                idempotency_key=new_id(),
                request_hash="fixture",
                usage_type="fixture",
                api_calls=api_calls,
                input_tokens=ai_tokens,
                cached_input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                ai_tokens=ai_tokens,
                cost_microcents=0,
                response_payload={"fixture": True},
                period_start=current_period_start(),
            )
        )
        database.commit()


def test_duplicate_request_returns_original_and_counts_once(
    client, session_factory
) -> None:
    payload = {
        "input_tokens": 1_000,
        "cached_input_tokens": 200,
        "output_tokens": 300,
        "reasoning_tokens": 50,
    }
    first = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "retry-safe-key"),
        json=payload,
    )
    second = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "retry-safe-key"),
        json=payload,
    )

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json() == second.json()
    assert first.headers["Idempotent-Replayed"] == "false"
    assert second.headers["Idempotent-Replayed"] == "true"
    with session_factory() as database:
        count = database.scalar(select(func.count()).select_from(UsageEvent))
        assert count == 1


def test_same_key_with_different_payload_is_rejected(client) -> None:
    first = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "conflicting-key"),
        json={"input_tokens": 10},
    )
    conflict = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "conflicting-key"),
        json={"input_tokens": 11},
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_exact_api_quota_is_allowed_then_next_call_is_429(
    client, session_factory
) -> None:
    add_usage(session_factory, api_calls=999)

    boundary = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "boundary-key"),
        json={},
    )
    over = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "over-key"),
        json={},
    )

    assert boundary.status_code == 201
    assert boundary.json()["api_calls_used"] == 1_000
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "api_quota_exceeded"
    assert over.headers["Retry-After"] == "3600"


def test_exact_token_quota_is_allowed_then_next_token_is_429(
    client, session_factory
) -> None:
    add_usage(session_factory, ai_tokens=99_999)
    boundary = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "token-boundary"),
        json={"input_tokens": 1},
    )
    over = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "token-over"),
        json={"input_tokens": 1},
    )

    assert boundary.status_code == 201
    assert boundary.json()["ai_tokens_used"] == 100_000
    assert over.status_code == 429
    assert over.json()["error"]["code"] == "token_quota_exceeded"


def test_past_due_subscription_returns_402(client, session_factory) -> None:
    with session_factory() as database:
        tenant = database.get(Tenant, TENANT_A_ID)
        tenant.subscription_status = "past_due"
        database.commit()

    response = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "past-due"),
        json={},
    )
    assert response.status_code == 402
    assert response.json()["error"]["code"] == "payment_required"


def test_token_pricing_does_not_double_count_cached_or_reasoning_tokens() -> None:
    cost = calculate_cost_microcents(
        api_calls=1,
        input_tokens=1_000_000,
        cached_input_tokens=400_000,
        output_tokens=500_000,
        reasoning_tokens=100_000,
    )
    cost_with_different_reasoning_subset = calculate_cost_microcents(
        api_calls=1,
        input_tokens=1_000_000,
        cached_input_tokens=400_000,
        output_tokens=500_000,
        reasoning_tokens=200_000,
    )

    assert cost == 405_002_000
    assert cost_with_different_reasoning_subset == cost


def test_usage_rollup_matches_event_cost_and_is_tenant_isolated(client) -> None:
    response = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "usage-rollup"),
        json={
            "input_tokens": 1_000,
            "cached_input_tokens": 250,
            "output_tokens": 500,
            "reasoning_tokens": 100,
        },
    )
    usage_a = client.get("/usage", headers={"X-API-Key": TENANT_A_KEY})
    usage_b = client.get("/usage", headers={"X-API-Key": TENANT_B_KEY})

    assert response.status_code == 201
    assert usage_a.status_code == 200
    assert usage_a.json()["api_calls"]["used"] == 1
    assert usage_a.json()["ai_tokens"]["used"] == 1_500
    assert usage_a.json()["cost_microcents"] == response.json()[
        "event_cost_microcents"
    ]
    assert usage_b.json()["api_calls"]["used"] == 0
    assert usage_b.json()["ai_tokens"]["used"] == 0


def test_boundary_validation_rejects_bad_token_categories(client) -> None:
    response = client.post(
        "/generate",
        headers=headers(TENANT_A_KEY, "invalid-categories"),
        json={"input_tokens": 10, "cached_input_tokens": 11},
    )
    assert response.status_code == 422


def test_missing_auth_and_idempotency_headers_are_clean_4xx(client) -> None:
    no_auth = client.get("/usage")
    no_idempotency = client.post(
        "/generate",
        headers={"X-API-Key": TENANT_A_KEY},
        json={},
    )
    assert no_auth.status_code == 401
    assert no_idempotency.status_code == 400
