# ADR-0004: Eval-first development; fine-tuning is conditional, not default

- **Status:** Ongoing
- **Date:** 2026-06-17

## Context

Quality claims for a data-analyst agent are only credible if measured. The hard,
often-skipped part is a ground-truth eval harness — so it must come first, not
last.
Separately, fine-tuning is a tempting default that adds build/retrain burden
and is only worth it where it demonstrably pays off. In this context, it'll just 
add additional cost & duties (periodic fine-tuning, if we consider this as a 
production scenario) which will outweigh its benefits.

## Decision

- **Build the eval harness and golden set early (phase 2), before the agents**, so
  every component is measured from its first commit. The golden set uses a *frozen
  data snapshot with documented, injected anomalies*, so diagnostic answers have
  known ground truth. Offline eval runs in CI as a **regression gate**.
- **Fine-tuning is not a default step.** It is introduced only for a narrow,
  high-volume node (e.g. the cheap classifier/router), and only when the eval shows
  a frontier/nano model underperforming *and* the cost model shows the swap pays
  off. Any fine-tune will be recorded as an ADR with its cost/latency win and its
  retraining burden.
- **The default reasoning model (ADR-0002) is selected by evidence**: run candidate
  models (Claude via Foundry vs a GPT frontier model) through the golden set and
  compare quality and cost-per-query.

## Consequences

- Model and fine-tune choices become documented, evidence-backed portfolio
  artifacts rather than assertions.
- Trade-off: eval-first front-loads unglamorous work before any visible agent
  behaviour. This is intentional and is itself the differentiating signal.
