-- schema.sql - Azure SQL Database schema for the Autonomous Data Analyst warehouse.
-- Tables are loaded from data/raw/*.csv, produced by generate_data.py.
-- This DDL is also the source the semantic layer / catalog is built from (phase 1b).

CREATE TABLE dbo.customers (
    customer_id   INT           NOT NULL PRIMARY KEY,
    full_name     NVARCHAR(120) NOT NULL,
    email         NVARCHAR(200) NOT NULL,   -- PII: gated by RBAC (UC-C)
    phone         NVARCHAR(20)  NOT NULL,   -- PII: gated by RBAC (UC-C)
    region        NVARCHAR(20)  NOT NULL,
    segment       NVARCHAR(20)  NOT NULL,
    signup_date   DATE          NOT NULL
);

CREATE TABLE dbo.products (
    product_id    INT           NOT NULL PRIMARY KEY,
    category      NVARCHAR(40)  NOT NULL,
    subcategory   NVARCHAR(40)  NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL
);

CREATE TABLE dbo.orders (
    order_id      INT           NOT NULL PRIMARY KEY,
    customer_id   INT           NOT NULL REFERENCES dbo.customers(customer_id),
    order_date    DATE          NOT NULL,
    region        NVARCHAR(20)  NOT NULL,
    channel       NVARCHAR(20)  NOT NULL,
    status        NVARCHAR(20)  NOT NULL    -- completed | returned | cancelled
);

CREATE TABLE dbo.order_items (
    order_item_id INT           NOT NULL PRIMARY KEY,
    order_id      INT           NOT NULL REFERENCES dbo.orders(order_id),
    product_id    INT           NOT NULL REFERENCES dbo.products(product_id),
    category      NVARCHAR(40)  NOT NULL,   -- denormalised for analytic queries
    region        NVARCHAR(20)  NOT NULL,   -- denormalised (= order region)
    channel       NVARCHAR(20)  NOT NULL,   -- denormalised (= order channel)
    order_date    DATE          NOT NULL,
    quantity      INT           NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL,
    discount      DECIMAL(4,2)  NOT NULL,
    line_gmv      DECIMAL(12,2) NOT NULL    -- quantity * unit_price * (1 - discount)
);

CREATE TABLE dbo.marketing_spend (
    month     DATE          NOT NULL,       -- first day of the month
    region    NVARCHAR(20)  NOT NULL,
    channel   NVARCHAR(20)  NOT NULL,
    spend     DECIMAL(12,2) NOT NULL,
    CONSTRAINT pk_marketing_spend PRIMARY KEY (month, region, channel)
);

CREATE TABLE dbo.inventory_snapshots (
    snapshot_date  DATE          NOT NULL,
    region         NVARCHAR(20)  NOT NULL,
    category       NVARCHAR(40)  NOT NULL,
    units_in_stock INT           NOT NULL,
    CONSTRAINT pk_inventory PRIMARY KEY (snapshot_date, region, category)
);

-- indexes for the analytical queries the Data Retrieval agent will generate
CREATE INDEX ix_order_items_date   ON dbo.order_items(order_date);
CREATE INDEX ix_order_items_region ON dbo.order_items(region, category);
CREATE INDEX ix_orders_date        ON dbo.orders(order_date);
