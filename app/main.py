from typing import Annotated

from fastapi import BackgroundTasks, Depends, FastAPI, Header, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.billing import BillingProvider, BillingService, StripeBillingProvider
from app.config import get_settings
from app.database import SessionLocal, get_db
from app.errors import ServiceError
from app.jobs import enqueue_reconciliation, run_reconciliation
from app.metering import MeteringService
from app.models import JobRun, Tenant
from app.repositories import JobRepository, TenantRepository
from app.schemas import (
    CheckoutResponse,
    GenerateRequest,
    GenerateResponse,
    JobResponse,
    JobStatusResponse,
    UsageResponse,
    WebhookResponse,
)
from app.security import hash_api_key, secrets_match


settings = get_settings()
app = FastAPI(
    title="Usage Metering and Billing Engine",
    version="1.0.0",
    description="Multi-tenant usage, quota, pricing, and Stripe test-mode sync.",
)
app.state.billing_provider = StripeBillingProvider(settings)

Database = Annotated[Session, Depends(get_db)]


@app.exception_handler(ServiceError)
def handle_service_error(_request: Request, error: ServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": error.message}},
        headers=error.headers,
    )


def authenticated_tenant(
    database: Database,
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> Tenant:
    if not api_key:
        raise ServiceError(
            401,
            "Tenant API key is required",
            "api_key_required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
    key_hash = hash_api_key(api_key, settings.tenant_api_key_pepper)
    tenant = TenantRepository(database).find_by_api_key_hash(key_hash)
    if tenant is None:
        raise ServiceError(401, "Invalid tenant API key", "invalid_api_key")
    return tenant


def idempotency_key(
    value: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str:
    if value is None or not value.strip():
        raise ServiceError(
            400,
            "Idempotency-Key header is required",
            "idempotency_key_required",
        )
    normalized = value.strip()
    if len(normalized) > 255:
        raise ServiceError(
            400,
            "Idempotency-Key must be 255 characters or fewer",
            "invalid_idempotency_key",
        )
    return normalized


def require_admin(
    value: Annotated[str | None, Header(alias="X-Admin-Key")] = None,
) -> None:
    if value is None or not secrets_match(value, settings.admin_api_key):
        raise ServiceError(401, "Invalid admin API key", "invalid_admin_key")


TenantContext = Annotated[Tenant, Depends(authenticated_tenant)]
IdempotencyKey = Annotated[str, Depends(idempotency_key)]
AdminContext = Annotated[None, Depends(require_admin)]


@app.get("/health")
def health(database: Database) -> dict[str, str]:
    database.execute(select(1))
    return {"status": "ok"}


@app.post("/generate", status_code=201, response_model=GenerateResponse)
def generate(
    payload: GenerateRequest,
    response: Response,
    database: Database,
    tenant: TenantContext,
    key: IdempotencyKey,
) -> dict:
    result, replayed = MeteringService(database).record(tenant.id, key, payload)
    response.headers["Idempotent-Replayed"] = str(replayed).lower()
    return result


@app.get("/usage", response_model=UsageResponse)
def usage(database: Database, tenant: TenantContext) -> dict:
    return MeteringService(database).usage(tenant.id)


@app.post("/billing/checkout", response_model=CheckoutResponse)
def checkout(tenant: TenantContext, request: Request) -> dict[str, str]:
    provider: BillingProvider = request.app.state.billing_provider
    return provider.create_checkout(tenant.id)


@app.get("/billing/success")
def billing_success() -> dict[str, str]:
    return {"message": "Checkout completed; awaiting verified Stripe webhook"}


@app.get("/billing/cancel")
def billing_cancel() -> dict[str, str]:
    return {"message": "Checkout canceled; no subscription change was applied"}


@app.post("/webhooks/stripe", response_model=WebhookResponse)
async def stripe_webhook(
    request: Request,
    database: Database,
    stripe_signature: Annotated[
        str | None, Header(alias="Stripe-Signature")
    ] = None,
) -> dict[str, str]:
    if not stripe_signature:
        raise ServiceError(
            400,
            "Stripe-Signature header is required",
            "invalid_webhook_signature",
        )
    payload = await request.body()
    provider: BillingProvider = request.app.state.billing_provider
    event = provider.verify_webhook(payload, stripe_signature)
    status, event_id = BillingService(database).process_event(event)
    return {"status": status, "event_id": event_id}


@app.post(
    "/admin/jobs/reconcile",
    status_code=202,
    response_model=JobResponse,
)
def reconcile(
    background_tasks: BackgroundTasks,
    database: Database,
    _admin: AdminContext,
    request: Request,
) -> dict[str, str]:
    job = enqueue_reconciliation(database)
    provider: BillingProvider = request.app.state.billing_provider
    background_tasks.add_task(
        run_reconciliation,
        job.id,
        provider,
        SessionLocal,
    )
    return {"job_id": job.id, "status": job.status}


@app.get("/admin/jobs/{job_id}", response_model=JobStatusResponse)
def job_status(
    job_id: str,
    database: Database,
    _admin: AdminContext,
) -> dict:
    job: JobRun | None = JobRepository(database).get(job_id)
    if job is None:
        raise ServiceError(404, "Job not found", "job_not_found")
    return {
        "job_id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "attempts": job.attempts,
        "error_message": job.error_message,
    }
