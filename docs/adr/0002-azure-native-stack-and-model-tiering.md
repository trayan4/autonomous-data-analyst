# ADR-0002: Azure-native stack with a tiered, provider-abstracted model layer

- **Status:** Ongoing
- **Date:** 2026-06-17

## Context

My cloud expertise are Azure-centric. I want frontier reasoning quality, low cost on
high-volume sub-tasks, and a portability story — without breaking the governed
Azure boundary (managed identity, private networking, unified billing).

## Decision

**Stack (Azure-native):**

| Concern | Resource |
|---|---|
| Orchestration | LangGraph (Python) |
| App host | Azure Container Apps (Functions for event-driven cheap nodes) |
| Retrieval | Azure AI Search — hybrid BM25 + vector, RRF |
| Warehouse | Azure SQL Database (Serverless) |
| State / memory | Azure Database for PostgreSQL (Flexible Server); SQLite locally |
| Identity / RBAC | Microsoft Entra ID + managed identity |
| Secrets | Azure Key Vault |
| Safety / PII | Azure AI Content Safety; PII via Azure AI Language or spaCy |
| Observability | OpenTelemetry → Azure Monitor / App Insights + LangSmith |
| IaC / CI-CD | Bicep; GitHub Actions (OIDC to Azure) |

**Model tiering:**

- **Reasoning tier** (orchestrator + Analysis) defaults to **Claude Sonnet 4.6 via
  Azure AI Foundry**.
- **Cheap tier** (routing, classification, guardrail scoring) uses a **nano-class
  model** (GPT-4.1-nano / GPT-5-nano).
- **Embeddings**: `text-embedding-3-small`.
- The model provider sits **behind an abstraction** so the reasoning model is a
  config choice. The default is chosen by the eval harness (ADR-0004), not by
  preference.

## Consequences

- Claude via Foundry keeps Entra auth, the Azure invoice (INR + GST), and Foundry
  governance/observability — no external key in Key Vault, no public egress.
- **Cost trade-off:** Sonnet ($3/$15 per 1M) is pricier than GPT-4.1 ($2/$8) on the
  reasoning tier. Even though reasoning-tier volume is low and the cheap tier
  carries the bulk; the abstraction will let me swap if the eval/cost numbers say so.
  This decision is to be reviewed later.
