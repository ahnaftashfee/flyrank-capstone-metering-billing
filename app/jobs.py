import time
from datetime import UTC, datetime
from typing import Callable

from sqlalchemy.orm import Session, sessionmaker

from app.billing import BillingProvider, BillingService
from app.models import JobRun, new_id
from app.repositories import JobRepository, SubscriptionRepository


def enqueue_reconciliation(database: Session) -> JobRun:
    job = JobRun(
        id=new_id(),
        job_type="stripe_subscription_reconciliation",
        status="queued",
        attempts=0,
    )
    database.add(job)
    database.commit()
    return job


def run_reconciliation(
    job_id: str,
    provider: BillingProvider,
    session_factory: sessionmaker,
    *,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    for attempt in range(1, max_attempts + 1):
        database = session_factory()
        try:
            job = JobRepository(database).get(job_id)
            if job is None:
                return
            job.status = "running"
            job.attempts = attempt
            database.commit()

            subscriptions = SubscriptionRepository(database).list_with_stripe_ids()
            billing = BillingService(database)
            for subscription in subscriptions:
                stripe_data = provider.retrieve_subscription(
                    str(subscription.stripe_subscription_id)
                )
                billing.sync_subscription(stripe_data)

            job = JobRepository(database).get(job_id)
            if job is None:
                return
            job.status = "succeeded"
            job.error_message = None
            job.finished_at = datetime.now(UTC)
            database.commit()
            return
        except Exception as error:
            database.rollback()
            if attempt == max_attempts:
                failed_database = session_factory()
                try:
                    failed_job = JobRepository(failed_database).get(job_id)
                    if failed_job is not None:
                        failed_job.status = "failed"
                        failed_job.attempts = attempt
                        failed_job.error_message = str(error)[:1_000]
                        failed_job.finished_at = datetime.now(UTC)
                        failed_database.commit()
                finally:
                    failed_database.close()
                return
            sleep(retry_delay_seconds * attempt)
        finally:
            database.close()
