from pydantic import BaseModel, ConfigDict, Field, model_validator


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = Field(default=0, ge=0, le=10_000_000)
    cached_input_tokens: int = Field(default=0, ge=0, le=10_000_000)
    output_tokens: int = Field(default=0, ge=0, le=10_000_000)
    reasoning_tokens: int = Field(default=0, ge=0, le=10_000_000)

    @model_validator(mode="after")
    def validate_subcategories(self) -> "GenerateRequest":
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached_input_tokens cannot exceed input_tokens")
        if self.reasoning_tokens > self.output_tokens:
            raise ValueError("reasoning_tokens cannot exceed output_tokens")
        return self


class GenerateResponse(BaseModel):
    event_id: str
    idempotency_key: str
    plan: str
    api_calls_used: int
    api_calls_limit: int
    ai_tokens_used: int
    ai_tokens_limit: int
    event_cost_microcents: int
    message: str


class UsageBucket(BaseModel):
    used: int
    limit: int
    remaining: int


class UsageResponse(BaseModel):
    tenant_id: str
    period: str
    plan: str
    subscription_status: str
    api_calls: UsageBucket
    ai_tokens: UsageBucket
    cost_microcents: int
    cost_cents: str


class CheckoutResponse(BaseModel):
    session_id: str
    checkout_url: str


class WebhookResponse(BaseModel):
    status: str
    event_id: str


class JobResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    job_type: str
    status: str
    attempts: int
    error_message: str | None
