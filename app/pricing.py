from dataclasses import dataclass


MICROCENTS_PER_CENT = 1_000_000
TOKENS_PER_PRICING_UNIT = 1_000_000


@dataclass(frozen=True)
class PricingConfig:
    """Pinned illustrative prices, stored as integer microcents."""

    api_call_microcents: int = 2_000
    input_per_million_microcents: int = 150_000_000
    cached_input_per_million_microcents: int = 37_500_000
    output_per_million_microcents: int = 600_000_000


PRICING = PricingConfig()


def rounded_unit_cost(quantity: int, per_million_microcents: int) -> int:
    return (
        quantity * per_million_microcents + TOKENS_PER_PRICING_UNIT // 2
    ) // TOKENS_PER_PRICING_UNIT


def calculate_cost_microcents(
    *,
    api_calls: int,
    input_tokens: int,
    cached_input_tokens: int,
    output_tokens: int,
    reasoning_tokens: int,
    pricing: PricingConfig = PRICING,
) -> int:
    if cached_input_tokens > input_tokens:
        raise ValueError("cached input tokens cannot exceed input tokens")
    if reasoning_tokens > output_tokens:
        raise ValueError("reasoning tokens cannot exceed output tokens")

    uncached_input_tokens = input_tokens - cached_input_tokens
    return sum(
        (
            api_calls * pricing.api_call_microcents,
            rounded_unit_cost(
                uncached_input_tokens,
                pricing.input_per_million_microcents,
            ),
            rounded_unit_cost(
                cached_input_tokens,
                pricing.cached_input_per_million_microcents,
            ),
            rounded_unit_cost(
                output_tokens,
                pricing.output_per_million_microcents,
            ),
        )
    )


def display_cents(cost_microcents: int) -> str:
    whole_cents, fractional_microcents = divmod(
        cost_microcents, MICROCENTS_PER_CENT
    )
    return f"{whole_cents}.{fractional_microcents:06d}"
