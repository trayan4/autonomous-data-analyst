# ADR-0003: Reliable text-to-SQL via semantic layer + validation, not a fine-tuned SQL model

- **Status:** Ongoing
- **Date:** 2026-06-17

## Context

Wrong numbers in an output destroy credibility instantly, so text-to-SQL must be
both reliable and safe. A fine-tuned SQL model can ensure accuracy on a *fixed*
schema, but it couples the model to that schema and forces a retrain on every
schema change — a poor fit for a multi-client / portable reference architecture,
and an unnecessary standing cost (a deployed fine-tune bills even at zero traffic).

## Decision

Generate SQL with the reasoning model using **schema-as-context / schema-RAG**
(relevant table, column, and metric definitions retrieved from the semantic
layer/catalog) plus **few-shot examples**, then pass **every generated query
through a deterministic validator before execution**:

- read-only enforcement (no DML/DDL),
- allow-listed tables and columns only,
- mandatory row and cost/time limits,
- rejection of disallowed operations and cross-joins outside the model.

Query results then feed the **groundedness check** (ADR-0001 guardrail) so every
cited number must trace to an actual query result.

## Consequences

- Portable across schemas and clients; no retraining cycle.
- The validator is the hard safety gate — the system fails closed on a bad query.
- Trade-off: schema-RAG + few-shot may be outperformed by a schema-specialised fine-tune
  on raw accuracy. For now, I'm accepting this for portability and **measure the gap on the
  golden set** (ADR-0004). If the eval shows an unacceptable gap on a stable
  schema, I'll go ahead with fine-tuning (under its own ADR).