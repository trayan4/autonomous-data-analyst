# Observability — KQL quick reference

Three queries for the traces this system ships to Azure Monitor, with how to read each.

These use the **classic Application Insights schema** (`dependencies`, `name`, `duration`,
`customDimensions`, `timestamp`) — what you get querying from the App Insights resource's
Logs blade. From a **Log Analytics workspace** query instead, swap to: `AppDependencies`,
`Name`, `DurationMs`, `Properties[...]`, `TimeGenerated`.

## 0. Confirm spans are arriving — the first thing to run

```kusto
dependencies
| where name startswith "node." | take 20
```

A bare smoke test: if this returns rows, export is flowing and ingestion has caught up.
Empty (or `Failed to resolve table 'dependencies'`) means either the first data hasn't
landed yet (give it 5–10 minutes) or you're querying the wrong schema/workspace — see the
schema note above.

## 1. Route distribution — what kind of traffic are we getting?

```kusto
dependencies
| where name == "graph.run"
| extend route = tostring(customDimensions["ada.route"])
| summarize requests = count() by route, bin(timestamp, 1h)
```

Each request emits one `graph.run` span tagged with the final route: `data_retrieval`
(simple), `analysis` (diagnostic), `refuse` (out of scope), or `pii_refuse` (blocked by
the entitlement gate). Grouped by route over time, this is the traffic mix — and since the
`analysis` path is the expensive one, it tells you whether cost pressure is rare or common.

## 2. Per-tier cost — is the strong model actually reserved?

```kusto
dependencies
| where name startswith "chat "
| extend tier = tostring(customDimensions["ada.model_tier"]),
         cost = todouble(customDimensions["gen_ai.usage.cost_usd"])
| summarize calls = count(), est_cost_usd = round(sum(cost), 4) by tier
```

Every model call is a `chat {model}` span carrying its tier and estimated USD cost.
Summed by tier, this is the proof of the tiering decision: `strong` (gpt-4.1) spend should
concentrate on routing and diagnosis, while `cheap` (gpt-4o-mini) carries the simple-path
volume. Note this is the app's *estimate* (Global Standard prices, input treated as an
upper bound because cached prefixes aren't discounted); reconcile against Foundry's metered
`InputTokens` / `OutputTokens` in the same workspace for the billed figure.

## 3. Per-node latency — where does the time go?

```kusto
dependencies
| where name startswith "node."
| summarize p50 = percentile(duration, 50), p95 = percentile(duration, 95),
            n = count() by name
| order by p95 desc
```

Each graph node emits a span. p50/p95 of duration per node shows where latency sits —
`node.analysis` (strong-tier diagnosis = two model calls) and `node.data_retrieval`
(generate + execute + synthesize) usually top the list, while `node.router` and
`node.compliance` are near-instant. The p95 column is what to watch for latency SLOs; a
large p95-over-p50 gap flags tail latency worth investigating.
