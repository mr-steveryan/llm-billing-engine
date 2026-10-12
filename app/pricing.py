from decimal import Decimal

# Pinned: OpenAI gpt-6.1-sol, Standard tier, short context, USD per 1M tokens.
# Source: https://developers.openai.com/api/docs/pricing (read 2026-10-12).
# shortcut: no cache-write or long-context (>272K) rates; usage events don't carry that info, add columns if needed.
MODEL = "gpt-6.1-sol"
INPUT_PER_M = Decimal("2.00")
CACHED_INPUT_PER_M = Decimal("0.10")
OUTPUT_PER_M = Decimal("10.00")


def cost_usd(input_tokens: int, cached_input_tokens: int, output_tokens: int) -> Decimal:
    # OpenAI usage shape: cached ⊂ input, reasoning ⊂ output. So reasoning needs no term of its own
    # (it is already in output_tokens), and only the uncached part of input pays the full input rate.
    return (
        (input_tokens - cached_input_tokens) * INPUT_PER_M
        + cached_input_tokens * CACHED_INPUT_PER_M
        + output_tokens * OUTPUT_PER_M
    ) / 1_000_000
