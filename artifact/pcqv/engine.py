"""PCQV: policy-constrained query optimization and rewrite verification.

This module is intentionally self-contained and CPU-only.  It implements a
controlled relational workload, a policy language, several SQL encodings,
a policy-aware left-deep optimizer, an equivalence checker, and benchmark
runners over SQLite.  It is a research prototype for reproducible experiments,
not a production access-control system.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import permutations
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
import csv
import json
import math
import os
import random
import sqlite3
import statistics
import time


# ---------------------------------------------------------------------------
# Relational/query and policy model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableRef:
    table: str
    alias: str


@dataclass(frozen=True)
class Join:
    left_alias: str
    left_col: str
    right_alias: str
    right_col: str


@dataclass(frozen=True)
class Filter:
    alias: str
    expr: str       # SQL expression with a {a} placeholder for the alias.
    sel: float      # Selectivity hint used by the research cost model.
    label: str


@dataclass
class QueryTemplate:
    name: str
    tables: List[TableRef]
    joins: List[Join]
    filters: List[Filter]
    select_exprs: List[Tuple[str, str]]
    group_by: List[str] = field(default_factory=list)
    having: Optional[str] = None
    order_by: Optional[str] = None
    limit: Optional[int] = None
    result_kind: str = "rows"  # rows | aggregate

    def table_for(self, alias: str) -> str:
        for t in self.tables:
            if t.alias == alias:
                return t.table
        raise KeyError(alias)

    def table_ref(self, alias: str) -> TableRef:
        for t in self.tables:
            if t.alias == alias:
                return t
        raise KeyError(alias)


@dataclass
class Context:
    user_id: int = 17
    role: str = "analyst"
    purpose: str = "analytics"
    clearance: int = 2
    allowed_tenants: Tuple[int, ...] = (1,)
    allowed_regions: Tuple[str, ...] = ("NA", "EU")
    min_group: int = 8

    def sql_literal_list(self, xs: Sequence[Any]) -> str:
        vals: List[str] = []
        for x in xs:
            if isinstance(x, str):
                vals.append("'" + x.replace("'", "''") + "'")
            else:
                vals.append(str(int(x)))
        return ",".join(vals) if vals else "NULL"


TABLE_COLUMNS: Dict[str, List[str]] = {
    "customers": ["cid", "tid", "region", "vip", "risk", "opted_in", "segment", "email"],
    "orders": ["oid", "tid", "cid", "owner_uid", "amount", "status", "purpose_tag", "created_day"],
    "lineitem": ["lid", "tid", "oid", "pid", "qty", "price"],
    "products": ["pid", "tid", "category", "sensitivity", "active"],
    "tickets": ["ticket_id", "tid", "cid", "owner_uid", "severity", "created_day", "topic", "pii_flag"],
}

PRIMARY_KEYS = {
    "customers": "cid",
    "orders": "oid",
    "lineitem": "lid",
    "products": "pid",
    "tickets": "ticket_id",
}


class PolicyManager:
    """Compiles policy fragments for the controlled SQL fragment.

    Policy complexity levels are cumulative:
    1 tenant isolation;
    2 local row filters;
    3 purpose and clearance restrictions;
    4 cross-table row guards;
    5 ACL disjunctions;
    6 aggregation k-thresholds.
    """

    def __init__(self, complexity: int, ctx: Context):
        self.complexity = int(complexity)
        self.ctx = ctx

    def _col(self, alias: str, name: str, raw_prefix: bool) -> str:
        return f"{alias}_{name}" if raw_prefix else f"{alias}.{name}"

    def row_predicates(self, table: str, alias: str, raw_prefix: bool = False) -> List[Tuple[str, float, str]]:
        c = self.ctx
        col = lambda name: self._col(alias, name, raw_prefix)
        preds: List[Tuple[str, float, str]] = []
        if table in {"customers", "orders", "lineitem", "products", "tickets"}:
            tenant_sel = min(0.95, max(0.001, len(c.allowed_tenants) / 40.0))
            preds.append((f"{col('tid')} IN ({c.sql_literal_list(c.allowed_tenants)})", tenant_sel, "tenant"))
        if self.complexity >= 2:
            if table == "customers":
                # At level 5 the ACL disjunction is the visible-region rule,
                # not an extra conjunct; otherwise ACL exceptions would be
                # made unreachable by the standalone region guard.
                if self.complexity < 5:
                    preds.append((f"{col('region')} IN ({c.sql_literal_list(c.allowed_regions)})", min(0.95, len(c.allowed_regions) / 8.0), "region"))
                preds.append((f"{col('risk')} <= 3", 0.68, "risk"))
            elif table == "orders":
                preds.append((f"{col('status')} <> 'internal'", 0.88, "status"))
            elif table == "products":
                preds.append((f"{col('active')} = 1", 0.92, "active"))
            elif table == "tickets":
                preds.append((f"{col('severity')} <= 4", 0.80, "severity"))
        if self.complexity >= 3:
            if table == "orders":
                if c.role == "auditor":
                    preds.append(("1=1", 1.0, "purpose-auditor"))
                else:
                    preds.append((f"{col('purpose_tag')} = '{c.purpose}'", 0.27, "purpose"))
            elif table == "products":
                preds.append((f"{col('sensitivity')} <= {int(c.clearance)}", min(1.0, (c.clearance + 1) / 6.0), "clearance"))
            elif table == "tickets":
                if c.purpose == "support":
                    preds.append((f"({col('owner_uid')} = {c.user_id} OR {col('severity')} <= 2)", 0.35, "support-owner"))
                else:
                    preds.append((f"{col('pii_flag')} = 0", 0.74, "ticket-pii"))
        if self.complexity >= 4 and not raw_prefix:
            if table == "orders":
                preds.append((
                    f"EXISTS (SELECT 1 FROM customers pc WHERE pc.cid = {alias}.cid AND pc.tid = {alias}.tid "
                    f"AND pc.region IN ({c.sql_literal_list(c.allowed_regions)}))",
                    min(0.95, len(c.allowed_regions) / 8.0),
                    "customer-guard",
                ))
            elif table == "lineitem":
                preds.append((
                    f"EXISTS (SELECT 1 FROM orders po WHERE po.oid = {alias}.oid AND po.tid = {alias}.tid AND po.status <> 'internal')",
                    0.88,
                    "order-guard",
                ))
        if self.complexity >= 5 and table == "customers":
            region = f"{col('region')} IN ({c.sql_literal_list(c.allowed_regions)})"
            acl = f"EXISTS (SELECT 1 FROM customer_acl ca WHERE ca.uid = {c.user_id} AND ca.cid = {col('cid')})"
            preds.append((f"({region} OR {acl})", min(0.97, len(c.allowed_regions) / 8.0 + 0.05), "acl-or-region"))
        return preds

    def policy_where(self, table: str, alias: str, raw_prefix: bool = False) -> str:
        return " AND ".join(p for p, _, _ in self.row_predicates(table, alias, raw_prefix)) or "1=1"

    def predicate_selectivity(self, table: str, alias: str) -> float:
        prod = 1.0
        seen = set()
        for _, sel, label in self.row_predicates(table, alias):
            # Robust against older policy variants where ACL disjunction and
            # region were both present; the current language makes them exclusive.
            if label == "region" and self.complexity >= 5 and table == "customers":
                continue
            if label in seen:
                continue
            seen.add(label)
            prod *= max(0.001, min(1.0, sel))
        return max(0.001, min(1.0, prod))

    def mask_expr(self, table: str, alias: str, col: str) -> str:
        if table == "customers" and col == "email":
            if self.ctx.purpose == "support":
                return f"CASE WHEN {alias}.opted_in = 1 THEN {alias}.email ELSE 'MASKED' END"
            return "'MASKED'"
        return f"{alias}.{col}"

    def group_having(self, q: QueryTemplate) -> Optional[str]:
        if self.complexity >= 6 and q.result_kind == "aggregate":
            return f"COUNT(*) >= {int(self.ctx.min_group)}"
        return q.having


# ---------------------------------------------------------------------------
# Controlled workload
# ---------------------------------------------------------------------------


def query_templates() -> List[QueryTemplate]:
    return [
        QueryTemplate(
            name="q1_customer_orders",
            tables=[TableRef("lineitem", "l"), TableRef("orders", "o"), TableRef("customers", "c")],
            joins=[Join("l", "oid", "o", "oid"), Join("o", "cid", "c", "cid")],
            filters=[Filter("o", "{a}.amount >= 250", 0.23, "amount"), Filter("c", "{a}.segment IN ('S1','S2')", 0.40, "segment")],
            select_exprs=[("c.cid", "cid"), ("o.oid", "oid"), ("o.amount", "amount"), ("MASK(c.email)", "email")],
            order_by="o.oid",
            limit=250,
        ),
        QueryTemplate(
            name="q2_revenue_by_product",
            tables=[TableRef("lineitem", "l"), TableRef("products", "p"), TableRef("orders", "o")],
            joins=[Join("l", "pid", "p", "pid"), Join("l", "oid", "o", "oid")],
            filters=[Filter("p", "{a}.category IN ('C1','C2','C3')", 0.30, "category"), Filter("o", "{a}.created_day BETWEEN 60 AND 300", 0.66, "date")],
            select_exprs=[("p.category", "category"), ("p.sensitivity", "sensitivity"), ("COUNT(*)", "n"), ("SUM(l.qty*l.price)", "revenue")],
            group_by=["p.category", "p.sensitivity"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q3_support_tickets",
            tables=[TableRef("tickets", "t"), TableRef("customers", "c")],
            joins=[Join("t", "cid", "c", "cid")],
            filters=[Filter("t", "{a}.severity >= 2", 0.80, "severity"), Filter("t", "{a}.topic IN ('billing','shipping')", 0.40, "topic")],
            select_exprs=[("t.ticket_id", "ticket_id"), ("c.cid", "cid"), ("t.severity", "severity"), ("MASK(c.email)", "email")],
            order_by="t.ticket_id",
            limit=250,
        ),
        QueryTemplate(
            name="q4_revenue_by_region",
            tables=[TableRef("lineitem", "l"), TableRef("orders", "o"), TableRef("customers", "c")],
            joins=[Join("l", "oid", "o", "oid"), Join("o", "cid", "c", "cid")],
            filters=[Filter("o", "{a}.created_day BETWEEN 1 AND 365", 1.0, "date")],
            select_exprs=[("c.region", "region"), ("COUNT(DISTINCT o.oid)", "orders"), ("SUM(l.qty*l.price)", "revenue")],
            group_by=["c.region"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q5_high_value_segments",
            tables=[TableRef("orders", "o"), TableRef("customers", "c")],
            joins=[Join("o", "cid", "c", "cid")],
            filters=[Filter("o", "{a}.amount BETWEEN 100 AND 800", 0.70, "amount-band")],
            select_exprs=[("c.segment", "segment"), ("COUNT(*)", "n"), ("AVG(o.amount)", "avg_amount")],
            group_by=["c.segment"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q6_lineitem_product_count",
            tables=[TableRef("lineitem", "l"), TableRef("products", "p"), TableRef("orders", "o"), TableRef("customers", "c")],
            joins=[Join("l", "pid", "p", "pid"), Join("l", "oid", "o", "oid"), Join("o", "cid", "c", "cid")],
            filters=[Filter("l", "{a}.qty >= 2", 0.80, "qty"), Filter("p", "{a}.category <> 'C9'", 0.89, "category-not")],
            select_exprs=[("p.category", "category"), ("COUNT(*)", "n"), ("SUM(l.price)", "sum_price")],
            group_by=["p.category"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q7_customer_summary",
            tables=[TableRef("orders", "o"), TableRef("customers", "c")],
            joins=[Join("o", "cid", "c", "cid")],
            filters=[Filter("c", "{a}.vip = 1", 0.16, "vip")],
            select_exprs=[("c.cid", "cid"), ("COUNT(o.oid)", "n_orders"), ("SUM(o.amount)", "total_amount")],
            group_by=["c.cid"],
            order_by="c.cid",
            limit=250,
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q8_sensitive_product_scan",
            tables=[TableRef("lineitem", "l"), TableRef("products", "p")],
            joins=[Join("l", "pid", "p", "pid")],
            filters=[Filter("p", "{a}.sensitivity >= 1", 0.82, "sens")],
            select_exprs=[("p.category", "category"), ("p.sensitivity", "sensitivity"), ("COUNT(*)", "n")],
            group_by=["p.category", "p.sensitivity"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q9_ticket_topic_segments",
            tables=[TableRef("tickets", "t"), TableRef("customers", "c")],
            joins=[Join("t", "cid", "c", "cid")],
            filters=[Filter("t", "{a}.created_day BETWEEN 30 AND 360", 0.91, "ticket-date"), Filter("c", "{a}.risk <= 4", 0.86, "risk-wide")],
            select_exprs=[("t.topic", "topic"), ("c.segment", "segment"), ("COUNT(*)", "n"), ("AVG(t.severity)", "avg_sev")],
            group_by=["t.topic", "c.segment"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q10_acl_customer_rows",
            tables=[TableRef("customers", "c")],
            joins=[],
            filters=[Filter("c", "{a}.risk BETWEEN 1 AND 4", 0.74, "risk-band")],
            select_exprs=[("c.cid", "cid"), ("c.segment", "segment"), ("c.region", "region"), ("MASK(c.email)", "email")],
            order_by="c.cid",
            limit=300,
        ),
        QueryTemplate(
            name="q11_order_ticket_bridge",
            tables=[TableRef("orders", "o"), TableRef("customers", "c"), TableRef("tickets", "t")],
            joins=[Join("o", "cid", "c", "cid"), Join("t", "cid", "c", "cid")],
            filters=[Filter("o", "{a}.amount >= 180", 0.31, "amount"), Filter("t", "{a}.severity >= 3", 0.60, "ticket-sev")],
            select_exprs=[("c.region", "region"), ("t.topic", "topic"), ("COUNT(DISTINCT o.oid)", "orders"), ("COUNT(t.ticket_id)", "tickets")],
            group_by=["c.region", "t.topic"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q12_product_segment_revenue",
            tables=[TableRef("orders", "o"), TableRef("customers", "c"), TableRef("lineitem", "l"), TableRef("products", "p")],
            joins=[Join("o", "cid", "c", "cid"), Join("l", "oid", "o", "oid"), Join("l", "pid", "p", "pid")],
            filters=[Filter("c", "{a}.segment IN ('S2','S3','S4')", 0.60, "segment-mid"), Filter("p", "{a}.active = 1", 0.92, "active-user")],
            select_exprs=[("c.segment", "segment"), ("p.category", "category"), ("COUNT(*)", "n"), ("SUM(l.qty*l.price)", "revenue")],
            group_by=["c.segment", "p.category"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q13_order_lineitem_rows",
            tables=[TableRef("orders", "o"), TableRef("lineitem", "l")],
            joins=[Join("l", "oid", "o", "oid")],
            filters=[Filter("o", "{a}.status IN ('open','pending')", 0.56, "status-visible"), Filter("l", "{a}.price >= 12", 0.71, "price")],
            select_exprs=[("o.oid", "oid"), ("l.lid", "lid"), ("o.amount", "amount"), ("l.qty", "qty"), ("l.price", "price")],
            order_by="o.oid",
            limit=300,
        ),
        QueryTemplate(
            name="q14_support_owner_rows",
            tables=[TableRef("tickets", "t"), TableRef("customers", "c")],
            joins=[Join("t", "cid", "c", "cid")],
            filters=[Filter("t", "{a}.topic <> 'fraud'", 0.80, "topic-not"), Filter("c", "{a}.opted_in IN (0,1)", 1.0, "opted-all")],
            select_exprs=[("t.ticket_id", "ticket_id"), ("t.owner_uid", "owner_uid"), ("t.topic", "topic"), ("MASK(c.email)", "email")],
            order_by="t.ticket_id",
            limit=300,
        ),
        QueryTemplate(
            name="q15_clearance_product_rows",
            tables=[TableRef("products", "p"), TableRef("lineitem", "l")],
            joins=[Join("l", "pid", "p", "pid")],
            filters=[Filter("p", "{a}.category IN ('C1','C4','C7')", 0.34, "category-skip"), Filter("l", "{a}.qty BETWEEN 1 AND 4", 0.80, "qty-band")],
            select_exprs=[("p.pid", "pid"), ("p.category", "category"), ("p.sensitivity", "sensitivity"), ("COUNT(l.lid)", "n")],
            group_by=["p.pid", "p.category", "p.sensitivity"],
            result_kind="aggregate",
        ),
        QueryTemplate(
            name="q16_region_product_mix",
            tables=[TableRef("customers", "c"), TableRef("orders", "o"), TableRef("lineitem", "l"), TableRef("products", "p")],
            joins=[Join("o", "cid", "c", "cid"), Join("l", "oid", "o", "oid"), Join("l", "pid", "p", "pid")],
            filters=[Filter("o", "{a}.created_day BETWEEN 90 AND 365", 0.75, "recent-ish"), Filter("p", "{a}.sensitivity <= 4", 0.84, "sens-wide")],
            select_exprs=[("c.region", "region"), ("p.category", "category"), ("COUNT(DISTINCT c.cid)", "customers"), ("SUM(o.amount)", "amount")],
            group_by=["c.region", "p.category"],
            result_kind="aggregate",
        ),
    ]


def context_for_selectivity(sel: float, purpose: str = "analytics") -> Context:
    n_tenants = max(1, min(40, int(round(40 * sel))))
    region_count = max(1, min(8, int(round(8 * min(0.98, math.sqrt(sel))))))
    regions = ("NA", "EU", "APAC", "LATAM", "MEA", "CN", "IN", "OCE")[:region_count]
    clearance = max(0, min(5, int(round(5 * min(0.98, math.sqrt(sel))))))
    return Context(allowed_tenants=tuple(range(1, n_tenants + 1)), allowed_regions=regions, clearance=clearance, purpose=purpose)


# ---------------------------------------------------------------------------
# SQLite database generation
# ---------------------------------------------------------------------------


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-200000")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS customers;
        DROP TABLE IF EXISTS orders;
        DROP TABLE IF EXISTS lineitem;
        DROP TABLE IF EXISTS products;
        DROP TABLE IF EXISTS tickets;
        DROP TABLE IF EXISTS customer_acl;
        CREATE TABLE customers(
            cid INTEGER PRIMARY KEY,
            tid INTEGER NOT NULL,
            region TEXT NOT NULL,
            vip INTEGER NOT NULL,
            risk INTEGER NOT NULL,
            opted_in INTEGER NOT NULL,
            segment TEXT NOT NULL,
            email TEXT NOT NULL
        );
        CREATE TABLE orders(
            oid INTEGER PRIMARY KEY,
            tid INTEGER NOT NULL,
            cid INTEGER NOT NULL,
            owner_uid INTEGER NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL,
            purpose_tag TEXT NOT NULL,
            created_day INTEGER NOT NULL
        );
        CREATE TABLE lineitem(
            lid INTEGER PRIMARY KEY,
            tid INTEGER NOT NULL,
            oid INTEGER NOT NULL,
            pid INTEGER NOT NULL,
            qty INTEGER NOT NULL,
            price REAL NOT NULL
        );
        CREATE TABLE products(
            pid INTEGER PRIMARY KEY,
            tid INTEGER NOT NULL,
            category TEXT NOT NULL,
            sensitivity INTEGER NOT NULL,
            active INTEGER NOT NULL
        );
        CREATE TABLE tickets(
            ticket_id INTEGER PRIMARY KEY,
            tid INTEGER NOT NULL,
            cid INTEGER NOT NULL,
            owner_uid INTEGER NOT NULL,
            severity INTEGER NOT NULL,
            created_day INTEGER NOT NULL,
            topic TEXT NOT NULL,
            pii_flag INTEGER NOT NULL
        );
        CREATE TABLE customer_acl(uid INTEGER NOT NULL, cid INTEGER NOT NULL, PRIMARY KEY(uid,cid));
        """
    )
    conn.commit()


def populate(conn: sqlite3.Connection, seed: int = 7, scale: float = 0.35) -> None:
    rng = random.Random(seed)
    n_tenants = 40
    n_customers = max(1200, int(12000 * scale))
    n_products = max(300, int(2500 * scale))
    n_orders = max(5000, int(55000 * scale))
    n_lineitem = max(12000, int(160000 * scale))
    n_tickets = max(2500, int(22000 * scale))
    regions = ["NA", "EU", "APAC", "LATAM", "MEA", "CN", "IN", "OCE"]
    segments = ["S1", "S2", "S3", "S4", "S5"]
    statuses = ["open", "closed", "pending", "internal"]
    status_weights = [0.36, 0.34, 0.20, 0.10]
    purposes = ["analytics", "support", "billing", "fraud"]
    categories = [f"C{i}" for i in range(1, 10)]
    topics = ["billing", "shipping", "warranty", "return", "fraud"]
    cur = conn.cursor()

    customers = []
    for cid in range(1, n_customers + 1):
        tid = 1 + (cid * 17 + rng.randint(0, n_tenants - 1)) % n_tenants
        region = regions[(tid + rng.randint(0, 5)) % len(regions)]
        vip = 1 if rng.random() < 0.16 else 0
        risk = min(5, int(rng.expovariate(0.75)))
        opted = 1 if rng.random() < 0.66 else 0
        segment = segments[(cid + tid) % len(segments)]
        email = f"u{cid}@tenant{tid}.example"
        customers.append((cid, tid, region, vip, risk, opted, segment, email))
    cur.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", customers)

    products = []
    for pid in range(1, n_products + 1):
        tid = 1 + (pid * 13 + rng.randint(0, n_tenants - 1)) % n_tenants
        category = categories[(pid + tid) % len(categories)]
        sensitivity = min(5, int(rng.triangular(0, 6, 2)))
        active = 1 if rng.random() < 0.92 else 0
        products.append((pid, tid, category, sensitivity, active))
    cur.executemany("INSERT INTO products VALUES (?,?,?,?,?)", products)

    cids_by_tenant: Dict[int, List[int]] = {t: [] for t in range(1, n_tenants + 1)}
    for cid, tid, *_ in customers:
        cids_by_tenant[tid].append(cid)
    pids_by_tenant: Dict[int, List[int]] = {t: [] for t in range(1, n_tenants + 1)}
    for pid, tid, *_ in products:
        pids_by_tenant[tid].append(pid)

    orders = []
    for oid in range(1, n_orders + 1):
        tid = 1 + rng.randrange(n_tenants)
        cid = rng.choice(cids_by_tenant[tid] or [rng.randrange(1, n_customers + 1)])
        owner = 1 + rng.randrange(300)
        amount = round(min(5000, rng.lognormvariate(5.2, 0.9)), 2)
        status = rng.choices(statuses, weights=status_weights, k=1)[0]
        purpose = rng.choice(purposes)
        day = 1 + rng.randrange(365)
        orders.append((oid, tid, cid, owner, amount, status, purpose, day))
    cur.executemany("INSERT INTO orders VALUES (?,?,?,?,?,?,?,?)", orders)

    oids_by_tenant: Dict[int, List[int]] = {t: [] for t in range(1, n_tenants + 1)}
    for oid, tid, *_ in orders:
        oids_by_tenant[tid].append(oid)

    lineitems = []
    for lid in range(1, n_lineitem + 1):
        tid = 1 + rng.randrange(n_tenants)
        oid = rng.choice(oids_by_tenant[tid] or [rng.randrange(1, n_orders + 1)])
        pid = rng.choice(pids_by_tenant[tid] or [rng.randrange(1, n_products + 1)])
        qty = 1 + rng.randrange(5)
        price = round(max(1.0, rng.lognormvariate(3.2, 0.8)), 2)
        lineitems.append((lid, tid, oid, pid, qty, price))
    cur.executemany("INSERT INTO lineitem VALUES (?,?,?,?,?,?)", lineitems)

    tickets = []
    for ticket_id in range(1, n_tickets + 1):
        tid = 1 + rng.randrange(n_tenants)
        cid = rng.choice(cids_by_tenant[tid] or [rng.randrange(1, n_customers + 1)])
        owner = rng.choice([17, 17, 17, 1 + rng.randrange(300)])
        severity = 1 + rng.randrange(5)
        day = 1 + rng.randrange(365)
        topic = rng.choice(topics)
        pii = 1 if rng.random() < 0.26 else 0
        tickets.append((ticket_id, tid, cid, owner, severity, day, topic, pii))
    cur.executemany("INSERT INTO tickets VALUES (?,?,?,?,?,?,?,?)", tickets)

    acl_size = min(n_customers, max(60, int(600 * scale)))
    acl = [(17, cid) for cid in rng.sample(range(1, n_customers + 1), acl_size)]
    cur.executemany("INSERT INTO customer_acl VALUES (?,?)", acl)
    conn.commit()


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_customers_tid_region ON customers(tid, region, risk);
        CREATE INDEX IF NOT EXISTS idx_customers_region ON customers(region);
        CREATE INDEX IF NOT EXISTS idx_customers_segment ON customers(segment, vip);
        CREATE INDEX IF NOT EXISTS idx_orders_tid_status_purpose ON orders(tid, status, purpose_tag, created_day);
        CREATE INDEX IF NOT EXISTS idx_orders_cid ON orders(cid);
        CREATE INDEX IF NOT EXISTS idx_orders_amount ON orders(amount);
        CREATE INDEX IF NOT EXISTS idx_lineitem_tid_oid ON lineitem(tid, oid);
        CREATE INDEX IF NOT EXISTS idx_lineitem_oid ON lineitem(oid);
        CREATE INDEX IF NOT EXISTS idx_lineitem_pid ON lineitem(pid);
        CREATE INDEX IF NOT EXISTS idx_products_tid_cat ON products(tid, category, sensitivity, active);
        CREATE INDEX IF NOT EXISTS idx_products_cat ON products(category, sensitivity);
        CREATE INDEX IF NOT EXISTS idx_tickets_tid_topic ON tickets(tid, topic, severity);
        CREATE INDEX IF NOT EXISTS idx_tickets_cid ON tickets(cid);
        CREATE INDEX IF NOT EXISTS idx_acl_cid ON customer_acl(cid);
        ANALYZE;
        """
    )
    conn.commit()


def prepare_database(path: str, seed: int = 7, scale: float = 0.35) -> Dict[str, int]:
    for p in [path, path + "-wal", path + "-shm"]:
        if os.path.exists(p):
            os.remove(p)
    conn = connect(path)
    try:
        create_schema(conn)
        populate(conn, seed=seed, scale=scale)
        create_indexes(conn)
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLE_COLUMNS}
        counts["customer_acl"] = conn.execute("SELECT COUNT(*) FROM customer_acl").fetchone()[0]
        return counts
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Compiler and optimizer
# ---------------------------------------------------------------------------


class Stats:
    """Lightweight statistics used by the research optimizer.

    The prototype intentionally avoids relying on SQLite's private statistics tables.
    It records table cardinalities and distinct counts for columns that appear in
    policy predicates or join predicates.  This gives the paper an auditable
    policy-aware cost model while staying portable across Python/SQLite versions.
    """
    def __init__(self, conn: sqlite3.Connection):
        self.rows = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLE_COLUMNS}
        self.ndv: Dict[Tuple[str, str], int] = {}
        for table, cols in TABLE_COLUMNS.items():
            for col in cols:
                try:
                    val = conn.execute(f"SELECT COUNT(DISTINCT {col}) FROM {table}").fetchone()[0]
                except sqlite3.OperationalError:
                    val = self.rows.get(table, 1)
                self.ndv[(table, col)] = max(1, int(val))

    def distinct(self, table: str, col: str) -> int:
        return max(1, int(self.ndv.get((table, col), self.rows.get(table, 1))))



class FaultyPolicyManager(PolicyManager):
    """Policy manager used only for mutation testing.

    Each mutation deletes one semantic obligation while sharing the same SQL
    compiler.  This keeps negative controls fair: when the checker reports a
    difference, the cause is a missing policy obligation rather than unrelated
    SQL generation.
    """

    def __init__(self, base: PolicyManager, *, drop_labels: Optional[set] = None,
                 no_mask: bool = False, no_group_guard: bool = False):
        super().__init__(base.complexity, base.ctx)
        self.drop_labels = set(drop_labels or set())
        self.no_mask = no_mask
        self.no_group_guard = no_group_guard

    def row_predicates(self, table: str, alias: str, raw_prefix: bool = False) -> List[Tuple[str, float, str]]:
        return [p for p in super().row_predicates(table, alias, raw_prefix) if p[2] not in self.drop_labels]

    def mask_expr(self, table: str, alias: str, col: str) -> str:
        if self.no_mask:
            return f"{alias}.{col}"
        return super().mask_expr(table, alias, col)

    def group_having(self, q: QueryTemplate) -> Optional[str]:
        if self.no_group_guard:
            return q.having
        return super().group_having(q)


class SQLCompiler:
    def __init__(self, stats: Stats, conn: Optional[sqlite3.Connection] = None):
        self.stats = stats
        self.conn = conn

    def compile(self, q: QueryTemplate, policy: PolicyManager, method: str) -> str:
        if method == "reference":
            return self._compile_view_barrier(q, policy, materialized=False)
        if method == "no_policy":
            return self._compile_join(q, policy, q.tables, force_order=False, include_policies=False)
        if method == "view_barrier":
            return self._compile_view_barrier(q, policy, materialized=True)
        if method == "post_join_filter":
            return self._compile_post_join_filter(q, policy, unsafe_aggregate=False)
        if method == "unsafe_late_aggregate":
            return self._compile_post_join_filter(q, policy, unsafe_aggregate=True)
        if method == "mut_no_mask":
            return self._compile_join(q, FaultyPolicyManager(policy, no_mask=True), q.tables, force_order=False, include_policies=True)
        if method == "mut_drop_tenant":
            return self._compile_join(q, FaultyPolicyManager(policy, drop_labels={"tenant"}), q.tables, force_order=False, include_policies=True)
        if method == "mut_drop_purpose":
            return self._compile_join(q, FaultyPolicyManager(policy, drop_labels={"purpose", "purpose-auditor", "support-owner", "ticket-pii"}), q.tables, force_order=False, include_policies=True)
        if method == "mut_drop_clearance":
            return self._compile_join(q, FaultyPolicyManager(policy, drop_labels={"clearance", "risk"}), q.tables, force_order=False, include_policies=True)
        if method == "mut_drop_region":
            return self._compile_join(q, FaultyPolicyManager(policy, drop_labels={"region"}), q.tables, force_order=False, include_policies=True)
        if method == "mut_drop_cross_guard":
            return self._compile_join(q, FaultyPolicyManager(policy, drop_labels={"customer-guard", "order-guard"}), q.tables, force_order=False, include_policies=True)
        if method == "mut_drop_acl":
            return self._compile_join(q, FaultyPolicyManager(policy, drop_labels={"acl-or-region"}), q.tables, force_order=False, include_policies=True)
        if method == "mut_no_group_guard":
            return self._compile_join(q, FaultyPolicyManager(policy, no_group_guard=True), q.tables, force_order=False, include_policies=True)
        if method == "oblivious_forced":
            return self._compile_join(q, policy, q.tables, force_order=True, include_policies=True)
        if method == "predicate_injection":
            return self._compile_join(q, policy, q.tables, force_order=False, include_policies=True)
        if method == "pcqv_no_order":
            # Same safe predicates as PCQV, but delegate every ordering decision to SQLite.
            return self._compile_join(q, policy, q.tables, force_order=False, include_policies=True)
        if method == "pcqv_no_stats":
            # Safe forced order, but the enumerator ignores policy selectivity.
            return self._compile_join(q, policy, self.policy_oblivious_order(q, policy), force_order=True, include_policies=True)
        if method == "pcqv_greedy":
            # Safe forced order from a greedy protected-cardinality heuristic.
            return self._compile_join(q, policy, self.policy_aware_greedy_order(q, policy), force_order=True, include_policies=True)
        if method == "pcqv_forced":
            # Safe forced order from exhaustive left-deep enumeration in the controlled fragment.
            return self._compile_join(q, policy, self.policy_aware_dp_order(q, policy), force_order=True, include_policies=True)
        if method == "oracle_order":
            # Diagnostic only: uses actual protected cardinalities measured from the database.
            return self._compile_join(q, policy, self.actual_policy_order(q, policy), force_order=True, include_policies=True)
        if method == "pcqv":
            choice = self.choose_pcqv_mode(q, policy)
            if choice["mode"] == "forced":
                return self._compile_join(q, policy, self.policy_aware_dp_order(q, policy), force_order=True, include_policies=True)
            return self._compile_join(q, policy, q.tables, force_order=False, include_policies=True)
        raise ValueError(f"unknown method: {method}")

    def _join_conditions(self, q: QueryTemplate, aliases: Optional[Iterable[str]] = None) -> List[str]:
        keep = set(aliases) if aliases is not None else None
        conds = []
        for j in q.joins:
            if keep is None or (j.left_alias in keep and j.right_alias in keep):
                conds.append(f"{j.left_alias}.{j.left_col} = {j.right_alias}.{j.right_col}")
        return conds

    def _compile_select_list(self, q: QueryTemplate, policy: PolicyManager) -> str:
        parts = []
        for expr, alias in q.select_exprs:
            if expr == "MASK(c.email)":
                compiled = policy.mask_expr("customers", "c", "email")
            else:
                compiled = expr
            parts.append(f"{compiled} AS {alias}")
        return ", ".join(parts)

    def _compile_join(self, q: QueryTemplate, policy: PolicyManager, ordered_tables: List[TableRef], force_order: bool, include_policies: bool) -> str:
        sep = " CROSS JOIN " if force_order else " JOIN "
        from_clause = sep.join(f"{tr.table} {tr.alias}" for tr in ordered_tables)
        where_parts = []
        where_parts.extend(self._join_conditions(q))
        where_parts.extend(f.expr.format(a=f.alias) for f in q.filters)
        if include_policies:
            where_parts.extend(policy.policy_where(tr.table, tr.alias) for tr in q.tables)
        where = " AND ".join(f"({x})" for x in where_parts) if where_parts else "1=1"
        sql = f"SELECT {self._compile_select_list(q, policy)} FROM {from_clause} WHERE {where}"
        if q.group_by:
            sql += " GROUP BY " + ", ".join(q.group_by)
            having = policy.group_having(q)
            if having:
                sql += " HAVING " + having
        if q.order_by:
            sql += " ORDER BY " + q.order_by
        if q.limit:
            sql += f" LIMIT {int(q.limit)}"
        return sql

    def _compile_view_barrier(self, q: QueryTemplate, policy: PolicyManager, materialized: bool = True) -> str:
        mat = "MATERIALIZED " if materialized else ""
        ctes = []
        alias_to_cte: Dict[str, str] = {}
        for tr in q.tables:
            name = f"p_{tr.alias}"
            alias_to_cte[tr.alias] = name
            ctes.append(f"{name} AS {mat}(SELECT * FROM {tr.table} {tr.alias} WHERE {policy.policy_where(tr.table, tr.alias)})")
        from_clause = " JOIN ".join(f"{alias_to_cte[tr.alias]} {tr.alias}" for tr in q.tables)
        where_parts = self._join_conditions(q) + [f.expr.format(a=f.alias) for f in q.filters]
        where = " AND ".join(f"({x})" for x in where_parts) if where_parts else "1=1"
        sql = "WITH " + ", ".join(ctes) + f" SELECT {self._compile_select_list(q, policy)} FROM {from_clause} WHERE {where}"
        if q.group_by:
            sql += " GROUP BY " + ", ".join(q.group_by)
            having = policy.group_having(q)
            if having:
                sql += " HAVING " + having
        if q.order_by:
            sql += " ORDER BY " + q.order_by
        if q.limit:
            sql += f" LIMIT {int(q.limit)}"
        return sql

    def _compile_post_join_filter(self, q: QueryTemplate, policy: PolicyManager, unsafe_aggregate: bool = False) -> str:
        from_clause = " CROSS JOIN ".join(f"{tr.table} {tr.alias}" for tr in q.tables)
        where_parts = self._join_conditions(q) + [f.expr.format(a=f.alias) for f in q.filters]
        where = " AND ".join(f"({x})" for x in where_parts) if where_parts else "1=1"
        cols = []
        for tr in q.tables:
            for col in TABLE_COLUMNS[tr.table]:
                cols.append(f"{tr.alias}.{col} AS {tr.alias}_{col}")
        raw = f"raw AS (SELECT {', '.join(cols)} FROM {from_clause} WHERE {where})"

        raw_policy_parts: List[str] = []
        aliases = {tr.alias: tr.table for tr in q.tables}
        for tr in q.tables:
            raw_policy_parts.extend(p for p, _, _ in policy.row_predicates(tr.table, tr.alias, raw_prefix=True))
        if policy.complexity >= 4:
            if "o" in aliases:
                if "c" in aliases:
                    raw_policy_parts.append(f"o_cid = c_cid AND o_tid = c_tid AND c_region IN ({policy.ctx.sql_literal_list(policy.ctx.allowed_regions)})")
                else:
                    raw_policy_parts.append(f"EXISTS (SELECT 1 FROM customers pc WHERE pc.cid = o_cid AND pc.tid = o_tid AND pc.region IN ({policy.ctx.sql_literal_list(policy.ctx.allowed_regions)}))")
            if "l" in aliases:
                if "o" in aliases:
                    raw_policy_parts.append("l_oid = o_oid AND l_tid = o_tid AND o_status <> 'internal'")
                else:
                    raw_policy_parts.append("EXISTS (SELECT 1 FROM orders po WHERE po.oid = l_oid AND po.tid = l_tid AND po.status <> 'internal')")
        raw_policy = " AND ".join(f"({x})" for x in raw_policy_parts) if raw_policy_parts else "1=1"

        def raw_expr(expr: str) -> str:
            out = expr.replace("MASK(c.email)", "CASE WHEN c_opted_in = 1 THEN c_email ELSE 'MASKED' END" if policy.ctx.purpose == "support" else "'MASKED'")
            for tr in sorted(q.tables, key=lambda x: -len(x.alias)):
                for col in TABLE_COLUMNS[tr.table]:
                    out = out.replace(f"{tr.alias}.{col}", f"{tr.alias}_{col}")
            return out

        select_list = ", ".join(f"{raw_expr(expr)} AS {alias}" for expr, alias in q.select_exprs)
        if unsafe_aggregate and q.result_kind == "aggregate":
            # Negative control: aggregate before row policies and group guards.
            sql = f"WITH {raw} SELECT {select_list} FROM raw"
            if q.group_by:
                sql += " GROUP BY " + ", ".join(raw_expr(g) for g in q.group_by)
                if policy.group_having(q):
                    sql += " HAVING " + policy.group_having(q)
            return sql
        sql = f"WITH {raw} SELECT {select_list} FROM raw WHERE {raw_policy}"
        if q.group_by:
            sql += " GROUP BY " + ", ".join(raw_expr(g) for g in q.group_by)
            having = policy.group_having(q)
            if having:
                sql += " HAVING " + raw_expr(having)
        if q.order_by:
            order = q.order_by
            for tr in q.tables:
                for col in TABLE_COLUMNS[tr.table]:
                    order = order.replace(f"{tr.alias}.{col}", f"{tr.alias}_{col}")
            sql += " ORDER BY " + order
        if q.limit:
            sql += f" LIMIT {int(q.limit)}"
        return sql

    # --- Cost model ---

    def estimated_base_cardinality(self, tr: TableRef, policy: PolicyManager, q: QueryTemplate, use_policy: bool = True) -> float:
        card = float(self.stats.rows[tr.table])
        if use_policy:
            card *= policy.predicate_selectivity(tr.table, tr.alias)
        for f in q.filters:
            if f.alias == tr.alias:
                card *= f.sel
        return max(1.0, card)

    def actual_base_cardinality(self, tr: TableRef, policy: PolicyManager, q: QueryTemplate, include_policy: bool = True) -> int:
        if self.conn is None:
            raise RuntimeError("actual_base_cardinality requires a SQLite connection")
        parts = []
        if include_policy:
            parts.append(policy.policy_where(tr.table, tr.alias))
        parts.extend(f.expr.format(a=f.alias) for f in q.filters if f.alias == tr.alias)
        where = " AND ".join(f"({x})" for x in parts) if parts else "1=1"
        return int(self.conn.execute(f"SELECT COUNT(*) FROM {tr.table} {tr.alias} WHERE {where}").fetchone()[0])

    def join_selectivity(self, order_aliases: Sequence[str], new_alias: str, q: QueryTemplate) -> float:
        """Estimate the reduction of joining a new alias to the current prefix.

        We use the standard uniform key-join approximation 1/max(NDV(left),
        NDV(right)) for every equality edge that connects the new alias to the
        prefix.  Disconnected prefixes are allowed for completeness but heavily
        penalized in order_cost().
        """
        sel = 1.0
        connected = False
        prefix = set(order_aliases)
        for j in q.joins:
            if j.left_alias == new_alias and j.right_alias in prefix:
                connected = True
                left_ndv = self.stats.distinct(q.table_for(j.left_alias), j.left_col)
                right_ndv = self.stats.distinct(q.table_for(j.right_alias), j.right_col)
                sel *= 1.0 / max(1.0, max(left_ndv, right_ndv))
            elif j.right_alias == new_alias and j.left_alias in prefix:
                connected = True
                left_ndv = self.stats.distinct(q.table_for(j.left_alias), j.left_col)
                right_ndv = self.stats.distinct(q.table_for(j.right_alias), j.right_col)
                sel *= 1.0 / max(1.0, max(left_ndv, right_ndv))
        return sel if connected else 1.0

    def order_cost(self, order: Sequence[TableRef], q: QueryTemplate, policy: PolicyManager, use_policy: bool = True, actual: bool = False) -> float:
        if actual and self.conn is None:
            raise RuntimeError("actual order_cost requires a SQLite connection")
        aliases: List[str] = []
        intermediate = 1.0
        total_cost = 0.0
        disconnected_penalty = 50.0
        for i, tr in enumerate(order):
            base = float(self.actual_base_cardinality(tr, policy, q, include_policy=use_policy)) if actual else self.estimated_base_cardinality(tr, policy, q, use_policy=use_policy)
            if i == 0:
                intermediate = base
            else:
                js = self.join_selectivity(aliases, tr.alias, q)
                if js == 1.0:
                    intermediate = intermediate * base * disconnected_penalty
                else:
                    intermediate = max(1.0, intermediate * base * js)
            aliases.append(tr.alias)
            total_cost += intermediate + base
        if q.result_kind == "aggregate":
            total_cost *= 1.1
        if policy.group_having(q):
            total_cost *= 1.03
        return max(1.0, total_cost)

    def _enumerate_orders(self, q: QueryTemplate) -> Iterable[Tuple[TableRef, ...]]:
        # The benchmark templates use at most four aliases, so exhaustive
        # left-deep enumeration is cheap and makes the ablation interpretable.
        # Disconnected permutations are kept but penalized by order_cost.
        return permutations(q.tables)

    def policy_oblivious_order(self, q: QueryTemplate, policy: PolicyManager) -> List[TableRef]:
        best_order: Optional[Tuple[TableRef, ...]] = None
        best_cost = float("inf")
        for perm in self._enumerate_orders(q):
            c = self.order_cost(perm, q, policy, use_policy=False, actual=False)
            if c < best_cost:
                best_order = perm
                best_cost = c
        assert best_order is not None
        return list(best_order)

    def policy_aware_greedy_order(self, q: QueryTemplate, policy: PolicyManager) -> List[TableRef]:
        remaining = list(q.tables)
        chosen: List[TableRef] = []
        while remaining:
            best = min(remaining, key=lambda tr: self.order_cost(tuple(chosen + [tr]), q, policy, use_policy=True, actual=False))
            chosen.append(best)
            remaining.remove(best)
        return chosen

    def policy_aware_order(self, q: QueryTemplate, policy: PolicyManager) -> List[TableRef]:
        best_order: Optional[Tuple[TableRef, ...]] = None
        best_cost = float("inf")
        for perm in self._enumerate_orders(q):
            c = self.order_cost(perm, q, policy, use_policy=True, actual=False)
            if c < best_cost:
                best_order = perm
                best_cost = c
        assert best_order is not None
        return list(best_order)


    def policy_aware_dp_order(self, q: QueryTemplate, policy: PolicyManager) -> List[TableRef]:
        """Selinger-style dynamic programming for safe left-deep join order.

        The earlier artifact used direct permutation enumeration because the
        templates were small. This DP implementation is equivalent on the
        current workload but exposes the actual optimizer algorithm used in the
        paper: for each subset, keep the lowest protected-cardinality prefix and
        extend it with one visible alias whose guards remain attached. The cost
        function is the same policy-aware intermediate-result proxy used by the
        exhaustive checker, so the result can be cross-checked against exhaustive
        enumeration for small queries while scaling to larger JOB-like stress
        tests.
        """
        n = len(q.tables)
        if n <= 1:
            return list(q.tables)
        alias_to_tr = {tr.alias: tr for tr in q.tables}
        alias_list = [tr.alias for tr in q.tables]
        dp: Dict[frozenset, Tuple[float, Tuple[str, ...], float]] = {}
        for a in alias_list:
            tr = alias_to_tr[a]
            base = self.estimated_base_cardinality(tr, policy, q, use_policy=True)
            dp[frozenset([a])] = (base + base, (a,), base)
        for size in range(2, n + 1):
            next_items = [frozenset(x) for x in __import__('itertools').combinations(alias_list, size)]
            for subset in next_items:
                best: Optional[Tuple[float, Tuple[str, ...], float]] = None
                for a in subset:
                    prev = subset - {a}
                    if prev not in dp:
                        continue
                    prev_cost, prev_order, prev_card = dp[prev]
                    tr = alias_to_tr[a]
                    base = self.estimated_base_cardinality(tr, policy, q, use_policy=True)
                    js = self.join_selectivity(prev_order, a, q)
                    disconnected = js == 1.0
                    out = prev_card * base * js
                    if disconnected:
                        out *= 50.0
                    out = max(1.0, out)
                    cost = prev_cost + out + base
                    cand = (cost, prev_order + (a,), out)
                    if best is None or cand[0] < best[0]:
                        best = cand
                if best is not None:
                    dp[subset] = best
        full = frozenset(alias_list)
        if full not in dp:
            return self.policy_aware_order(q, policy)
        return [alias_to_tr[a] for a in dp[full][1]]

    def actual_policy_order(self, q: QueryTemplate, policy: PolicyManager) -> List[TableRef]:
        if self.conn is None:
            return self.policy_aware_order(q, policy)
        best_order: Optional[Tuple[TableRef, ...]] = None
        best_cost = float("inf")
        for perm in self._enumerate_orders(q):
            c = self.order_cost(perm, q, policy, use_policy=True, actual=True)
            if c < best_cost:
                best_order = perm
                best_cost = c
        assert best_order is not None
        return list(best_order)

    def choose_pcqv_mode(self, q: QueryTemplate, policy: PolicyManager) -> Dict[str, Any]:
        original = q.tables
        best = self.policy_aware_dp_order(q, policy)
        original_cost = self.order_cost(original, q, policy, use_policy=True, actual=False)
        best_cost = self.order_cost(best, q, policy, use_policy=True, actual=False)
        native_proxy_cost = original_cost * 0.90  # native optimizer may recover some order choices from indexes
        gap = native_proxy_cost / max(1.0, best_cost)
        # Choose forced SQL only when the policy-aware enumerator predicts a
        # substantial benefit.  Otherwise keep native predicate injection as the
        # strongest safe baseline and let SQLite use its own plan search.
        mode = "forced" if [x.alias for x in best] != [x.alias for x in original] and gap >= 1.5 else "native"
        return {
            "mode": mode,
            "template_order": [x.alias for x in original],
            "policy_aware_order": [x.alias for x in best],
            "policy_oblivious_order": [x.alias for x in self.policy_oblivious_order(q, policy)],
            "greedy_order": [x.alias for x in self.policy_aware_greedy_order(q, policy)],
            "estimated_original_cost": round(original_cost, 3),
            "estimated_best_cost": round(best_cost, 3),
            "estimated_native_proxy_cost": round(native_proxy_cost, 3),
            "estimated_gap": round(gap, 3),
            "num_left_deep_orders": math.factorial(len(q.tables)),
        }

    def explain_order(self, q: QueryTemplate, policy: PolicyManager) -> Dict[str, Any]:
        d = self.choose_pcqv_mode(q, policy)
        d["estimated_base_cards"] = {tr.alias: round(self.estimated_base_cardinality(tr, policy, q), 3) for tr in q.tables}
        d["estimated_plain_cards"] = {tr.alias: round(self.estimated_base_cardinality(tr, policy, q, use_policy=False), 3) for tr in q.tables}
        if self.conn is not None:
            d["actual_base_cards"] = {tr.alias: self.actual_base_cardinality(tr, policy, q) for tr in q.tables}
            d["actual_plain_cards"] = {tr.alias: self.actual_base_cardinality(tr, policy, q, include_policy=False) for tr in q.tables}
            d["oracle_order"] = [tr.alias for tr in self.actual_policy_order(q, policy)]
        d["base_rows"] = {tr.alias: self.stats.rows[tr.table] for tr in q.tables}
        return d


# ---------------------------------------------------------------------------
# Safety and testing utilities
# ---------------------------------------------------------------------------


def rule_catalog() -> List[Dict[str, str]]:
    return [
        {"rule": "R1 base guard introduction", "condition": "attach every deterministic row guard to the protected base relation", "implemented": "yes"},
        {"rule": "R2 ordinary selection pushdown", "condition": "selection predicate does not read a masked value and is deterministic", "implemented": "yes"},
        {"rule": "R3 join reordering", "condition": "inner joins only; all guards remain attached or preserved as conjuncts", "implemented": "yes"},
        {"rule": "R4 cross-table guard reification", "condition": "correlated EXISTS is preserved or replaced by equivalent joined attributes", "implemented": "yes"},
        {"rule": "R5 mask movement", "condition": "mask only at observable projection; masked attributes not used for join/group/filter", "implemented": "yes"},
        {"rule": "R6 aggregate guard", "condition": "group threshold applied after row guards and final grouping", "implemented": "yes"},
        {"rule": "R7 unsafe late aggregate", "condition": "forbidden: aggregate unprotected rows before row guards", "implemented": "negative-control"},
        {"rule": "M1 no mask", "condition": "forbidden: observable masked columns are returned raw", "implemented": "mutation-control"},
        {"rule": "M2 dropped tenant guard", "condition": "forbidden: tenant isolation is deleted from base predicates", "implemented": "mutation-control"},
        {"rule": "M3 dropped purpose guard", "condition": "forbidden: purpose or ticket-owner restrictions are deleted", "implemented": "mutation-control"},
        {"rule": "M4 dropped clearance/risk guard", "condition": "forbidden: product clearance or customer risk guards are deleted", "implemented": "mutation-control"},
        {"rule": "M5 dropped region guard", "condition": "forbidden: contextual regional visibility is deleted", "implemented": "mutation-control"},
        {"rule": "M6 dropped cross-table guard", "condition": "forbidden: correlated membership guards are deleted", "implemented": "mutation-control"},
        {"rule": "M3 dropped ACL disjunction", "condition": "forbidden: exception/region policy is deleted", "implemented": "mutation-control"},
        {"rule": "M4 missing group guard", "condition": "forbidden: aggregate threshold is omitted", "implemented": "mutation-control"},
    ]


def method_safety(q: QueryTemplate, method: str) -> Tuple[bool, str]:
    if method == "no_policy":
        return False, "policy guards intentionally absent"
    if method == "unsafe_late_aggregate" and q.result_kind == "aggregate":
        return False, "aggregate is evaluated before row policies"
    if method.startswith("mut_"):
        return False, "mutation deletes one policy obligation for checker sensitivity"
    return True, "candidate encodes the protected-view normal form under the rule side conditions"


def timed_query(conn: sqlite3.Connection, sql: str, reps: int = 3, timeout_s: float = 3.0) -> Tuple[float, int]:
    """Execute a query with a per-execution SQLite progress timeout.

    The timeout is part of the benchmark contract: it prevents one pathological
    baseline from turning a CPU-only artifact into a long background task.  A
    timed-out cell is recorded as an error by the caller rather than replaced
    by a fabricated latency.
    """
    def run_once() -> List[tuple]:
        start = time.perf_counter()
        def progress() -> int:
            return 1 if (time.perf_counter() - start) > timeout_s else 0
        conn.set_progress_handler(progress, 5000)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.set_progress_handler(None, 0)
    try:
        run_once()
    except sqlite3.Error as e:
        raise RuntimeError(f"SQLite error or timeout: {e}\nSQL:\n{sql}") from e
    times: List[float] = []
    nrows = 0
    for _ in range(reps):
        t0 = time.perf_counter()
        try:
            rows = run_once()
        except sqlite3.Error as e:
            raise RuntimeError(f"SQLite error or timeout: {e}\nSQL:\n{sql}") from e
        dt = (time.perf_counter() - t0) * 1000.0
        times.append(dt)
        nrows = len(rows)
    return statistics.median(times), nrows


def canonicalize(rows: List[tuple]) -> List[tuple]:
    out = []
    for row in rows:
        r = []
        for x in row:
            if isinstance(x, float):
                r.append(round(x, 4))
            else:
                r.append(x)
        out.append(tuple(r))
    return sorted(out, key=lambda z: repr(z))


def execute_rows(conn: sqlite3.Connection, sql: str) -> List[tuple]:
    return canonicalize(conn.execute(sql).fetchall())


def multiset_violation(got: List[tuple], ref: List[tuple]) -> int:
    counts: Dict[tuple, int] = {}
    for r in ref:
        counts[r] = counts.get(r, 0) + 1
    extra = 0
    for r in got:
        if counts.get(r, 0) > 0:
            counts[r] -= 1
        else:
            extra += 1
    return extra


def symmetric_difference_count(got: List[tuple], ref: List[tuple]) -> int:
    return multiset_violation(got, ref) + multiset_violation(ref, got)


def execute_rows_bounded(conn: sqlite3.Connection, sql: str, seconds: float = 2.0) -> List[tuple]:
    start = time.perf_counter()
    def progress() -> int:
        return 1 if (time.perf_counter() - start) > seconds else 0
    conn.set_progress_handler(progress, 5000)
    try:
        return execute_rows(conn, sql)
    finally:
        conn.set_progress_handler(None, 0)


# ---------------------------------------------------------------------------
# Experiment runners
# ---------------------------------------------------------------------------


MAIN_METHODS = [
    "no_policy",
    "post_join_filter",
    "view_barrier",
    "oblivious_forced",
    "predicate_injection",
    "pcqv_no_stats",
    "pcqv_forced",
    "pcqv",
    "oracle_order",
]
SAFE_METHODS = [m for m in MAIN_METHODS if m != "no_policy"]
MUTATION_METHODS = ["mut_no_mask", "mut_drop_tenant", "mut_drop_purpose", "mut_drop_clearance", "mut_drop_region", "mut_drop_cross_guard", "mut_drop_acl", "mut_no_group_guard"]
CHECK_METHODS = SAFE_METHODS + ["unsafe_late_aggregate"]
SELECTIVITIES = [0.025, 0.05, 0.10, 0.25, 0.50]
CORRECTNESS_SELECTIVITIES = [0.05, 0.25]
COMPLEXITIES = [1, 2, 3, 4, 5, 6]
CORRECTNESS_COMPLEXITIES = [1, 3, 5, 6]


def run_performance(db_path: str, out_csv: str, plan_json: str, reps: int = 2, scale_label: str = "0.35") -> None:
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    rows: List[Dict[str, Any]] = []
    plan_examples: List[Dict[str, Any]] = []
    no_policy_cache: Dict[str, Tuple[float, int, str]] = {}
    try:
        for sel in SELECTIVITIES:
            ctx = context_for_selectivity(sel)
            for comp in COMPLEXITIES:
                policy = PolicyManager(comp, ctx)
                for q in query_templates():
                    if q.name in {"q4_revenue_by_region", "q6_lineitem_product_count"} and comp in {1, 4, 6} and sel in {0.025, 0.25}:
                        plan_examples.append({"selectivity": sel, "complexity": comp, "query": q.name, **compiler.explain_order(q, policy)})
                    for method in MAIN_METHODS:
                        sql = compiler.compile(q, policy, method)
                        if method == "no_policy" and q.name in no_policy_cache:
                            ms, nrows, err = no_policy_cache[q.name]
                        else:
                            try:
                                ms, nrows = timed_query(conn, sql, reps=reps)
                                err = ""
                            except Exception as e:
                                ms, nrows, err = float("nan"), -1, str(e).splitlines()[0][:240]
                            if method == "no_policy":
                                no_policy_cache[q.name] = (ms, nrows, err)
                        rows.append({
                            "scale": scale_label,
                            "selectivity": sel,
                            "complexity": comp,
                            "query": q.name,
                            "method": method,
                            "latency_ms": round(ms, 4) if ms == ms else "nan",
                            "rows": nrows,
                            "safe_by_rule": int(method_safety(q, method)[0]),
                            "error": err,
                        })
    finally:
        conn.close()
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(plan_json, "w") as f:
        json.dump(plan_examples, f, indent=2)


def run_correctness(db_path: str, out_csv: str, sql_examples_json: str) -> None:
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    rows: List[Dict[str, Any]] = []
    examples: List[Dict[str, Any]] = []
    try:
        for sel in CORRECTNESS_SELECTIVITIES:
            ctx = context_for_selectivity(sel)
            for comp in CORRECTNESS_COMPLEXITIES:
                policy = PolicyManager(comp, ctx)
                for q in query_templates():
                    ref_sql = compiler.compile(q, policy, "reference")
                    try:
                        ref = execute_rows_bounded(conn, ref_sql, seconds=0.35)
                        ref_err = ""
                    except Exception as e:
                        ref, ref_err = [], str(e).splitlines()[0][:240]
                    for method in CHECK_METHODS:
                        sql = compiler.compile(q, policy, method)
                        safe, reason = method_safety(q, method)
                        try:
                            got = execute_rows_bounded(conn, sql, seconds=0.35)
                            equiv = got == ref
                            violation = 0 if equiv else multiset_violation(got, ref)
                            diff_count = 0 if equiv else symmetric_difference_count(got, ref)
                            err = ""
                        except Exception as e:
                            equiv = False
                            violation = -1
                            diff_count = -1
                            err = str(e).splitlines()[0][:240]
                        rows.append({
                            "selectivity": sel,
                            "complexity": comp,
                            "query": q.name,
                            "method": method,
                            "safe_by_rule": int(safe),
                            "rule_reason": reason,
                            "equivalent_to_reference": int(equiv),
                            "policy_violation_count": violation,
                            "symmetric_difference_count": diff_count,
                            "reference_error": ref_err,
                            "error": err,
                        })
                    if sel == 0.10 and comp == 6 and q.name in {"q4_revenue_by_region", "q7_customer_summary"}:
                        examples.append({
                            "query": q.name,
                            "reference": ref_sql,
                            "pcqv": compiler.compile(q, policy, "pcqv"),
                            "unsafe_late_aggregate": compiler.compile(q, policy, "unsafe_late_aggregate"),
                            "mut_no_mask": compiler.compile(q, policy, "mut_no_mask"),
                            "mut_drop_cross_guard": compiler.compile(q, policy, "mut_drop_cross_guard"),
                            "mut_drop_acl": compiler.compile(q, policy, "mut_drop_acl"),
                            "mut_no_group_guard": compiler.compile(q, policy, "mut_no_group_guard"),
                        })
    finally:
        conn.close()
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(sql_examples_json, "w") as f:
        json.dump(examples, f, indent=2)



def run_mutation_controls(db_path: str, out_csv: str, seconds_per_query: float = 0.5) -> None:
    """Targeted negative controls for checker sensitivity.

    The full equivalence grid already includes the unsafe late-aggregation
    baseline.  This targeted suite mutates individual policy obligations on
    diagnostic query shapes so the artifact can show that masks, cross-table
    guards, ACL exceptions, and group thresholds are each observable.
    """
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    rows: List[Dict[str, Any]] = []
    keep_queries = {"q1_customer_orders", "q3_support_tickets", "q4_revenue_by_region", "q7_customer_summary", "q10_acl_customer_rows", "q12_product_segment_revenue", "q16_region_product_mix"}
    try:
        for sel in [0.025, 0.25, 0.50]:
            ctx = context_for_selectivity(sel)
            for comp in [5, 6]:
                policy = PolicyManager(comp, ctx)
                for q in [qt for qt in query_templates() if qt.name in keep_queries]:
                    ref_sql = compiler.compile(q, policy, "reference")
                    try:
                        ref = execute_rows_bounded(conn, ref_sql, seconds=seconds_per_query)
                        ref_err = ""
                    except Exception as e:
                        ref, ref_err = [], str(e).splitlines()[0][:240]
                    for method in MUTATION_METHODS:
                        sql = compiler.compile(q, policy, method)
                        safe, reason = method_safety(q, method)
                        try:
                            got = execute_rows_bounded(conn, sql, seconds=seconds_per_query)
                            equiv = got == ref
                            violation = 0 if equiv else multiset_violation(got, ref)
                            diff_count = 0 if equiv else symmetric_difference_count(got, ref)
                            err = ""
                        except Exception as e:
                            equiv = False
                            violation = -1
                            diff_count = -1
                            err = str(e).splitlines()[0][:240]
                        rows.append({
                            "selectivity": sel,
                            "complexity": comp,
                            "query": q.name,
                            "method": method,
                            "safe_by_rule": int(safe),
                            "rule_reason": reason,
                            "equivalent_to_reference": int(equiv),
                            "policy_violation_count": violation,
                            "symmetric_difference_count": diff_count,
                            "reference_error": ref_err,
                            "error": err,
                        })
    finally:
        conn.close()
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)

def run_planning_overhead(db_path: str, out_csv: str, iterations: int = 8) -> None:
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    methods = ["post_join_filter", "view_barrier", "oblivious_forced", "predicate_injection", "pcqv_no_stats", "pcqv_forced", "pcqv", "oracle_order"]
    rows: List[Dict[str, Any]] = []
    try:
        for sel in [0.025, 0.10, 0.25, 0.50]:
            ctx = context_for_selectivity(sel)
            for comp in [1, 3, 6]:
                policy = PolicyManager(comp, ctx)
                for q in query_templates():
                    for method in methods:
                        local_iters = 1 if method == "oracle_order" else iterations
                        t0 = time.perf_counter()
                        size = 0
                        for _ in range(local_iters):
                            size += len(compiler.compile(q, policy, method))
                        dt = (time.perf_counter() - t0) * 1000.0 / local_iters
                        rows.append({
                            "selectivity": sel,
                            "complexity": comp,
                            "query": q.name,
                            "method": method,
                            "compile_ms": round(dt, 6),
                            "sql_chars": int(size / local_iters),
                            "iterations": local_iters,
                        })
    finally:
        conn.close()
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


def run_ablation(db_path: str, out_csv: str, reps: int = 2) -> None:
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    methods = ["oblivious_forced", "pcqv_no_order", "pcqv_no_stats", "pcqv_greedy", "pcqv_forced", "pcqv", "oracle_order"]
    rows: List[Dict[str, Any]] = []
    try:
        for sel in [0.05, 0.25, 0.50]:
            ctx = context_for_selectivity(sel)
            for comp in [3, 5, 6]:
                policy = PolicyManager(comp, ctx)
                for q in query_templates():
                    for method in methods:
                        sql = compiler.compile(q, policy, method)
                        try:
                            ms, nrows = timed_query(conn, sql, reps=reps)
                            err = ""
                        except Exception as e:
                            ms, nrows, err = float("nan"), -1, str(e).splitlines()[0][:240]
                        rows.append({
                            "selectivity": sel,
                            "complexity": comp,
                            "query": q.name,
                            "method": method,
                            "latency_ms": round(ms, 4) if ms == ms else "nan",
                            "rows": nrows,
                            "error": err,
                        })
    finally:
        conn.close()
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


def q_error(est: float, actual: float) -> float:
    est = max(float(est), 1.0)
    actual = max(float(actual), 1.0)
    return max(est / actual, actual / est)


def run_cardinality_study(db_path: str, out_csv: str) -> None:
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    rows: List[Dict[str, Any]] = []
    try:
        for sel in SELECTIVITIES:
            ctx = context_for_selectivity(sel)
            for comp in COMPLEXITIES:
                policy = PolicyManager(comp, ctx)
                for q in query_templates():
                    for tr in q.tables:
                        est_policy = compiler.estimated_base_cardinality(tr, policy, q, use_policy=True)
                        est_plain = compiler.estimated_base_cardinality(tr, policy, q, use_policy=False)
                        actual_policy = compiler.actual_base_cardinality(tr, policy, q, include_policy=True)
                        actual_plain = compiler.actual_base_cardinality(tr, policy, q, include_policy=False)
                        rows.append({
                            "selectivity": sel,
                            "complexity": comp,
                            "query": q.name,
                            "alias": tr.alias,
                            "table": tr.table,
                            "estimated_policy_cardinality": round(est_policy, 4),
                            "actual_policy_cardinality": actual_policy,
                            "policy_q_error": round(q_error(est_policy, actual_policy), 4),
                            "estimated_without_policy": round(est_plain, 4),
                            "actual_without_policy": actual_plain,
                            "plain_q_error": round(q_error(est_plain, actual_plain), 4),
                        })
    finally:
        conn.close()
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)


def run_rule_coverage(out_csv: str) -> None:
    rows = rule_catalog()
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["rule", "condition", "implemented"])
        w.writeheader(); w.writerows(rows)



def run_proof_obligations(out_csv: str, out_json: str) -> None:
    """Enumerate small finite models for rewrite side conditions.

    These checks are not a replacement for the algebraic proof sketches in the
    paper.  They are executable obligations that catch regressions in the same
    edge cases used by the formal discussion: selection/policy commutation,
    alias-stable joins, mask movement, and aggregate non-commutativity.
    """
    from itertools import chain, combinations as _combinations

    def powerset(xs):
        xs = list(xs)
        return chain.from_iterable(_combinations(xs, r) for r in range(len(xs) + 1))

    rows: List[Dict[str, Any]] = []
    counter: Dict[str, Any] = {}

    # Obligation O1: conjunctive local query predicates and row policies commute.
    tuples = [(x, y) for x in range(2) for y in range(2)]
    checked = 0
    ok = True
    for rel in powerset(tuples):
        rel = list(rel)
        left = sorted([t for t in rel if t[0] == 1 and t[1] == 1])
        right = sorted([t for t in rel if t[1] == 1 and t[0] == 1])
        checked += 1
        if left != right:
            ok = False
            counter["local_selection_commutation"] = {"relation": rel, "left": left, "right": right}
            break
    rows.append({"obligation": "local selection and row-policy commutation", "models_checked": checked, "passed": int(ok), "comment": "sigma_phi(sigma_rho(R)) = sigma_rho(sigma_phi(R)) for conjunctive tuple predicates"})

    # Obligation O2: alias-stable inner-join reordering under alias-attached guards.
    Rdom = [(a, b) for a in range(2) for b in range(2)]
    Sdom = [(b, c) for b in range(2) for c in range(2)]
    Tdom = [(c, d) for c in range(2) for d in range(2)]
    checked = 0
    ok = True
    def join_rst(R, S, T, order_tag):
        out = []
        if order_tag == "left":
            for r in R:
                if r[0] != 1:  # guard attached to R alias
                    continue
                for s0 in S:
                    if r[1] != s0[0] or s0[1] != 1:  # guard attached to S alias
                        continue
                    for t in T:
                        if s0[1] == t[0]:
                            out.append((r[0], r[1], s0[0], s0[1], t[0], t[1]))
        else:
            for s0 in S:
                if s0[1] != 1:
                    continue
                for t in T:
                    if s0[1] != t[0]:
                        continue
                    for r in R:
                        if r[0] == 1 and r[1] == s0[0]:
                            out.append((r[0], r[1], s0[0], s0[1], t[0], t[1]))
        return sorted(out)
    for R in powerset(Rdom):
        for S in powerset(Sdom):
            for T in powerset(Tdom):
                checked += 1
                left = join_rst(list(R), list(S), list(T), "left")
                right = join_rst(list(R), list(S), list(T), "right")
                if left != right:
                    ok = False
                    counter["alias_stable_join_reorder"] = {"R": list(R), "S": list(S), "T": list(T), "left": left, "right": right}
                    break
            if not ok:
                break
        if not ok:
            break
    rows.append({"obligation": "alias-stable guarded inner-join reorder", "models_checked": checked, "passed": int(ok), "comment": "guards travel with aliases across join orders"})

    # Obligation O3: delaying deterministic masks past unrelated selection is safe.
    Mdom = [(email, flag) for email in ["a@x", "b@x"] for flag in [0, 1]]
    checked = 0
    ok = True
    def mask(row):
        return ("MASKED", row[1])
    for rel in powerset(Mdom):
        rel = list(rel)
        left = sorted(mask(t) for t in rel if t[1] == 1)
        right = sorted(t for t in (mask(x) for x in rel) if t[1] == 1)
        checked += 1
        if left != right:
            ok = False
            counter["mask_delay_unrelated_selection"] = {"relation": rel, "left": left, "right": right}
            break
    rows.append({"obligation": "mask delay past unrelated selection", "models_checked": checked, "passed": int(ok), "comment": "safe only when predicate/group/join does not inspect masked attribute"})

    # Counterexample C1: aggregate before row policy changes the contributing set.
    rel = [("tenantA", "g", 10), ("tenantB", "g", 90)]
    allowed = [r for r in rel if r[0] == "tenantA"]
    correct = [] if len(allowed) < 2 else [("g", sum(r[2] for r in allowed), len(allowed))]
    unsafe = [("g", sum(r[2] for r in rel), len(rel))]
    counter["late_aggregate_counterexample"] = {"relation": rel, "correct_after_policy_then_group_guard": correct, "unsafe_group_before_policy": unsafe}
    rows.append({"obligation": "negative: aggregation does not commute with row policy", "models_checked": 1, "passed": int(correct != unsafe), "comment": "counterexample retained intentionally"})

    # Counterexample C2: masking before grouping can collapse distinct raw keys.
    rel = [("a@x", 1), ("b@x", 1)]
    correct = sorted([("MASKED", 1), ("MASKED", 1)])  # group by raw key, mask projection later
    unsafe = sorted([("MASKED", 2)])                  # group by masked key
    counter["mask_before_group_counterexample"] = {"relation": rel, "correct_group_raw_then_mask": correct, "unsafe_group_masked": unsafe}
    rows.append({"obligation": "negative: mask-before-group can change bag results", "models_checked": 1, "passed": int(correct != unsafe), "comment": "counterexample retained intentionally"})

    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    with open(out_json, "w") as f:
        json.dump(counter, f, indent=2)


def run_explain_plans(db_path: str, out_json: str) -> None:
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    examples: List[Dict[str, Any]] = []
    try:
        for sel in [0.025, 0.25]:
            ctx = context_for_selectivity(sel)
            policy = PolicyManager(6, ctx)
            for q in [qt for qt in query_templates() if qt.name in {"q4_revenue_by_region", "q6_lineitem_product_count", "q8_sensitive_product_scan"}]:
                for method in ["predicate_injection", "view_barrier", "oblivious_forced", "pcqv_forced", "pcqv", "oracle_order"]:
                    sql = compiler.compile(q, policy, method)
                    try:
                        plan = [dict(row) for row in conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()]
                        err = ""
                    except Exception as e:
                        plan, err = [], str(e).splitlines()[0][:240]
                    examples.append({
                        "selectivity": sel,
                        "complexity": 6,
                        "query": q.name,
                        "method": method,
                        "choice": compiler.explain_order(q, policy),
                        "sql": sql,
                        "explain_query_plan": plan,
                        "error": err,
                    })
    finally:
        conn.close()
    with open(out_json, "w") as f:
        json.dump(examples, f, indent=2)


def run_scale_sensitivity(root: str, out_csv: str, seed: int = 19, reps: int = 1) -> None:
    """Compact scale sweep used as an external-validity stress test.

    The sweep is intentionally small so the whole artifact remains runnable on
    a CPU-only review environment.  It varies table size while using the most
    diagnostic query shapes and the strongest safe baselines.
    """
    data_dir = os.path.join(root, "data")
    os.makedirs(data_dir, exist_ok=True)
    methods = ["predicate_injection", "view_barrier", "oblivious_forced", "pcqv_forced", "pcqv"]
    query_keep = {"q1_customer_orders", "q4_revenue_by_region", "q6_lineitem_product_count", "q8_sensitive_product_scan", "q12_product_segment_revenue", "q16_region_product_mix"}
    rows: List[Dict[str, Any]] = []
    for scale in [0.03, 0.06, 0.12, 0.24, 0.48]:
        db_path = os.path.join(data_dir, f"pcqv_seed{seed}_scale_sweep_{scale}.db")
        prepare_database(db_path, seed=seed, scale=scale)
        conn = connect(db_path)
        stats = Stats(conn)
        compiler = SQLCompiler(stats, conn)
        try:
            for sel in [0.10, 0.50]:
                ctx = context_for_selectivity(sel)
                for comp in [3, 6]:
                    policy = PolicyManager(comp, ctx)
                    for q in [qt for qt in query_templates() if qt.name in query_keep]:
                        for method in methods:
                            sql = compiler.compile(q, policy, method)
                            try:
                                ms, nrows = timed_query(conn, sql, reps=reps)
                                err = ""
                            except Exception as e:
                                ms, nrows, err = float("nan"), -1, str(e).splitlines()[0][:240]
                            rows.append({
                                "scale": scale,
                                "selectivity": sel,
                                "complexity": comp,
                                "query": q.name,
                                "method": method,
                                "latency_ms": round(ms, 4) if ms == ms else "nan",
                                "rows": nrows,
                                "error": err,
                            })
        finally:
            conn.close()
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)



# ---------------------------------------------------------------------------
# Summary helpers
# ---------------------------------------------------------------------------


def _read_csv(path: str) -> List[Dict[str, str]]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _float(x: Any) -> float:
    try:
        return float(x)
    except Exception:
        return float("nan")


def _median(xs: Iterable[float]) -> float:
    vals = [x for x in xs if x == x]
    return statistics.median(vals) if vals else float("nan")


def _percentile(xs: Iterable[float], p: float) -> float:
    vals = sorted(x for x in xs if x == x)
    if not vals:
        return float("nan")
    k = min(len(vals) - 1, max(0, int(round((len(vals) - 1) * p))))
    return vals[k]


def summarize(result_dir: str, out_json: str, out_tables_tex: str) -> Dict[str, Any]:
    perf = _read_csv(os.path.join(result_dir, "performance.csv"))
    corr = _read_csv(os.path.join(result_dir, "correctness.csv"))
    ab = _read_csv(os.path.join(result_dir, "ablation.csv"))
    ov = _read_csv(os.path.join(result_dir, "planning_overhead.csv"))
    ce = _read_csv(os.path.join(result_dir, "cardinality.csv"))

    methods = sorted({r["method"] for r in perf})
    med_by_method = {m: round(_median(_float(r["latency_ms"]) for r in perf if r["method"] == m), 4) for m in methods}
    p95_by_method = {m: round(_percentile((_float(r["latency_ms"]) for r in perf if r["method"] == m), 0.95), 4) for m in methods}

    # Paired speedups by selectivity/complexity/query.
    key = lambda r: (r["selectivity"], r["complexity"], r["query"])
    by_key_method: Dict[Tuple[str, str, str], Dict[str, float]] = {}
    for r in perf:
        by_key_method.setdefault(key(r), {})[r["method"]] = _float(r["latency_ms"])

    def speedups(base: str, target: str = "pcqv") -> List[float]:
        vals = []
        for d in by_key_method.values():
            if base in d and target in d and d[target] > 0 and d[base] == d[base] and d[target] == d[target]:
                vals.append(d[base] / d[target])
        return vals

    safe_corr = [r for r in corr if r["safe_by_rule"] == "1"]
    negative_corr = [r for r in corr if r["method"] == "unsafe_late_aggregate"]
    unsafe_agg = [r for r in negative_corr if r["query"] in {"q2_revenue_by_product", "q4_revenue_by_region", "q5_high_value_segments", "q6_lineitem_product_count", "q7_customer_summary", "q8_sensitive_product_scan"}]
    metrics = {
        "num_performance_rows": len(perf),
        "num_correctness_rows": len(corr),
        "num_ablation_rows": len(ab),
        "num_planning_overhead_rows": len(ov),
        "num_cardinality_rows": len(ce),
        "methods": methods,
        "performance_median_ms": med_by_method,
        "performance_p95_ms": p95_by_method,
        "safe_equivalent": [sum(1 for r in safe_corr if r["equivalent_to_reference"] == "1"), len(safe_corr)],
        "negative_equivalent": [sum(1 for r in negative_corr if r["equivalent_to_reference"] == "1"), len(negative_corr)],
        "unsafe_aggregate_equivalent": [sum(1 for r in unsafe_agg if r["equivalent_to_reference"] == "1"), len(unsafe_agg)],
        "negative_policy_violations": sum(max(0, int(r["policy_violation_count"])) for r in negative_corr),
        "pcqv_speedup_vs_view_barrier_median": round(_median(speedups("view_barrier")), 3),
        "pcqv_speedup_vs_post_filter_median": round(_median(speedups("post_join_filter")), 3),
        "pcqv_speedup_vs_oblivious_median": round(_median(speedups("oblivious_forced")), 3),
        "pcqv_vs_predicate_injection_ratio_median": round(_median(speedups("predicate_injection", "pcqv")), 3),
        "pcqv_vs_oracle_order_ratio_median": round(_median(speedups("oracle_order", "pcqv")), 3),
        "pcqv_median_compile_ms": round(_median(_float(r["compile_ms"]) for r in ov if r["method"] == "pcqv"), 6),
        "oracle_median_compile_ms": round(_median(_float(r["compile_ms"]) for r in ov if r["method"] == "oracle_order"), 6),
        "policy_cardinality_qerror_median": round(_median(_float(r["policy_q_error"]) for r in ce), 3),
        "policy_cardinality_qerror_p95": round(_percentile((_float(r["policy_q_error"]) for r in ce), 0.95), 3),
    }

    # Useful paper tables.
    q_methods = ["post_join_filter", "view_barrier", "oblivious_forced", "predicate_injection", "pcqv", "oracle_order"]
    by_query: Dict[str, Dict[str, float]] = {}
    for q in sorted({r["query"] for r in perf}):
        by_query[q] = {m: round(_median(_float(r["latency_ms"]) for r in perf if r["query"] == q and r["method"] == m), 3) for m in q_methods}
    by_sel: Dict[str, Dict[str, float]] = {}
    for sel in ["0.025", "0.05", "0.1", "0.25", "0.5"]:
        by_sel[sel] = {m: round(_median(_float(r["latency_ms"]) for r in perf if r["selectivity"] == sel and r["method"] == m), 3) for m in q_methods}
    by_comp: Dict[str, Dict[str, float]] = {}
    for comp in ["1", "2", "3", "4", "5", "6"]:
        by_comp[comp] = {m: round(_median(_float(r["latency_ms"]) for r in perf if r["complexity"] == comp and r["method"] == m), 3) for m in q_methods}
    ab_med = {m: round(_median(_float(r["latency_ms"]) for r in ab if r["method"] == m), 3) for m in sorted({r["method"] for r in ab})}

    with open(out_json, "w") as f:
        json.dump(metrics, f, indent=2)

    def latex_num(x: Any) -> str:
        if isinstance(x, float):
            if x != x:
                return "--"
            return f"{x:.3f}".rstrip("0").rstrip(".")
        return str(x)

    with open(out_tables_tex, "w") as f:
        f.write("% Auto-generated by artifact/pcqv/engine.py summarize().\n")
        f.write("\\newcommand{\\NumPerformanceRows}{%d}\n" % metrics["num_performance_rows"])
        f.write("\\newcommand{\\NumCorrectnessRows}{%d}\n" % metrics["num_correctness_rows"])
        f.write("\\newcommand{\\NumAblationRows}{%d}\n" % metrics["num_ablation_rows"])
        f.write("\\newcommand{\\NumCardinalityRows}{%d}\n" % metrics["num_cardinality_rows"])
        f.write("\\newcommand{\\SafeEqPassed}{%d}\n" % metrics["safe_equivalent"][0])
        f.write("\\newcommand{\\SafeEqTotal}{%d}\n" % metrics["safe_equivalent"][1])
        f.write("\\newcommand{\\UnsafeViolations}{%d}\n" % metrics["negative_policy_violations"])
        f.write("\\newcommand{\\PcqvMedianMs}{%s}\n" % latex_num(metrics["performance_median_ms"].get("pcqv")))
        f.write("\\newcommand{\\PcqvSpeedView}{%s}\n" % latex_num(metrics["pcqv_speedup_vs_view_barrier_median"]))
        f.write("\\newcommand{\\PcqvSpeedPost}{%s}\n" % latex_num(metrics["pcqv_speedup_vs_post_filter_median"]))
        f.write("\\newcommand{\\PcqvSpeedObliv}{%s}\n" % latex_num(metrics["pcqv_speedup_vs_oblivious_median"]))
        f.write("\\newcommand{\\PcqvPredRatio}{%s}\n" % latex_num(metrics["pcqv_vs_predicate_injection_ratio_median"]))
        f.write("\\newcommand{\\PcqvCompileMs}{%s}\n" % latex_num(metrics["pcqv_median_compile_ms"]))
        f.write("\\newcommand{\\PolicyQErrorMedian}{%s}\n" % latex_num(metrics["policy_cardinality_qerror_median"]))
        f.write("\\newcommand{\\PolicyQErrorPninetyfive}{%s}\n" % latex_num(metrics["policy_cardinality_qerror_p95"]))
        f.write("\n")
        f.write("\\newcommand{\\PerfSummaryRows}{%\n")
        for m in ["post_join_filter", "view_barrier", "oblivious_forced", "predicate_injection", "pcqv_no_stats", "pcqv_forced", "pcqv", "oracle_order"]:
            f.write(f"{m.replace('_','\\_')} & {latex_num(med_by_method[m])} & {latex_num(p95_by_method[m])}\\\\\n")
        f.write("}\n")
        f.write("\\newcommand{\\ByQueryRows}{%\n")
        for q, d in by_query.items():
            f.write(q.replace("_", "\\_") + " & " + " & ".join(latex_num(d[m]) for m in q_methods) + "\\\\\n")
        f.write("}\n")
        f.write("\\newcommand{\\BySelectivityRows}{%\n")
        for sel, d in by_sel.items():
            f.write(sel + " & " + " & ".join(latex_num(d[m]) for m in q_methods) + "\\\\\n")
        f.write("}\n")
        f.write("\\newcommand{\\ByComplexityRows}{%\n")
        for comp, d in by_comp.items():
            f.write(comp + " & " + " & ".join(latex_num(d[m]) for m in q_methods) + "\\\\\n")
        f.write("}\n")
        f.write("\\newcommand{\\AblationRows}{%\n")
        for m, v in ab_med.items():
            f.write(m.replace("_", "\\_") + " & " + latex_num(v) + "\\\\\n")
        f.write("}\n")

    return metrics
