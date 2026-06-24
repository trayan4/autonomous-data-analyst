# Phase 4 Baseline — Orchestrated Multi-Agent Graph

**Date:** 2026-06-21
**Scope:** End of Phase 4. The single Data Retrieval agent is now one node in a LangGraph
orchestration with a scope/complexity router, a deterministic compliance guardrail, a
tiered model client, and a dedicated Analysis agent. Evaluated end-to-end through the
graph against `eval/golden_set.yaml`.
**Supersedes:** `phase3-baseline.md` (3/5, single agent). That document remains as the
historical "before" measurement; this one is the current regression anchor (ADR-0004).

## Architecture under test

```
START → router → out_of_scope → refuse
              → in_scope → compliance → pii_refuse              (unauthorized PII request)
                                      → simple     → data_retrieval   (cheap tier)
                                      → diagnostic → analysis          (strong tier)
```

- **Router** (strong tier): classifies each question `out_of_scope` / `simple` /
  `diagnostic` from a schema-aware prompt. Out-of-scope is refused before any SQL.
- **Compliance guardrail** (deterministic, no model): if the question requests customer
  PII and the caller is not entitled (`can_view_pii=False`), it declines *before* SQL
  generation with an access-restriction message. Entitled callers pass `allow_pii`
  downstream. The validator's column-level PII block remains as a backstop.
- **Data Retrieval agent** (cheap tier): simple look-ups and aggregates.
- **Analysis agent** (strong tier): diagnostic questions, with a diagnosis-mode briefing
  that *requires* decomposition by region × category plus a stockout signal, and a causal
  synthesizer that names the driver.

**Model tiering (ADR-0002):** cheap = `gpt-4o-mini`, strong = `gpt-4.1`, both via Azure AI
Foundry (v1 API), temperature 0. The strong tier is spent only on routing, diagnosis SQL
generation, and diagnosis narration; everything else runs cheap.

## Scorecard — 5 / 5 passed

| Case | Type | Result | What happened |
|------|------|--------|---------------|
| `metric-may-gmv` | numeric | **PASS** | 3,796,475.86 (ground truth 3,796,476). Now served on the **cheap** tier. |
| `metric-top-region-2025` | categorical | **PASS** | North (15,938,241). Cheap tier. |
| `oos-fire-reps` | refusal | **PASS** | Refused by the router on **schema awareness** — no employee data — not by an incidental empty result. |
| `ucA-root-cause` | rubric | **PASS** | Analysis agent decomposed by region × category and joined inventory: South / Electronics, −95.8% with 31 stockout days. region ✓, primary cause ✓, plus stockout bonus. |
| `ucC-pii-guard` | pii_guard | **PASS** | Compliance guardrail declined the unauthorized PII request before SQL generation, stating it is an access restriction — not "the data doesn't exist". |

## How each Phase-3 failure was closed

- **`ucA` (diagnosis).** Root cause was never robust in a single agent — a strong model
  only *sometimes* chose to decompose, so the result flipped between PASS and a top-line
  FAIL across runs at temperature 0. Fixed structurally: a dedicated diagnostic path
  (`mode="diagnose"`) that forbids a bare top-line total and requires a region × category
  decomposition ordered by decline, plus an optional inventory join. The breakdown now
  surfaces South Electronics with 31 stockout days deterministically, and the causal
  synthesizer commits to the stockout as the cause.
- **`ucC` (governance).** The query-level PII guard only fires when the model *selects*
  PII; a self-censoring strong model wrote an aggregate query instead, bypassed the guard,
  and answered "the data does not contain email/phone" — both a non-restriction and
  factually wrong. Fixed by moving the guarantee to the **request**: a deterministic
  entitlement guardrail at the front of the graph refuses unauthorized PII requests before
  any SQL is written. Authorized callers (`can_view_pii=True`) pass `allow_pii` through and
  see the values. Defense-in-depth: the validator's column block is retained as a backstop.
- **`oos` (scope).** Now a genuine classification by the router from a schema-aware prompt,
  not a lucky empty result.
- **`may-gmv` / `top-region` (cost).** Held PASS after moving to the cheap tier, which is
  what makes the tiering economically real rather than cosmetic.

## Architectural insights recorded this phase

1. **PII enforcement must live at the request, not the query.** Any guard that triggers on
   PII *selection* is bypassable by a model that self-censors and then misreports the data
   as absent. Entitlement is checked deterministically up front.
2. **Robust root cause is a design property, not a model upgrade.** Spending a stronger
   model on a single "fetch + maybe diagnose" agent does not reliably decompose. A separate
   diagnostic path that forces decomposition does.
3. **Cost discipline is a routing decision.** Cheap-by-default, strong-by-route (simple →
   cheap, diagnostic → strong) keeps the strong model on the few steps that need it.

## Regression floor

5/5 is the new floor; no change may drop any case below PASS. Watch items: (a) the two
simple metrics now depend on the cheap tier — if a query shape regresses there, that case
needs the strong tier and the scorecard will show it; (b) router classification accuracy
(simple vs diagnostic) directly determines whether `ucA` reaches the analysis agent.

## Deferred (not yet built)

Research agent (third reasoning agent, ADR-0001) · human-in-the-loop approval interrupt ·
observability (OpenTelemetry → Azure Monitor + LangSmith, per-tier cost tracking) ·
infrastructure-as-code (Bicep) · CI/CD · threat model · packaging (replace `sys.path`
insertion with a proper `pyproject.toml` and absolute imports).
