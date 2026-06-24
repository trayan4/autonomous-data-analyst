# Observability — Azure Monitor trace export

Ships the application's OpenTelemetry spans (the `graph.run` root, the `node.*` spans,
and the `chat {model}` GenAI spans) to Azure Monitor / Application Insights. This is the
**application** layer; it complements the **platform** metrics your Foundry diagnostic
settings already emit (metered tokens, requests, throttling, latency per deployment).
Point both at the **same Log Analytics workspace** to correlate them.

## Enable

1. Install the exporter (optional dependency): `pip install azure-monitor-opentelemetry-exporter`
2. **You need an Application Insights resource — a Log Analytics workspace alone is not a
   destination this exporter can reach.** If you only have a workspace, create a
   **workspace-based** Application Insights resource and point it at that existing
   workspace (Portal → Create resource → Application Insights → Resource Mode:
   Workspace-based → select your workspace). Its telemetry then lands *in* your workspace,
   alongside the Foundry diagnostics — no separate data store.
3. Get the connection string: Portal → that Application Insights resource → Overview →
   **Connection String** (the full string, not just the instrumentation key).
4. Set environment (in `.env` or the shell):
   ```
   ADA_TRACE=azure
   APPLICATIONINSIGHTS_CONNECTION_STRING=InstrumentationKey=...;IngestionEndpoint=https://...
   ```
5. Run anything that goes through the graph, e.g. `ada-eval` or
   `ada-ask "Why did sales drop last month?"`. Spans are
   batched and flushed on exit (the entry points call `tracing.shutdown()`).
6. Wait 2–5 minutes for ingestion before querying — an immediate query often looks empty
   even when export is working.

## Where spans land

Our spans are internal, so in a workspace-based App Insights they appear in
**`AppDependencies`** (classic Logs alias: `dependencies`). Span attributes land in the
dynamic **`Properties`** column (classic: `customDimensions`):

- `graph.run` — one per request; `Properties["ada.route"]`, `Properties["ada.scope"]`
- `node.*` — one per node traversed (`node.router`, `node.compliance`,
  `node.data_retrieval`, `node.analysis`, `node.refuse`, `node.pii_refuse`)
- `chat {model}` — one per model call; `gen_ai.request.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`, `gen_ai.usage.cost_usd`, `ada.model_tier`

## Two schemas: workspace vs classic

The same spans are queryable two ways, with different table/column names:

| Querying from… | Table | Name col | Duration col | Attributes | Time col |
|---|---|---|---|---|---|
| **Log Analytics workspace** Logs | `AppDependencies` | `Name` | `DurationMs` | `Properties[...]` | `TimeGenerated` |
| **Application Insights resource** Logs (classic) | `dependencies` | `name` | `duration` | `customDimensions[...]` | `timestamp` |

The KQL below uses the workspace schema. If you're in the App Insights resource's Logs
blade, swap to the classic names (e.g. `dependencies | where name startswith "node."`).

## Sample KQL

Route distribution (which questions go simple vs diagnostic vs refused):
```kusto
AppDependencies
| where Name == "graph.run"
| extend route = tostring(Properties["ada.route"])
| summarize requests = count() by route, bin(TimeGenerated, 1h)
```

Estimated cost and tokens by tier and model (the tiering story, from app traces):
```kusto
AppDependencies
| where Name startswith "chat "
| extend tier  = tostring(Properties["ada.model_tier"]),
         model = tostring(Properties["gen_ai.request.model"]),
         in_t  = toint(Properties["gen_ai.usage.input_tokens"]),
         out_t = toint(Properties["gen_ai.usage.output_tokens"]),
         cost  = todouble(Properties["gen_ai.usage.cost_usd"])
| summarize calls = count(), input = sum(in_t), output = sum(out_t),
            est_cost_usd = round(sum(cost), 4) by tier, model
```

Latency per node (where the time goes):
```kusto
AppDependencies
| where Name startswith "node."
| summarize p50 = percentile(DurationMs, 50), p95 = percentile(DurationMs, 95),
            n = count() by Name
| order by p95 desc
```

## Reconcile app cost vs platform billing

The `chat` spans carry **our estimated** cost (Global Standard prices, input treated as an
upper bound because cached prefixes aren't discounted). Your Foundry diagnostic metrics
carry **metered** `InputTokens` / `OutputTokens` per deployment — the billing truth.
Comparing the two in the same workspace validates the cost model and surfaces the caching
discount. Per-model platform metrics come from splitting on `ModelDeploymentName` /
`ModelName` (the Models metric namespace), not from per-deployment diagnostic settings.

## Verify it's flowing

A minute or two after a run, this should return rows:
```kusto
AppDependencies | where Name startswith "node." | take 20
```
