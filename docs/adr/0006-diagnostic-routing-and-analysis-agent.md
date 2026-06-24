# ADR-0006: Diagnostic routing and a dedicated Analysis agent with forced decomposition

- **Status:** Ongoing
- **Date:** 2026-06-21

## Context

ADR-0001 separated Analysis from Data Retrieval by *function*. Phase-4 evaluation
(`ucA`) revealed the *operational* reason the boundary matters, and surfaced a second
issue about where model tiering is decided.

With a single Data Retrieval agent asked to both fetch data and implicitly root-cause,
diagnosis quality was **non-deterministic**. Even on the strong model at temperature 0,
the agent sometimes wrote a rich region × category breakdown that found the cause (PASS)
and sometimes a top-line total that did not (FAIL), across otherwise identical runs. Root
cause was *accidental* — a property of whatever query the model happened to write — not a
*guarantee*. A bigger model (GPT-5.4) did not fix this; it only changed how often the coin landed
heads.

## Decision

- The **router** classifies each question into `out_of_scope` / `simple` / `diagnostic`
  (replacing the earlier binary in/out-of-scope check), on the strong tier with few-shot
  examples and a "when unsure, choose simple" bias.
- **Diagnostic** questions route to a dedicated **Analysis agent** that runs a
  *diagnosis-mode* briefing: it forbids a bare top-line total and **requires** the metric
  change to be decomposed by region × product category, ordered by largest decline, with an
  optional inventory/stockout join — paired with a causal synthesizer that names the
  driving segment and likely cause. Root cause is therefore a **structural property of the
  path**, not a hope about model behavior.
- **Simple** questions route to the Data Retrieval agent.
- **Per-route tiering** (refines ADR-0002; as-built models are `gpt-4o-mini` cheap /
  `gpt-4.1` strong via Azure AI Foundry): the strong tier is spent only on the router,
  diagnosis SQL generation, and diagnosis narration. Simple retrieval and simple synthesis
  run on the cheap tier.
- The Analysis agent is kept **slim**: a thin specialization of the shared
  generate → validate → execute → repair chain (it varies only the prompt mode, the tier,
  and the synthesizer), **not** a duplicated pipeline or a multi-agent sub-system.

## Consequences

- `ucA` passes reliably — the decomposition deterministically surfaces South / Electronics
  with a 31-day stockout, satisfying the rubric's region and primary-cause axes plus the
  stockout bonus.
- Cost discipline is realized through **routing**, not a global model choice: the strong
  model touches only the few steps that need it, and the simple metrics (`may-gmv`,
  `top-region`) hold PASS on the cheap tier.
- Eval-driven (ADR-0004): the simple/diagnostic split is validated by the golden set; a
  diagnostic misrouted as simple is observable as a `ucA` regression.
- **Trade-offs:**
  - Router accuracy now gates diagnosis — a diagnostic misclassified as simple yields a
    top-line answer. Mitigated by a strong-tier classifier with few-shot and the
    "unsure → simple" bias, and caught by eval (in reality, this never happened).
  - The diagnosis briefing is prescriptive (it forces a region × category shape). If future
    use cases need other axes (channel, time cohort, customer segment), the directive is
    generalized under a new ADR.
  - The slim Analysis agent still couples analysis with synthesis — the same trade-off
    ADR-0001 flagged. I'll split it if eval shows synthesis quality degrading the analysis.
