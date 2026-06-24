# Architecture Decision Records

This log records the significant architectural decisions for the **Autonomous Data
Analyst** reference architecture. One file per decision; format follows Michael
Nygard's ADR pattern (Status / Context / Decision / Consequences). Superseded
decisions are retained and marked.

| ADR | Title | Status |
|-----|-------|--------|
| [0000](0000-record-architecture-decisions.md) | Record architecture decisions | Ongoing |
| [0001](0001-agent-topology.md) | Agent topology — three agents, compliance gate, HITL interrupt | Ongoing |
| [0002](0002-azure-native-stack-and-model-tiering.md) | Azure-native stack with a tiered, provider-abstracted model layer | Ongoing |
| [0003](0003-validated-text-to-sql.md) | Reliable text-to-SQL via semantic layer + validation, not fine-tuning | Ongoing |
| [0004](0004-eval-first-and-conditional-fine-tuning.md) | Eval-first development; conditional fine-tuning | Ongoing |
| [0005](0005-pii-enforcement-at-the-request.md) | PII enforcement at the request — deterministic entitlement guardrail | Ongoing |
| [0006](0006-diagnostic-routing-and-analysis-agent.md) | Diagnostic routing and a dedicated Analysis agent with forced decomposition | Ongoing |

## Adding an ADR

1. Copy the structure of an existing ADR.
2. Use the next sequential number.
3. Set status to `Proposed`, then `Ongoing` once finalized (or `Superseded by
   ADR-XXXX` when replaced).
