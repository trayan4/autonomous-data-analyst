"""
dependency-free cost/usage core for observability.

Every model call is recorded with its tier, deployment, token counts and computed
USD cost, aggregated within a "run" (one question, or a whole eval). OpenTelemetry
spans (Part 2) and Azure Monitor export (Part 3) layer on top of this; the cost
arithmetic lives here so it is testable without any backend or network.

Prices below are verified Azure Global Standard pay-as-you-go rates 
(the model deployment types I've used).
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

# USD per 1,000,000 tokens.
# Source: Azure OpenAI / AI Foundry "Global Standard" pay-as-you-go pricing.
# Note: Azure discounts cached static input prefixes, so input cost here is an upper bound.
PRICES: dict[str, dict[str, float]] = {
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.00, "output": 8.00},
}
_UNKNOWN = {"input": 0.0, "output": 0.0}


def set_prices(table: dict) -> None:
    """Replace the price table (e.g. with verified Foundry pricing)."""
    PRICES.clear()
    PRICES.update(table)


def cost_usd(deployment: str, prompt_tokens: int, completion_tokens: int) -> float:
    p = PRICES.get(deployment, _UNKNOWN)
    return (prompt_tokens * p["input"] + completion_tokens * p["output"]) / 1_000_000


@dataclass
class CallRecord:
    tier: str
    deployment: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    seconds: float


@dataclass
class Run:
    label: str = ""
    calls: list = field(default_factory=list)


# Thread-local so concurrent evals/requests don't cross-contaminate.
_state = threading.local()


def start_run(label: str = "") -> Run:
    run = Run(label=label)
    _state.run = run
    return run


def current_run():
    return getattr(_state, "run", None)


def end_run():
    run = current_run()
    _state.run = None
    return run


def record_call(tier: str, deployment: str, prompt_tokens, completion_tokens,
                seconds: float) -> CallRecord:
    pt, ct = int(prompt_tokens or 0), int(completion_tokens or 0)
    rec = CallRecord(tier, deployment, pt, ct, cost_usd(deployment, pt, ct), float(seconds))
    run = current_run()
    if run is not None:
        run.calls.append(rec)
    return rec


def summarize(run=None) -> dict:
    run = run if run is not None else current_run()
    calls = run.calls if run else []
    by_tier: dict[str, dict] = {}
    for c in calls:
        t = by_tier.setdefault(c.tier, {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0})
        t["calls"] += 1
        t["prompt"] += c.prompt_tokens
        t["completion"] += c.completion_tokens
        t["cost"] += c.cost_usd
    total = {
        "calls": len(calls),
        "prompt": sum(c.prompt_tokens for c in calls),
        "completion": sum(c.completion_tokens for c in calls),
        "cost": sum(c.cost_usd for c in calls),
        "seconds": sum(c.seconds for c in calls),
    }
    return {"by_tier": by_tier, "total": total}


def merge_summaries(summaries: list) -> dict:
    by_tier: dict[str, dict] = {}
    total = {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0, "seconds": 0.0}
    for s in summaries:
        for k in total:
            total[k] += s["total"][k]
        for tier, v in s["by_tier"].items():
            d = by_tier.setdefault(tier, {"calls": 0, "prompt": 0, "completion": 0, "cost": 0.0})
            for k in d:
                d[k] += v[k]
    return {"by_tier": by_tier, "total": total}


def format_summary(summary: dict, label: str = "") -> str:
    t = summary["total"]
    tag = f" [{label}]" if label else ""
    lines = [f"cost{tag}: ${t['cost']:.4f}  "
             f"({t['calls']} calls, {t['prompt']:,} in + {t['completion']:,} out tokens, "
             f"{t['seconds']:.1f}s)"]
    for tier, v in sorted(summary["by_tier"].items()):
        lines.append(f"      {tier:>6}: ${v['cost']:.4f}  {v['calls']} calls  "
                     f"in={v['prompt']:,} out={v['completion']:,}")
    return "\n".join(lines)


if __name__ == "__main__":
    start_run("demo")
    record_call("strong", "gpt-4.1", 1200, 300, 1.4)      # router + diagnosis
    record_call("cheap", "gpt-4o-mini", 800, 120, 0.5)    # simple synth
    print(format_summary(summarize(), "demo"))
