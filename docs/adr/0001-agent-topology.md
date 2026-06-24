# ADR-0001: Agent topology — three agents, compliance as a gate, human approval as an interrupt

- **Status:** Ongoing
- **Date:** 2026-06-17

## Context

I initially envisioned six agents: Orchestrator, Research, Data Retrieval,
Compliance, Document Generation, and Human Approval. But more agents increase
orchestration surface, latency, and per-query cost (each hop is an LLM call), and
they make evaluation and debugging harder. Two of the six are also not autonomous
reasoners: "Human Approval" can be a control gate, and "Compliance" is a deterministic
guardrail. Decorative agent count is a liability, not a strength.

## Decision

- The system will use **three reasoning agents** behind one **orchestrator/router**:
  - **Data Retrieval** — catalog/semantic-layer lookup and validated text-to-SQL
    against the warehouse.
  - **Research** — external/contextual signals (web, incident logs, market data).
  - **Analysis** — quantitative decomposition, root-cause reasoning, and synthesis
    of the final answer/report.
- **Compliance is a guardrail node**, not an agent: RBAC entitlement check, PII
  detection, groundedness check, and toxicity/safety. It can hard-block before
  output.
- **Human approval is a LangGraph interrupt** (HITL gate) before any
  executive-facing or externally published output, not an agent.

## Consequences

- Fewer LLM hops → lower latency and cost, simpler eval.
- Each boundary is justified by *function* — warehouse/SQL vs external/web vs
  reasoning+synthesis — and can be defended in review.
- Trade-off: the Analysis agent carries two responsibilities (analysis and
  synthesis). If that coupling causes problems (e.g. eval shows synthesis quality
  degrading complex analysis), I'll split it under a new ADR.
- A dedicated Document-Generation agent can be added later if a use case needs it,
  with its own ADR.
