# ADR-0000: Record architecture decisions

- **Status:** Ongoing
- **Date:** 2026-06-17

## Context

This project is a public reference architecture. 

## Decision

I've used Architecture Decision Records (ADRs):

- One Markdown file per significant decision, stored in `docs/adr/`, numbered
  sequentially (`NNNN-short-title.md`).
- Each ADR captures **Status**, **Context**, **Decision**, and **Consequences**.
- Superseded decisions are kept (marked `Superseded by ADR-XXXX`) rather than
  deleted, so the decision history is preserved.
- Small, easily reversible choices do not require an ADR.

Format follows Michael Nygard's ADR pattern.

## Consequences

- Every significant architectural choice is traceable to its rationale.
- The log doubles as the portfolio's "judgment" artifact — it makes the
  reasoning, including the things we deliberately chose *not* to build, legible.
- Minor overhead per decision; accepted as worthwhile.
