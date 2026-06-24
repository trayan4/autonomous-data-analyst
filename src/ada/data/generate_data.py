"""
Synthetic e-commerce dataset with a documented, injected anomaly.

Produces six CSV tables under ./raw/ for the Autonomous Data Analyst project.
Deterministic (fixed seed) so the golden-set ground truth is stable across runs.

GROUND TRUTH for use case UC-A ("why did sales drop last month?"):
  Target month : 2026-05 (the last full month in the data).
  Primary cause: an Electronics stockout in the South region, ~2026-05-08..05-25
                 (90% of South x Electronics orders lost in that window).
  Secondary    : a paused South-region Paid Search campaign for all of 2026-05
                 (~70% of those orders lost).
  See docs/anomaly-spec.md for the exact decomposition measured from this run.
"""

from __future__ import annotations
import pathlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
from faker import Faker

# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
SEED = 42
rng = np.random.default_rng(SEED)
Faker.seed(SEED)
fake = Faker()

OUT = pathlib.Path("data/raw")
OUT.mkdir(exist_ok=True)

START = date(2024, 6, 1)
END = date(2026, 5, 31)          # last full month = May 2026

REGIONS = np.array(["North", "South", "East", "West"])
REGION_W = np.array([0.30, 0.28, 0.22, 0.20])

CHANNELS = np.array(["Paid Search", "Organic", "Email", "Social", "Direct"])
CHANNEL_W = np.array([0.30, 0.25, 0.15, 0.15, 0.15])

SEGMENTS = np.array(["New", "Returning", "VIP"])
SEGMENT_W = np.array([0.45, 0.40, 0.15])

CATS = ["Electronics", "Apparel", "Home", "Beauty", "Sports"]
CAT_W = np.array([0.20, 0.26, 0.21, 0.18, 0.15])
SUBCATS = {
    "Electronics": ["Phones", "Laptops", "Audio"],
    "Apparel": ["Men", "Women", "Kids"],
    "Home": ["Kitchen", "Furniture", "Decor"],
    "Beauty": ["Skincare", "Makeup", "Fragrance"],
    "Sports": ["Fitness", "Outdoor", "Cycling"],
}
# (median price, lognormal sigma) per category
CAT_PRICE = {
    "Electronics": (240, 0.5),
    "Apparel": (48, 0.5),
    "Home": (95, 0.55),
    "Beauty": (32, 0.5),
    "Sports": (65, 0.5),
}
# month -> seasonal multiplier (May is baseline 1.0 so the drop reads as anomaly)
MONTHLY = {1: 0.95, 2: 0.90, 3: 1.00, 4: 1.00, 5: 1.00, 6: 1.00,
           7: 1.00, 8: 1.02, 9: 1.05, 10: 1.10, 11: 1.30, 12: 1.35}

N_CUST = 12_000
N_PROD = 600
AVG_ORDERS = 300                 # average orders/day before trend & seasonality

# anomaly windows
STOCKOUT_FROM = pd.Timestamp(2026, 5, 1)
STOCKOUT_TO = pd.Timestamp(2026, 5, 31)
ANOMALY_YEAR, ANOMALY_MONTH = 2026, 5

# --------------------------------------------------------------------------- #
# dimensions: customers & products
# --------------------------------------------------------------------------- #
cust_region = rng.choice(len(REGIONS), N_CUST, p=REGION_W)
cust_seg = rng.choice(len(SEGMENTS), N_CUST, p=SEGMENT_W)
signup = [START - timedelta(days=int(x)) for x in rng.integers(0, 1000, N_CUST)]
names = [fake.name() for _ in range(N_CUST)]
emails = [f"{n.lower().replace(' ', '.').replace(',', '').replace('.', '.', 1)}{i}@example.com"
          for i, n in enumerate(names)]
phone_nums = rng.integers(2_000_000_000, 9_999_999_999, N_CUST)
phones = [f"+1{n}" for n in phone_nums]

customers = pd.DataFrame({
    "customer_id": np.arange(1, N_CUST + 1),
    "full_name": names,
    "email": emails,                       # PII - used by UC-C (RBAC/redaction)
    "phone": phones,                        # PII - used by UC-C
    "region": REGIONS[cust_region],
    "segment": SEGMENTS[cust_seg],
    "signup_date": signup,
})

prod_cat = rng.choice(len(CATS), N_PROD, p=CAT_W)
prod_cats = [CATS[i] for i in prod_cat]
prod_subs = [str(rng.choice(SUBCATS[c])) for c in prod_cats]
prod_prices = np.array([rng.lognormal(np.log(CAT_PRICE[c][0]), CAT_PRICE[c][1])
                        for c in prod_cats]).round(2)
products = pd.DataFrame({
    "product_id": np.arange(1, N_PROD + 1),
    "category": prod_cats,
    "subcategory": prod_subs,
    "unit_price": prod_prices,
})

# lookups for fast sampling
region_cust = {ri: customers.loc[cust_region == ri, "customer_id"].to_numpy()
               for ri in range(len(REGIONS))}
prod_ids_by_cat = {ci: products.loc[products["category"] == CATS[ci], "product_id"].to_numpy()
                   for ci in range(len(CATS))}
prod_price_by_cat = {ci: products.loc[products["category"] == CATS[ci], "unit_price"].to_numpy()
                     for ci in range(len(CATS))}

# --------------------------------------------------------------------------- #
# facts: orders & order_items (baseline, no anomaly yet)
# --------------------------------------------------------------------------- #
days = [START + timedelta(days=i) for i in range((END - START).days + 1)]
order_frames, item_frames = [], []
oid = 0

for d in days:
    di = (d - START).days
    trend = 1 + 0.00022 * di
    weekly = 1.15 if d.weekday() >= 5 else 1.0
    lam = AVG_ORDERS * trend * weekly * MONTHLY[d.month]
    n = rng.poisson(lam)
    if n == 0:
        continue

    r_idx = rng.choice(len(REGIONS), n, p=REGION_W)
    ch_idx = rng.choice(len(CHANNELS), n, p=CHANNEL_W)
    status = rng.choice(["completed", "returned", "cancelled"], n, p=[0.92, 0.06, 0.02])

    cust = np.empty(n, dtype=int)
    for ri in range(len(REGIONS)):
        m = r_idx == ri
        k = int(m.sum())
        if k:
            cust[m] = rng.choice(region_cust[ri], k)

    order_ids = np.arange(oid + 1, oid + n + 1)
    oid += n
    order_frames.append(pd.DataFrame({
        "order_id": order_ids,
        "customer_id": cust,
        "order_date": d,
        "region": REGIONS[r_idx],
        "channel": CHANNELS[ch_idx],
        "status": status,
    }))

    n_items = rng.integers(1, 4, n)
    rep_order = np.repeat(order_ids, n_items)
    rep_region = np.repeat(r_idx, n_items)
    rep_channel = np.repeat(ch_idx, n_items)
    tot = rep_order.size

    it_cat = rng.choice(len(CATS), tot, p=CAT_W)
    prod = np.empty(tot, dtype=int)
    uprice = np.empty(tot)
    for ci in range(len(CATS)):
        m = it_cat == ci
        k = int(m.sum())
        if k:
            sel = rng.choice(len(prod_ids_by_cat[ci]), k)
            prod[m] = prod_ids_by_cat[ci][sel]
            uprice[m] = prod_price_by_cat[ci][sel]

    qty = rng.integers(1, 4, tot)
    disc = rng.choice([0.0, 0.1, 0.2], tot, p=[0.7, 0.2, 0.1])
    gmv = (qty * uprice * (1 - disc)).round(2)

    item_frames.append(pd.DataFrame({
        "order_id": rep_order,
        "product_id": prod,
        "category": [CATS[c] for c in it_cat],
        "region": REGIONS[rep_region],
        "channel": CHANNELS[rep_channel],
        "order_date": d,
        "quantity": qty,
        "unit_price": uprice.round(2),
        "discount": disc,
        "line_gmv": gmv,
    }))

orders = pd.concat(order_frames, ignore_index=True)
items = pd.concat(item_frames, ignore_index=True)
orders["order_date"] = pd.to_datetime(orders["order_date"])
items["order_date"] = pd.to_datetime(items["order_date"])

baseline_may_gmv = items.loc[(items.order_date.dt.year == ANOMALY_YEAR) &
                             (items.order_date.dt.month == ANOMALY_MONTH), "line_gmv"].sum()

# --------------------------------------------------------------------------- #
# inject the anomaly (explicit, auditable transforms)
# --------------------------------------------------------------------------- #
# (a) Electronics stockout in South, 2026-05-08..05-25: drop 90% of those items.
win = (items.order_date >= STOCKOUT_FROM) & (items.order_date <= STOCKOUT_TO)
stock_mask = win & (items.region == "South") & (items.category == "Electronics")
drop_stock = items[stock_mask].sample(frac=0.95, random_state=SEED).index
items = items.drop(drop_stock)

# (b) Paused South Paid Search campaign for all of 2026-05: drop 70% of orders.
may = (orders.order_date.dt.year == ANOMALY_YEAR) & (orders.order_date.dt.month == ANOMALY_MONTH)
ps_mask = may & (orders.region == "South") & (orders.channel == "Paid Search")
drop_orders = orders[ps_mask].sample(frac=0.80, random_state=SEED)["order_id"]
orders = orders[~orders.order_id.isin(drop_orders)]
items = items[~items.order_id.isin(drop_orders)]

# remove orders left with no items
orders = orders[orders.order_id.isin(items.order_id.unique())]
items = items.reset_index(drop=True)
items.insert(0, "order_item_id", np.arange(1, len(items) + 1))

# --------------------------------------------------------------------------- #
# correlating signals: marketing_spend & inventory_snapshots
# --------------------------------------------------------------------------- #
ms_rows = []
for p in pd.period_range(START, END, freq="M"):
    for ri, rg in enumerate(REGIONS):
        for ci, ch in enumerate(CHANNELS):
            base = 18_000 * REGION_W[ri] * CHANNEL_W[ci]
            spend = base * MONTHLY[p.month] * rng.uniform(0.9, 1.1)
            if ch == "Paid Search" and rg == "South" and p.year == ANOMALY_YEAR and p.month == ANOMALY_MONTH:
                spend *= 0.03                      # campaign paused
            ms_rows.append((p.to_timestamp().date(), rg, ch, round(spend, 2)))
marketing_spend = pd.DataFrame(ms_rows, columns=["month", "region", "channel", "spend"])

inv_rows = []
d = START
while d <= END:
    for rg in REGIONS:
        for cat in CATS:
            units = int(rng.integers(200, 2000))
            if rg == "South" and cat == "Electronics" and STOCKOUT_FROM <= pd.Timestamp(d) <= STOCKOUT_TO:
                units = 0                          # stockout
            inv_rows.append((d, rg, cat, units))
    d += timedelta(days=1)
inventory = pd.DataFrame(inv_rows, columns=["snapshot_date", "region", "category", "units_in_stock"])

# --------------------------------------------------------------------------- #
# write & verify
# --------------------------------------------------------------------------- #
customers.to_csv(OUT / "customers.csv", index=False)
products.to_csv(OUT / "products.csv", index=False)
orders.to_csv(OUT / "orders.csv", index=False)
items.to_csv(OUT / "order_items.csv", index=False)
marketing_spend.to_csv(OUT / "marketing_spend.csv", index=False)
inventory.to_csv(OUT / "inventory_snapshots.csv", index=False)

# ---- verification report ----
m = items.copy()
m["month"] = m.order_date.dt.to_period("M")
monthly = m.groupby("month")["line_gmv"].sum()
trail = monthly.loc[["2026-02", "2026-03", "2026-04"]].mean()
may_gmv = monthly.loc["2026-05"]
drop_pct = (may_gmv - trail) / trail * 100

# decomposition vs the trailing-3-month baseline, by region x category
def seg_gmv(period, keys):
    sub = m[m.month == period]
    return sub.groupby(keys)["line_gmv"].sum()

trail_reg = pd.concat([seg_gmv("2026-02", "region"), seg_gmv("2026-03", "region"),
                       seg_gmv("2026-04", "region")], axis=1).mean(axis=1)
may_reg = seg_gmv("2026-05", "region")
reg_delta = (may_reg - trail_reg)

trail_seg = pd.concat([seg_gmv("2026-02", ["region", "category"]),
                       seg_gmv("2026-03", ["region", "category"]),
                       seg_gmv("2026-04", ["region", "category"])], axis=1).mean(axis=1)
may_seg = seg_gmv("2026-05", ["region", "category"])
delta = (may_seg - trail_seg).sort_values()
gross_declines = -delta[delta < 0].sum()          # sum of negative movements only

print("=" * 66)
print(f"rows: customers={len(customers):,}  products={len(products):,}  "
      f"orders={len(orders):,}  order_items={len(items):,}")
print(f"      marketing_spend={len(marketing_spend):,}  inventory={len(inventory):,}")
print("-" * 66)
print("Monthly GMV $000 (last 6 months):")
print((monthly.tail(6) / 1000).round(1).to_string())
print("-" * 66)
print(f"Trailing 3-month avg (Feb-Apr 2026): {trail:>12,.0f}")
print(f"May 2026 GMV                       : {may_gmv:>12,.0f}")
print(f"Total drop                         : {drop_pct:>11,.1f}%")
print("-" * 66)
print("By region - May 2026 vs trailing 3-month avg:")
for rg, v in reg_delta.sort_values().items():
    pct = v / trail_reg[rg] * 100
    print(f"  {rg:<6} {v:>12,.0f}   ({pct:>5.1f}%)")
print("-" * 66)
print("Largest single drivers of the decline (region x category):")
for (rg, cat), v in delta.head(4).items():
    share = -v / gross_declines * 100
    print(f"  {rg:<6} x {cat:<12} {v:>12,.0f}   ({share:4.0f}% of all declines)")
print("-" * 66)
# evidence trail for the two documented causes
sps_apr = orders[(orders.order_date.dt.to_period('M') == pd.Period('2026-04')) &
                 (orders.region == 'South') & (orders.channel == 'Paid Search')].shape[0]
sps_may = orders[(orders.order_date.dt.to_period('M') == pd.Period('2026-05')) &
                 (orders.region == 'South') & (orders.channel == 'Paid Search')].shape[0]
print(f"South Paid Search orders : Apr={sps_apr:,}  May={sps_may:,}  "
      f"({(sps_may/sps_apr-1)*100:.0f}%)  <- campaign pause")
print(f"South Electronics inventory in May: "
      f"{inventory[(inventory.region=='South')&(inventory.category=='Electronics')&(inventory.snapshot_date.astype(str).str.startswith('2026-05'))]['units_in_stock'].sum()} units  <- stockout")
print("=" * 66)
