# ADR-0005: PII enforcement at the request, not the query — a deterministic entitlement guardrail

- **Status:** Ongoing
- **Date:** 2026-06-21

## Context

ADR-0001 designated Compliance as a guardrail node rather than an agent. Phase-4
evaluation (`ucC`) exposed why the *placement* of the check is the load-bearing
decision.

The initial PII enforcement lived at SQL-validation time: the validator blocks any
query that **selects** a PII column (`full_name`, `email`, `phone`). This only fires
if the model selects PII. When the strong model (`gpt-4.1`) was engaged, it read the
"never select PII" rule in the briefing and **self-censored** — it wrote an aggregate
customer-count query, never selected a PII column, so the column guard never triggered,
and then WRONGLY answered *"this data does not contain email, phone."*

That outcome is doubly wrong: it states **no restriction** (the entitlement story is
invisible), and it is **factually false** — the columns exist; access is restricted, not
absent. The lesson: a guard that triggers on PII *selection* is bypassable by any model
that self-censors, and it degrades into a misleading "we don't have that" answer. The
guarantee cannot depend on what SQL the model chooses to write.

## Decision

Enforce PII entitlement at the **request**, deterministically, at the front of the graph,
**before** SQL generation.

- A **compliance node** (no model call) runs for every in-scope question. It detects
  whether the question requests customer PII via a deterministic rule-based 
  keyword check over contact-field terms (`email`, `phone`, `contact details`, `full name`, …), with a **marketing-channel exclusion** so "GMV by email channel" is not mistaken for a PII request.
- Entitlement is an explicit caller input: `can_view_pii`, supplied by the auth layer,
  defaulting to `False`.
  - **Unauthorized + PII request →** decline before any SQL is generated, with an explicit
    access-restriction message ("not authorized to view customer personal data… this is an
    access restriction, not a gap in the data"). This will also help with cost-limitting.
  - **Authorized →** pass `allow_pii` downstream; the agent selects and returns the values.
- The validator's column-level PII block is **retained as a backstop** (defense in depth)
  for any path where a generated query references PII despite the front gate.

## Consequences

- The guarantee no longer depends on model behavior. A self-censoring — or adversarial —
  model can no longer convert a restricted request into a misleading "we don't have that".
- Honest UX: *restricted* is reported as restricted, never as *absent*.
- The gate is deterministic and offline-testable (no model inside it), so it is reliable in
  eval; `ucC` moves from FAIL to PASS deterministically (tested multiple times).
- **Trade-off:** the request-level detector is keyword-based and heuristic — it can
  over-match (novel PII phrasings) or under-match. Mitigations: the channel-context
  exclusion reduces false positives, and the validator backstop still catches column-level
  leaks. If the keyword gate proves brittle, a model-assisted PII-intent classifier on the
  strong tier can replace it under a new ADR.
- **Scope:** this ADR covers entitlement (who may see PII). Redaction/anonymization of PII
  in returned values for partially entitled roles remains future work (Phase 5 governance).