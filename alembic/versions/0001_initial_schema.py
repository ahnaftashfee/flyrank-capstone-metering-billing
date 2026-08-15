"""Create billing tables and seed the two plans.

Revision ID: 0001
Revises:
Create Date: 2026-08-15
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "plans",
        sa.Column("slug", sa.String(length=32), primary_key=True),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("api_call_limit", sa.Integer(), nullable=False),
        sa.Column("ai_token_limit", sa.Integer(), nullable=False),
    )
    op.bulk_insert(
        sa.table(
            "plans",
            sa.column("slug", sa.String()),
            sa.column("display_name", sa.String()),
            sa.column("api_call_limit", sa.Integer()),
            sa.column("ai_token_limit", sa.Integer()),
        ),
        [
            {
                "slug": "free",
                "display_name": "Free",
                "api_call_limit": 1_000,
                "ai_token_limit": 100_000,
            },
            {
                "slug": "pro",
                "display_name": "Pro",
                "api_call_limit": 100_000,
                "ai_token_limit": 10_000_000,
            },
        ],
    )

    op.create_table(
        "tenants",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("api_key_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            "plan_slug",
            sa.String(length=32),
            sa.ForeignKey("plans.slug"),
            nullable=False,
        ),
        sa.Column("subscription_status", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("stripe_customer_id", sa.String(length=255), unique=True),
        sa.Column("stripe_subscription_id", sa.String(length=255), unique=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_period_end", sa.DateTime(timezone=True)),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.String(length=36),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("usage_type", sa.String(length=32), nullable=False),
        sa.Column("api_calls", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("ai_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_microcents", sa.BigInteger(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "idempotency_key",
            name="uq_usage_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_usage_tenant_period",
        "usage_events",
        ["tenant_id", "period_start"],
    )

    op.create_table(
        "stripe_events",
        sa.Column("event_id", sa.String(length=255), primary_key=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_table(
        "job_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("job_type", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_table("job_runs")
    op.drop_table("stripe_events")
    op.drop_index("ix_usage_tenant_period", table_name="usage_events")
    op.drop_table("usage_events")
    op.drop_table("subscriptions")
    op.drop_table("tenants")
    op.drop_table("plans")
