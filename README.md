# Autonomous Data Analyst

A multi-agent **LangGraph** reference architecture on **Azure** that answers
natural-language analytics questions against an e-commerce warehouse: it classifies
the request, enforces data-access entitlements, writes *validated* read-only SQL,
root-causes anomalies, and narrates the finding — with model-tier cost control,
OpenTelemetry observability, and token streaming throughout.

It is built **depth-first and eval-first**: a narrow slice of use cases taken all the
way to a passing golden-set scorecard with known ground truth, rather than a broad
demo that works only on the happy path. Every significant decision is recorded as an
[ADR](docs/adr/README.md).

```
ada-ask "Why did sales drop last month?"

> Sales dropped last month primarily due to a collapse in Electronics sales in the
> South region, which fell from an average of 571,223 to 23,927 (-95.8%). This
> segment experienced a full 31 days of stockout, the likely cause. Secondary
> contributors were smaller declines in South Beauty (-21.4%), Sports (-17.6%),
> Apparel (-17.2%), and Home (-16.6%) — none approaching the Electronics drop.
route : analysis
```

The answer streams in token-by-token, and the figures above are the *injected* ground
truth: the synthetic dataset has a documented South-Electronics stockout, so a
diagnostic answer can be scored for correctness rather than judged by vibes.

## Architecture

A single LangGraph state machine routes each question through governance before any
SQL is written:

```
                 ┌─────────── out_of_scope ──────────► refuse ──┐
                 │                                              │
START ─► router ─┤                                              ├─► END
                 │            ┌─ pii_refuse ──────────────────► │
                 └─ in_scope ─┤  (entitlement refusal)          │
                  compliance  ├─ simple ─────► data_retrieval ─►│   (cheap tier)
                              │                                 │
                              └─ diagnostic ─► analysis ───────►│   (strong tier)
```

- **router** — an LLM scope classifier. Out-of-scope questions (employees, suppliers,
  competitors — data the warehouse does not hold) are refused *up front*, which is the
  robust fix for confabulation. (ADR-0001, ADR-0006)
- **compliance** — a *deterministic* entitlement guardrail. A request for customer PII
  from an unentitled caller is declined before any SQL is generated, with a clear
  "access restriction, not missing data" message. The guarantee lives at the request,
  not the query, so a self-censoring model cannot turn a blocked request into a
  misleading "we don't have that." (ADR-0005)
- **data_retrieval** — the Data Retrieval agent: schema-RAG prompt → deterministic SQL
  validator (read-only, allow-listed tables) → execute → narrate, with an
  execution-repair loop that feeds DB errors back for a corrected query. (ADR-0003)
- **analysis** — the diagnostic path: forces a region×category decomposition joined to
  the stockout signal, so root cause is a *design property* of the query, not an
  emergent hope from a bigger model. (ADR-0006)

The few-shot examples that ground SQL generation are chosen by a pluggable retriever —
synonym-expanded keyword overlap by default, or an opt-in **hybrid** of BM25 + dense
vectors fused with Reciprocal Rank Fusion. (ADR-0007)

## What this demonstrates

| Concern | Approach | ADR |
|---|---|---|
| Confabulation on unanswerable questions | Scope router refuses out-of-scope before any SQL runs | 0001, 0006 |
| Unsafe / hallucinated SQL | Schema-RAG + deterministic `sqlglot` validator (read-only, allow-listed) + execution-repair | 0003 |
| PII / entitlement leakage | Deterministic guardrail at the *request*, not the query | 0005 |
| Root-cause quality | Forced region×category decomposition + stockout join | 0006 |
| Few-shot retrieval quality | Hybrid BM25 + dense-vector selection fused with RRF (opt-in) | 0007 |
| LLM cost | Two-tier models: cheap default, strong only where the eval proves it's needed | 0002 |
| Provider lock-in | Thin, provider-abstracted model client (Azure AI Foundry v1 / OpenAI-compatible) | 0002 |
| Regression safety | Eval-first golden set with known ground truth, scored on every change | 0004 |
| Operability & cost attribution | OpenTelemetry spans + per-call cost telemetry → Azure Monitor | Phase 5 |
| Responsiveness | Token streaming of the final narration via LangGraph's custom channel | Phase 5 |

## Quick start

**Prerequisites:** Python ≥ 3.10, an Azure AI Foundry resource with two chat
deployments (`gpt-4o-mini`, `gpt-4.1`), and an Azure SQL database. The ODBC Driver 18
for SQL Server is needed for live queries. Hybrid retrieval additionally needs an
embedding deployment (`text-embedding-3-small`) (step 6).

```sh
# 1. install — core + data tooling + Azure trace export
pip install -e ".[data,azure]" --pre
#    optional surfaces: web UI (ada-serve) and hybrid retrieval
pip install -e ".[api,retrieval]" --pre

# 2. configure
cp .env.example .env        # then fill in your Foundry + Azure SQL values

# 3. provision Azure (SQL database + Foundry deployments; App Insights if you'll trace)
#    Follow infra/setup.md — a manual portal/CLI checklist mapped to each .env key.
#    (Declarative Bicep IaC is on the roadmap; see docs/future_improvements.md.)

# 4. build the dataset (writes ./data/raw/*.csv, loads into Azure SQL)
python -m ada.data.generate_data
python -m ada.data.load_to_azuresql
python -m ada.semantic.validate_semantic_layer    # sanity-check the layer vs the data

# 5. verify, then ask
ada-eval                                      # runs the golden set against live Azure (expect 5/5)
ada-ask "Why did sales drop last month?"      # CLI, streams the answer
ada-serve                                     # optional: web UI at http://127.0.0.1:8000
```

To turn on hybrid few-shot retrieval, install the `retrieval` extra (above), ensure an
embedding deployment exists, and set `ADA_RETRIEVER=hybrid` in `.env`. It is opt-in: the
default (`keyword`) needs no extra dependencies and no embedding call. Re-run `ada-eval`
after switching, since retrieval is a prompt input. (ADR-0007)

## The eval gate

The golden set is the contract. `ada-eval` runs every case end-to-end against live
Azure and scores it — root-cause accuracy, numeric groundedness, governance refusals,
and out-of-scope handling — printing a per-call cost breakdown that proves the tiering.

```
id                       type         result  detail
------------------------------------------------------------------------------
ucA-root-cause           rubric       PASS  region=True primary=True
ucC-pii-guard            pii_guard    PASS  blocked + explained
metric-may-gmv           numeric      PASS  expected 3,796,476; found 3,796,475.86
metric-top-region-2025   categorical  PASS  expected 'North'
oos-fire-reps            refusal      PASS  declined / no data
------------------------------------------------------------------------------
5/5 passed
cost [eval total]: ~$0.013–0.031   (cheap: 4 calls; strong: 7–9 calls)
```

Cost varies run-to-run by a cent or two depending on whether the SQL execution-repair
loop triggers — which is the self-healing behaving as designed.

## Observability & cost

Model tiering (ADR-0002) keeps the cheap model everywhere it passes the eval and
spends the strong model only on the router, diagnosis SQL generation, and diagnosis
narration. Every model call is recorded with token usage and computed cost, and
emitted as an OpenTelemetry span following the GenAI semantic conventions
(`gen_ai.*`), plus an `ada.model_tier` attribute.

Set `ADA_TRACE=azure` to ship spans to Azure Monitor / Application Insights; the
[runbook](docs/observability-azure-monitor.md) covers setup and the
[query cheat-sheet](docs/observability-queries.md) has ready KQL for route
distribution, per-tier cost, and per-node latency. Streamed calls carry
`gen_ai.request.streaming = true` so they're distinguishable in the trace, and still
record usage (via `stream_options.include_usage`).

## Project structure

```
src/ada/
  agent/          model client, config, context builder, retriever, SQL generator/
                  validator, executor, synthesizer, and the Data Retrieval + Analysis agents
  orchestrator/   the LangGraph state machine (graph.py) + ada-ask entry point
  api/            FastAPI web surface (app.py) + ada-serve entry point (token-streaming UI)
  eval/           golden set + scorer + harness (ada-eval entry point)
  observability/  cost telemetry + OpenTelemetry tracing
  semantic/       semantic_layer.yaml + its validator
  data/           synthetic data generation, schema.sql, Azure SQL loaders
tests/            offline test scripts mirroring the package (no live Azure needed)
docs/             SCOPE, anomaly spec, phase baselines, observability, future work, and ADRs
infra/            setup.md — manual dev Azure provisioning (Bicep IaC is on the roadmap)
```

Run the offline tests (they mock the model, DB, and agents via dependency injection,
so no Azure is required):

```sh
for t in $(find tests -name 'test_*.py'); do python "$t"; done
```

## Design decisions

The [ADR log](docs/adr/README.md) is the reasoning trail. The load-bearing ones:
[0001](docs/adr/0001-agent-topology.md) topology,
[0002](docs/adr/0002-azure-native-stack-and-model-tiering.md) stack + tiering,
[0003](docs/adr/0003-validated-text-to-sql.md) validated text-to-SQL,
[0005](docs/adr/0005-pii-enforcement-at-the-request.md) PII at the request,
[0006](docs/adr/0006-diagnostic-routing-and-analysis-agent.md) diagnostic routing, and
[0007](docs/adr/0007-hybrid-few-shot-retrieval.md) hybrid few-shot retrieval.

## Scope & roadmap

**Built and verified (this repo):** the routing + compliance + retrieval + diagnosis
graph, validated text-to-SQL with execution-repair, the deterministic entitlement
guardrail, model tiering, cost telemetry + OpenTelemetry → Azure Monitor, token
streaming, a hybrid (BM25 + dense-vector) few-shot retriever, a FastAPI streaming web
UI (`ada-serve`), and the eval harness passing 5/5.

**Designed, on the roadmap:** a third Research agent for multi-source corroboration,
a human-in-the-loop approval interrupt before executive output, chart/exec-summary
generation, Bicep IaC (the dev provisioning is currently a manual checklist,
`infra/setup.md`), a CI regression gate wired to `ada-eval`, RBAC-based PII visibility,
and a threat model. The [SCOPE](docs/SCOPE.md) document holds the full v1 vision;
[future_improvements](docs/future_improvements.md) sketches the next steps; this README
reflects what is actually implemented.
