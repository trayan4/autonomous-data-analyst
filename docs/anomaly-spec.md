# Anomaly Specification — UC-A Ground Truth

This document is the **known ground truth** for use case UC-A ("why did sales drop
last month?"). The dataset is generated, not real, precisely so the cause is known
and the agent's answer can be scored. Numbers below are measured from the
deterministic run of `data/generate_data.py` (SEED = 42).

## The dataset

Six tables, loaded from `data/raw/*.csv` (schema in `data/schema.sql`):

| Table | Grain | Rows |
|---|---|---|
| customers | one row per customer (incl. PII: email, phone) | 12,000 |
| products | one row per product | 600 |
| orders | one row per order | 259,383 |
| order_items | one row per line item | 518,307 |
| marketing_spend | month × region × channel | 480 |
| inventory_snapshots | day × region × category | 14,600 |

Span: 2024-06-01 → 2026-05-31. Dimensions: region {North, South, East, West},
category {Electronics, Apparel, Home, Beauty, Sports}, channel {Paid Search,
Organic, Email, Social, Direct}. Baseline has trend, weekly and monthly
seasonality, and noise. **Target month for UC-A: 2026-05** (the last full month).

## The injected anomaly (two documented causes)

Applied as explicit, auditable transforms in the generator:

1. **PRIMARY — Electronics stockout in the South region, all of May 2026.**
   95% of `South × Electronics` line items in May are removed (lost sales).
   Evidence trail: `inventory_snapshots` shows `units_in_stock = 0` for
   South × Electronics across May.
2. **SECONDARY — paused Paid Search campaign in the South region, all of May 2026.**
   80% of South Paid Search orders in May are removed.
   Evidence trail: `marketing_spend` shows South Paid Search spend collapsing to
   ~3% of normal for May; South Paid Search order count falls from 951 (Apr) to
   181 (May), −81%.

## The measured effect

| Metric | Value |
|---|---|
| Trailing 3-month avg GMV (Feb–Apr 2026) | $4,106,321 |
| May 2026 GMV | $3,796,476 |
| **Total drop** | **−7.5%** |

**By region (May vs trailing avg):** the decline is concentrated entirely in South.

| Region | Δ GMV | % |
|---|---|---|
| South | −651,229 | −56.0% |
| North | +139,553 | +11.3% |
| East | +110,557 | +12.3% |
| West | +91,273 | +11.2% |

**Largest single drivers (region × category), share of all segment declines:**

| Segment | Δ GMV | Share |
|---|---|---|
| South × Electronics | −547,296 | 84% |
| South × Home | −41,020 | 6% |
| South × Apparel | −27,394 | 4% |
| South × Sports | −19,256 | 3% |

## Expected answer (what UC-A scores against)

A correct response must establish, grounded in query results:

1. Overall GMV fell ~7.5% month-over-month vs the trailing 3-month average.
2. The decline is **concentrated in the South region** (−56%); North, East, and
   West each *grew* ~11–12%. (Recognising the concentration is the key insight —
   the small headline number hides a large localised problem.)
3. **Primary cause: an Electronics stockout in South** — South × Electronics is
   ~84% of the total decline, and South Electronics inventory was 0 through May.
4. **Secondary cause: the paused South Paid Search campaign** — orders −81% MoM,
   spend ≈ 0.
5. No other region or category is materially affected.

Scoring (per `docs/SCOPE.md`): pass requires the agent to identify the **South
Electronics stockout as the primary driver** with every cited number traceable to
a query result (groundedness ≥ 0.95). Naming the regional concentration and the
secondary campaign cause are full-credit signals.

## Regenerating

```bash
cd data
pip install -r requirements.txt
python generate_data.py      # writes data/raw/*.csv, prints the verification report
```

The run is deterministic; the numbers above will reproduce exactly.
