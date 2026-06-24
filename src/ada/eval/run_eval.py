"""
runs the Data Retrieval agent against the golden set and score it.

For each case in golden_set.yaml: ask the agent the question, collect its answer,
then hand all answers to scorer.score_all for grading. This is the regression gate
(ADR-0004): a single PASS/FAIL scorecard the whole pipeline is measured against.

  ada-eval                           # live: runs the real agent (needs model + DB)

run(agent_fn=...) accepts a stand-in agent for offline testing.
"""
from __future__ import annotations

import importlib.resources as resources
import sys

import yaml

from ada.eval.scorer import score_all
from ada.observability import telemetry, tracing

GOLDEN = resources.files("ada.eval") / "golden_set.yaml"


def load_cases():
    return yaml.safe_load(GOLDEN.read_text())["cases"]


def run(agent_fn=None, verbose=True):
    if agent_fn is None:
        from ada.orchestrator.graph import run as graph_run

        def agent_fn(q):
            return graph_run(q).get("answer", "")

    answers = {}
    case_summaries = []
    for c in load_cases():
        if telemetry is not None:
            telemetry.start_run(c["id"])
        ans = agent_fn(c["question"])
        answers[c["id"]] = ans
        case_cost = None
        if telemetry is not None:
            case_cost = telemetry.summarize()
            case_summaries.append((c["id"], case_cost))
            telemetry.end_run()
        if verbose:
            print(f"\n[{c['id']}] {c['question']}\n  -> {str(ans)[:300]}")
            if case_cost is not None and case_cost["total"]["calls"]:
                print("  " + telemetry.format_summary(case_cost))

    print()
    passed, results = score_all(answers)

    if telemetry is not None and case_summaries:
        total = telemetry.merge_summaries([s for _, s in case_summaries])
        if total["total"]["calls"]:
            print("\n" + "-" * 78)
            print(telemetry.format_summary(total, "eval total"))
            print("-" * 78)

    return passed, results, answers


def main():
    """Console entry point: ada-eval."""
    tracing.configure_from_env()          # ADA_TRACE=console|azure|none (default none)
    try:
        ok, _, _ = run()
    finally:
        tracing.shutdown()                # flush batched spans to Azure Monitor
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
