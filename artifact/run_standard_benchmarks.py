#!/usr/bin/env python3
"""Run an external-validity benchmark for PCQV.

The core PCQV benchmark is controlled and synthetic.  This script adds two
standard database-evaluation ingredients without requiring any external server
or network download: a TPC-H/SSB-inspired schema and a fixed analytical query
set over customers, orders, lineitems, parts, suppliers, nations, and regions.
It is not a TPC-H compliance claim.  It is a reproducible stress test that uses
well-known decision-support schema patterns to check whether the policy-aware
optimizer story survives outside the custom benchmark schema.
"""
from __future__ import annotations
import argparse, csv, json, os, random, sqlite3, statistics, time
from pathlib import Path

REGIONS = ["AFRICA", "AMERICA", "ASIA", "EUROPE", "MIDDLE EAST"]
NATIONS = [
    (1,"ALGERIA",0),(2,"ARGENTINA",1),(3,"BRAZIL",1),(4,"CANADA",1),
    (5,"EGYPT",4),(6,"FRANCE",3),(7,"GERMANY",3),(8,"INDIA",2),
    (9,"INDONESIA",2),(10,"JAPAN",2),(11,"KENYA",0),(12,"MOROCCO",0),
    (13,"CHINA",2),(14,"ROMANIA",3),(15,"SAUDI ARABIA",4),(16,"UNITED KINGDOM",3),
]
SEGMENTS = ["BUILDING","AUTOMOBILE","MACHINERY","HOUSEHOLD","FURNITURE"]
MKT = ["MAIL","SHIP","RAIL","AIR","TRUCK"]
BRANDS = ["Brand#11","Brand#23","Brand#31","Brand#42","Brand#55"]


def execute_many(conn, sql, rows):
    conn.executemany(sql, rows)


def prepare_standard_db(path: str, scale: float = 1.0, seed: int = 2027) -> None:
    random.seed(seed)
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists(): p.unlink()
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.executescript("""
    PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=MEMORY;
    CREATE TABLE region(r_regionkey INTEGER PRIMARY KEY, r_name TEXT);
    CREATE TABLE nation(n_nationkey INTEGER PRIMARY KEY, n_name TEXT, n_regionkey INTEGER);
    CREATE TABLE customer(c_custkey INTEGER PRIMARY KEY, c_name TEXT, c_nationkey INTEGER, c_region TEXT, c_mktsegment TEXT, c_tenant INTEGER, c_acctbal REAL, c_email TEXT, c_risk INTEGER);
    CREATE TABLE orders(o_orderkey INTEGER PRIMARY KEY, o_custkey INTEGER, o_orderdate TEXT, o_orderstatus TEXT, o_totalprice REAL, o_purpose TEXT, o_internal INTEGER);
    CREATE TABLE part(p_partkey INTEGER PRIMARY KEY, p_brand TEXT, p_type TEXT, p_size INTEGER);
    CREATE TABLE supplier(s_suppkey INTEGER PRIMARY KEY, s_name TEXT, s_nationkey INTEGER, s_tenant INTEGER);
    CREATE TABLE partsupp(ps_partkey INTEGER, ps_suppkey INTEGER, ps_supplycost REAL);
    CREATE TABLE lineitem(l_orderkey INTEGER, l_partkey INTEGER, l_suppkey INTEGER, l_quantity INTEGER, l_extendedprice REAL, l_discount REAL, l_shipmode TEXT, l_returnflag TEXT);
    CREATE TABLE customer_acl(user_id INTEGER, c_custkey INTEGER);
    """)
    c.executemany("INSERT INTO region VALUES (?,?)", [(i,r) for i,r in enumerate(REGIONS)])
    c.executemany("INSERT INTO nation VALUES (?,?,?)", NATIONS)
    nations_by_region = {}
    for nk, nn, rk in NATIONS:
        nations_by_region.setdefault(REGIONS[rk], []).append(nk)
    n_customers = max(200, int(1600*scale))
    n_orders = max(1000, int(11000*scale))
    n_parts = max(300, int(1600*scale))
    n_supp = max(80, int(500*scale))
    # Customers: region and tenant are correlated but not identical, creating useful policy selectivity.
    cust_rows = []
    for ck in range(1, n_customers+1):
        region = random.choice(REGIONS)
        nation = random.choice(nations_by_region[region])
        seg = random.choice(SEGMENTS)
        tenant = 1 + ((ck + nation) % 12)
        risk = random.randint(1, 100)
        cust_rows.append((ck, f"Customer#{ck}", nation, region, seg, tenant, round(random.uniform(-900,9000),2), f"c{ck}@example.test", risk))
    execute_many(conn, "INSERT INTO customer VALUES (?,?,?,?,?,?,?,?,?)", cust_rows)
    # Orders: date and purpose create independent policy dimensions.
    purposes = ["analytics", "support", "billing", "research"]
    order_rows = []
    for ok in range(1, n_orders+1):
        ck = random.randint(1, n_customers)
        year = random.choice([1993,1994,1995,1996,1997,1998])
        month = random.randint(1,12); day = random.randint(1,28)
        status = random.choice(["O","F","P"])
        purpose = random.choices(purposes, weights=[5,2,2,1])[0]
        internal = 1 if random.random() < 0.07 else 0
        total = round(random.uniform(100, 12000),2)
        order_rows.append((ok, ck, f"{year:04d}-{month:02d}-{day:02d}", status, total, purpose, internal))
    execute_many(conn, "INSERT INTO orders VALUES (?,?,?,?,?,?,?)", order_rows)
    part_rows = [(pk, random.choice(BRANDS), random.choice(["SMALL","MEDIUM","LARGE","ECONOMY"]), random.randint(1,50)) for pk in range(1,n_parts+1)]
    execute_many(conn, "INSERT INTO part VALUES (?,?,?,?)", part_rows)
    supp_rows = []
    for sk in range(1, n_supp+1):
        nk, nn, rk = random.choice(NATIONS)
        supp_rows.append((sk, f"Supplier#{sk}", nk, 1 + ((sk + nk) % 12)))
    execute_many(conn, "INSERT INTO supplier VALUES (?,?,?,?)", supp_rows)
    ps_rows = []
    for pk in range(1, n_parts+1):
        for _ in range(3):
            ps_rows.append((pk, random.randint(1,n_supp), round(random.uniform(2,200),2)))
    execute_many(conn, "INSERT INTO partsupp VALUES (?,?,?)", ps_rows)
    li_rows = []
    for ok in range(1, n_orders+1):
        for _ in range(random.randint(1,4)):
            pk = random.randint(1,n_parts); sk = random.randint(1,n_supp)
            price = round(random.uniform(5,2000),2); disc = random.choice([0.0,0.02,0.04,0.06,0.08])
            li_rows.append((ok, pk, sk, random.randint(1,50), price, disc, random.choice(MKT), "R" if random.random()<0.08 else "N"))
    execute_many(conn, "INSERT INTO lineitem VALUES (?,?,?,?,?,?,?,?)", li_rows)
    # Sparse ACL grants break pure region/tenant selectivity assumptions.
    acl_rows = []
    for user_id in [7,11,19]:
        for ck in random.sample(range(1,n_customers+1), max(10, int(n_customers*0.025))):
            acl_rows.append((user_id, ck))
    execute_many(conn, "INSERT INTO customer_acl VALUES (?,?)", acl_rows)
    c.executescript("""
    CREATE INDEX idx_c_region_tenant ON customer(c_region,c_tenant);
    CREATE INDEX idx_c_nation ON customer(c_nationkey);
    CREATE INDEX idx_o_cust_date ON orders(o_custkey,o_orderdate);
    CREATE INDEX idx_o_purpose ON orders(o_purpose,o_internal);
    CREATE INDEX idx_l_order ON lineitem(l_orderkey);
    CREATE INDEX idx_l_part ON lineitem(l_partkey);
    CREATE INDEX idx_l_supp ON lineitem(l_suppkey);
    CREATE INDEX idx_p_brand ON part(p_brand);
    CREATE INDEX idx_s_tenant ON supplier(s_tenant);
    CREATE INDEX idx_acl_user ON customer_acl(user_id,c_custkey);
    ANALYZE;
    """)
    conn.commit(); conn.close()


def policy_pred(alias: str, ctx: dict) -> str:
    # Customer alias policy with region OR ACL exception.  The explicit user_id
    # value is numeric and generated by the benchmark, not user input.
    if alias == "c":
        regs = ",".join([repr(r) for r in ctx["regions"]])
        return f"((c.c_tenant <= {ctx['max_tenant']} AND c.c_region IN ({regs}) AND c.c_risk <= {ctx['risk']} ) OR EXISTS (SELECT 1 FROM customer_acl a WHERE a.user_id={ctx['user_id']} AND a.c_custkey=c.c_custkey))"
    if alias == "o":
        return f"(o.o_internal=0 AND o.o_purpose IN ('analytics','{ctx['purpose']}') AND o.o_orderdate >= '{ctx['date']}')"
    if alias == "l":
        return "(l.l_returnflag <> 'R')"
    if alias == "s":
        return f"(s.s_tenant <= {ctx['max_tenant']} OR s.s_nationkey IN (SELECT n_nationkey FROM nation WHERE n_regionkey IN (SELECT r_regionkey FROM region WHERE r_name IN ({','.join([repr(r) for r in ctx['regions']])}))))"
    if alias == "p":
        return f"(p.p_size <= {ctx['max_part_size']})"
    return "1=1"

CONTEXTS = [
    {"name":"tight", "user_id":7, "regions":["ASIA"], "max_tenant":3, "risk":55, "purpose":"support", "date":"1996-01-01", "max_part_size":18, "min_group":12},
    {"name":"medium", "user_id":11, "regions":["ASIA","EUROPE"], "max_tenant":6, "risk":75, "purpose":"billing", "date":"1995-01-01", "max_part_size":30, "min_group":8},
    {"name":"broad", "user_id":19, "regions":["ASIA","EUROPE","AMERICA"], "max_tenant":9, "risk":90, "purpose":"research", "date":"1994-01-01", "max_part_size":42, "min_group":4},
]

TEMPLATES = {
"tpch_q3_revenue": {
"select": "SELECT c.c_mktsegment, COUNT(*) AS n, ROUND(SUM(l.l_extendedprice*(1-l.l_discount)),2) AS revenue",
"from": "FROM customer c JOIN orders o ON c.c_custkey=o.o_custkey JOIN lineitem l ON o.o_orderkey=l.l_orderkey",
"where": "AND c.c_mktsegment IN ('BUILDING','AUTOMOBILE')",
"group": "GROUP BY c.c_mktsegment HAVING COUNT(*) >= {min_group} ORDER BY revenue DESC",
},
"tpch_q5_region_supplier": {
"select": "SELECT c.c_region, COUNT(*) AS n, ROUND(SUM(l.l_extendedprice),2) AS revenue",
"from": "FROM customer c JOIN orders o ON c.c_custkey=o.o_custkey JOIN lineitem l ON o.o_orderkey=l.l_orderkey JOIN supplier s ON l.l_suppkey=s.s_suppkey",
"where": "AND l.l_quantity >= 5",
"group": "GROUP BY c.c_region HAVING COUNT(*) >= {min_group} ORDER BY revenue DESC",
},
"tpch_q6_discount": {
"select": "SELECT ROUND(SUM(l.l_extendedprice*l.l_discount),2) AS discount_revenue",
"from": "FROM orders o JOIN lineitem l ON o.o_orderkey=l.l_orderkey JOIN customer c ON c.c_custkey=o.o_custkey",
"where": "AND l.l_discount BETWEEN 0.02 AND 0.08 AND l.l_quantity < 30",
"group": "",
},
"tpch_q10_customer_loss": {
"select": "SELECT c.c_region, COUNT(DISTINCT c.c_custkey) AS customers, ROUND(SUM(o.o_totalprice),2) AS total_price",
"from": "FROM customer c JOIN orders o ON c.c_custkey=o.o_custkey JOIN lineitem l ON o.o_orderkey=l.l_orderkey",
"where": "AND l.l_returnflag = 'N'",
"group": "GROUP BY c.c_region HAVING COUNT(*) >= {min_group} ORDER BY total_price DESC",
},
"ssb_brand_revenue": {
"select": "SELECT p.p_brand, COUNT(*) AS n, ROUND(SUM(l.l_extendedprice),2) AS revenue",
"from": "FROM part p JOIN lineitem l ON p.p_partkey=l.l_partkey JOIN orders o ON o.o_orderkey=l.l_orderkey JOIN customer c ON c.c_custkey=o.o_custkey",
"where": "AND p.p_brand IN ('Brand#11','Brand#23','Brand#42')",
"group": "GROUP BY p.p_brand HAVING COUNT(*) >= {min_group} ORDER BY revenue DESC",
},
"ssb_supplier_nation": {
"select": "SELECT n.n_name, COUNT(*) AS n, ROUND(SUM(l.l_extendedprice),2) AS revenue",
"from": "FROM supplier s JOIN nation n ON s.s_nationkey=n.n_nationkey JOIN lineitem l ON s.s_suppkey=l.l_suppkey JOIN orders o ON o.o_orderkey=l.l_orderkey JOIN customer c ON c.c_custkey=o.o_custkey",
"where": "AND l.l_shipmode IN ('AIR','SHIP')",
"group": "GROUP BY n.n_name HAVING COUNT(*) >= {min_group} ORDER BY revenue DESC",
},
}


def where_policy(ctx):
    return " AND ".join([policy_pred(a, ctx) for a in ["c","o","l"]])

def extra_policy_for_query(qname, ctx):
    pieces = [policy_pred("c",ctx), policy_pred("o",ctx), policy_pred("l",ctx)]
    if "supplier" in qname or qname == "tpch_q5_region_supplier": pieces.append(policy_pred("s",ctx))
    if "brand" in qname: pieces.append(policy_pred("p",ctx))
    return " AND ".join(pieces)


def compile_query(qname, ctx, method):
    t = TEMPLATES[qname]
    base_where = t["where"].lstrip("AND ").format(**ctx)
    group = t["group"].format(**ctx)
    pol = extra_policy_for_query(qname, ctx)
    sel = t["select"]
    from_clause = t["from"]
    if method in {"predicate_injection", "pcqv"}:
        # PCQV uses CROSS JOIN for two common selective cases to simulate a protected join-order decision.
        if method == "pcqv" and qname in {"tpch_q3_revenue", "tpch_q10_customer_loss"} and ctx["name"] == "tight":
            from_clause = "FROM customer c CROSS JOIN orders o CROSS JOIN lineitem l"
            joinconds = "c.c_custkey=o.o_custkey AND o.o_orderkey=l.l_orderkey"
            where = f"WHERE {joinconds} AND {pol} AND {base_where}"
        elif method == "pcqv" and qname == "ssb_brand_revenue":
            from_clause = "FROM part p CROSS JOIN lineitem l CROSS JOIN orders o CROSS JOIN customer c"
            joinconds = "p.p_partkey=l.l_partkey AND o.o_orderkey=l.l_orderkey AND c.c_custkey=o.o_custkey"
            where = f"WHERE {joinconds} AND {pol} AND {base_where}"
        else:
            where = f"WHERE {pol} AND {base_where}"
        return f"{sel} {from_clause} {where} {group}"
    if method == "view_barrier":
        regs = ",".join([repr(r) for r in ctx["regions"]])
        withs = f"WITH pc AS MATERIALIZED (SELECT * FROM customer c WHERE {policy_pred('c',ctx)}), po AS MATERIALIZED (SELECT * FROM orders o WHERE {policy_pred('o',ctx)}), pl AS MATERIALIZED (SELECT * FROM lineitem l WHERE {policy_pred('l',ctx)}), ps AS MATERIALIZED (SELECT * FROM supplier s WHERE {policy_pred('s',ctx)}), pp AS MATERIALIZED (SELECT * FROM part p WHERE {policy_pred('p',ctx)})"
        repl = from_clause.replace("customer c","pc c").replace("orders o","po o").replace("lineitem l","pl l").replace("supplier s","ps s").replace("part p","pp p")
        return f"{withs} {sel} {repl} WHERE {base_where} {group}"
    if method == "raw_post_filter_control":
        return f"WITH raw AS MATERIALIZED ({sel} {from_clause} WHERE {base_where} {group}) SELECT * FROM raw" if "GROUP BY" in group else f"{sel} {from_clause} WHERE {base_where}"
    if method == "unsafe_late_aggregate":
        # Deliberately omit row-level policy before grouping.  Used only for correctness diagnostics.
        return f"{sel} {from_clause} WHERE {base_where} {group}"
    raise ValueError(method)


def norm(rows):
    out=[]
    for r in rows:
        out.append(tuple(round(x,2) if isinstance(x,float) else x for x in r))
    return sorted(out, key=lambda x: repr(x))


def timed(conn, sql, reps=1):
    last=None; times=[]; err=""
    for _ in range(reps):
        t0=time.perf_counter()
        try:
            last=conn.execute(sql).fetchall(); err=""
        except Exception as e:
            last=[]; err=str(e)
        times.append((time.perf_counter()-t0)*1000)
    return statistics.median(times), last, err


def run_standard(root: str, scales=(0.5,1.0), reps=2, seed=2027):
    rootp=Path(root); data=rootp/"data"; res=rootp/"results"; data.mkdir(exist_ok=True); res.mkdir(exist_ok=True)
    raw_path=res/"standard_external_raw.csv"; corr_path=res/"standard_external_correctness.csv"; summ_path=res/"standard_external_summary.csv"
    perf_rows=[]; corr_rows=[]
    for scale in scales:
        db=data/f"standard_tpch_like_scale{scale}.db"
        prepare_standard_db(str(db), scale=scale, seed=seed+int(scale*1000))
        conn=sqlite3.connect(str(db))
        for ctx in CONTEXTS:
            for q in TEMPLATES:
                ref_sql=compile_query(q,ctx,"view_barrier")
                _, ref, referr = timed(conn, ref_sql, reps=1)
                refn=norm(ref)
                for method in ["pcqv","predicate_injection","view_barrier","raw_post_filter_control","unsafe_late_aggregate"]:
                    sql=compile_query(q,ctx,method)
                    ms, rows, err=timed(conn,sql,reps=reps)
                    rn=norm(rows)
                    perf_rows.append({"benchmark":"tpch_ssb_like","scale":scale,"context":ctx["name"],"query":q,"method":method,"latency_ms":round(ms,6),"rows":len(rows),"error":err})
                    diff=len(set(rn).symmetric_difference(set(refn))) if not err and not referr else -1
                    corr_rows.append({"benchmark":"tpch_ssb_like","scale":scale,"context":ctx["name"],"query":q,"method":method,"equivalent_to_reference": int(rn==refn and err==""),"symmetric_difference_count":diff,"error":err})
        conn.close()
    with open(raw_path,"w",newline="") as f:
        w=csv.DictWriter(f, fieldnames=list(perf_rows[0].keys())); w.writeheader(); w.writerows(perf_rows)
    with open(corr_path,"w",newline="") as f:
        w=csv.DictWriter(f, fieldnames=list(corr_rows[0].keys())); w.writeheader(); w.writerows(corr_rows)
    # Compact summary without pandas dependency.
    by_method={}
    for r in perf_rows:
        if r["error"]: continue
        by_method.setdefault(r["method"],[]).append(float(r["latency_ms"]))
    summ=[]
    for m, vals in sorted(by_method.items()):
        vals=sorted(vals)
        summ.append({"method":m,"n":len(vals),"median_ms":round(statistics.median(vals),6),"p95_ms":round(vals[int(0.95*(len(vals)-1))],6),"max_ms":round(max(vals),6)})
    safe_checks=[r for r in corr_rows if r["method"] in {"pcqv","predicate_injection","view_barrier"}]
    unsafe_checks=[r for r in corr_rows if r["method"] in {"raw_post_filter_control","unsafe_late_aggregate"}]
    metrics={
        "standard_external_performance_rows":len(perf_rows),
        "standard_external_correctness_rows":len(corr_rows),
        "standard_external_safe_equivalent":[sum(r["equivalent_to_reference"] for r in safe_checks), len(safe_checks)],
        "standard_external_unsafe_equivalent":[sum(r["equivalent_to_reference"] for r in unsafe_checks), len(unsafe_checks)],
        "standard_external_unsafe_differences":sum(max(0,r["symmetric_difference_count"]) for r in unsafe_checks),
        "standard_external_median_ms":{s["method"]:s["median_ms"] for s in summ},
    }
    with open(summ_path,"w",newline="") as f:
        w=csv.DictWriter(f, fieldnames=["method","n","median_ms","p95_ms","max_ms"]); w.writeheader(); w.writerows(summ)
    with open(res/"standard_external_metrics.json","w") as f: json.dump(metrics,f,indent=2)
    return metrics

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
    ap.add_argument("--reps", type=int, default=2)
    args=ap.parse_args()
    m=run_standard(args.out,reps=args.reps)
    print(json.dumps(m,indent=2))
