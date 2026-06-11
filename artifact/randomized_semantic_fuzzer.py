#!/usr/bin/env python3
"""Randomized semantic fuzzing for PCQV.

This checker generates fresh SQL-core templates (not the fixed benchmark suite),
compiles each under the protected-view reference, safe encodings, and unsafe
mutations, and compares multiset-normalized outputs.  It is intentionally small
and deterministic so that reviewers can rerun it on CPU-only SQLite.
"""
from __future__ import annotations

import argparse, csv, json, os, random, sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pcqv.engine import (  # type: ignore
    Context, Filter, Join, PolicyManager, QueryTemplate, SQLCompiler, Stats,
    TableRef, connect, execute_rows_bounded, context_for_selectivity,
    method_safety, multiset_violation, prepare_database, symmetric_difference_count,
)

TABLES = {
    "customers": ("c", ["region", "segment", "risk", "vip", "opted_in"]),
    "orders": ("o", ["amount", "status", "purpose_tag", "created_day"]),
    "lineitem": ("l", ["qty", "price"]),
    "products": ("p", ["category", "sensitivity", "active"]),
    "tickets": ("t", ["severity", "topic", "created_day", "pii_flag"]),
}

JOIN_LIBRARY = [
    ("orders", "customers", Join("o", "cid", "c", "cid")),
    ("lineitem", "orders", Join("l", "oid", "o", "oid")),
    ("lineitem", "products", Join("l", "pid", "p", "pid")),
    ("tickets", "customers", Join("t", "cid", "c", "cid")),
]

FILTER_LIBRARY: Dict[str, List[Tuple[str, float, str]]] = {
    "customers": [("{a}.region IN ('NA','EU','APAC')", .38, "region3"), ("{a}.risk <= 4", .86, "risk4"), ("{a}.segment IN ('S1','S3','S5')", .60, "seg-odd"), ("{a}.vip IN (0,1)", 1.0, "vip-all")],
    "orders": [("{a}.amount >= 150", .36, "amount150"), ("{a}.status <> 'internal'", .88, "not-internal"), ("{a}.created_day BETWEEN 45 AND 320", .75, "date-mid")],
    "lineitem": [("{a}.qty BETWEEN 1 AND 4", .80, "qty14"), ("{a}.price >= 8", .78, "price8")],
    "products": [("{a}.category IN ('C1','C2','C4','C7')", .44, "cat4"), ("{a}.sensitivity <= 4", .84, "sens4"), ("{a}.active = 1", .92, "active")],
    "tickets": [("{a}.severity >= 2", .80, "sev2"), ("{a}.topic <> 'fraud'", .80, "topic-not"), ("{a}.created_day BETWEEN 15 AND 350", .92, "ticket-date")],
}

SAFE_METHODS = ["post_join_filter", "view_barrier", "predicate_injection", "pcqv_forced", "pcqv", "oracle_order"]
UNSAFE_METHODS = ["unsafe_late_aggregate", "mut_no_mask", "mut_drop_tenant", "mut_drop_purpose", "mut_drop_clearance", "mut_drop_region", "mut_drop_cross_guard", "mut_drop_acl", "mut_no_group_guard"]


def connected_subset(rng: random.Random) -> Tuple[List[str], List[Join]]:
    # Draw a connected subgraph by growing from one table along join edges.
    tables = [rng.choice(list(TABLES))]
    joins: List[Join] = []
    while len(tables) < rng.randint(1, 4):
        candidates = [e for e in JOIN_LIBRARY if (e[0] in tables) ^ (e[1] in tables)]
        if not candidates:
            break
        a, b, j = rng.choice(candidates)
        new = b if a in tables else a
        tables.append(new); joins.append(j)
    return tables, joins


def build_template(rng: random.Random, i: int) -> QueryTemplate:
    tables, joins = connected_subset(rng)
    refs = [TableRef(t, TABLES[t][0]) for t in tables]
    filters: List[Filter] = []
    for t in tables:
        for expr, sel, label in rng.sample(FILTER_LIBRARY[t], rng.randint(0, min(2, len(FILTER_LIBRARY[t])))):
            filters.append(Filter(TABLES[t][0], expr, sel, f"fz-{label}"))
    aggregate = rng.random() < 0.55 and len(tables) >= 2
    aliases = {t: TABLES[t][0] for t in tables}
    if aggregate:
        group_candidates = []
        if "customers" in tables: group_candidates += ["c.region", "c.segment"]
        if "products" in tables: group_candidates += ["p.category", "p.sensitivity"]
        if "tickets" in tables: group_candidates += ["t.topic"]
        if not group_candidates:
            group_candidates = [f"{refs[0].alias}.tid"]
        group_by = rng.sample(group_candidates, rng.randint(1, min(2, len(group_candidates))))
        select_exprs = [(g, g.split('.')[-1]) for g in group_by]
        if "orders" in tables:
            select_exprs.append(("SUM(o.amount)", "sum_amount"))
        elif "lineitem" in tables:
            select_exprs.append(("SUM(l.qty*l.price)", "sum_value"))
        else:
            select_exprs.append(("COUNT(*)", "n"))
        select_exprs.append(("COUNT(*)", "n"))
        return QueryTemplate(f"fz{i:03d}", refs, joins, filters, select_exprs, group_by=group_by, result_kind="aggregate")
    else:
        select_exprs = []
        for t in tables:
            a = TABLES[t][0]
            pk = {"customers":"cid", "orders":"oid", "lineitem":"lid", "products":"pid", "tickets":"ticket_id"}[t]
            select_exprs.append((f"{a}.{pk}", f"{a}_{pk}"))
        if "customers" in tables:
            select_exprs.append(("MASK(c.email)", "email"))
        # A deterministic key order is part of the oracle contract for LIMIT;
        # otherwise two semantically equivalent plans can return different
        # prefixes under a non-unique ORDER BY.
        pk = {"customers":"cid", "orders":"oid", "lineitem":"lid", "products":"pid", "tickets":"ticket_id"}
        order_by = ", ".join(f"{TABLES[t][0]}.{pk[t]}" for t in tables)
        return QueryTemplate(f"fz{i:03d}", refs, joins, filters, select_exprs, order_by=order_by, limit=200, result_kind="rows")


def run(db_path: str, out_csv: str, out_json: str, n: int, seed: int) -> None:
    conn = connect(db_path)
    stats = Stats(conn)
    compiler = SQLCompiler(stats, conn)
    rng = random.Random(seed)
    rows = []
    first_failures = []
    try:
        for i in range(1, n + 1):
            q = build_template(rng, i)
            sel = rng.choice([0.025, 0.05, 0.10, 0.25, 0.50])
            comp = rng.choice([1,2,3,4,5,6])
            ctx = context_for_selectivity(sel, purpose=rng.choice(["analytics", "support", "billing"]))
            pol = PolicyManager(comp, ctx)
            try:
                ref_sql = compiler.compile(q, pol, "reference")
                ref = execute_rows_bounded(conn, ref_sql, seconds=0.45)
                ref_err = ""
            except Exception as e:
                ref = []
                ref_err = str(e).splitlines()[0][:200]
            for method in SAFE_METHODS + UNSAFE_METHODS:
                safe, reason = method_safety(q, method)
                # Force mutations/late aggregate to be unsafe diagnostics; some can be no-op on templates.
                if method in UNSAFE_METHODS:
                    safe = False
                try:
                    sql = compiler.compile(q, pol, method)
                    got = execute_rows_bounded(conn, sql, seconds=0.45)
                    equiv = (got == ref) and not ref_err
                    diff = 0 if equiv else symmetric_difference_count(got, ref)
                    viol = 0 if equiv else multiset_violation(got, ref)
                    err = ""
                except Exception as e:
                    equiv = False; diff = -1; viol = -1; err = str(e).splitlines()[0][:200]
                rows.append({
                    "case": i, "query": q.name, "kind": q.result_kind, "tables": "+".join(t.table for t in q.tables),
                    "selectivity": sel, "complexity": comp, "method": method,
                    "declared_safe": int(safe), "equivalent": int(equiv),
                    "symmetric_difference_count": diff, "policy_violation_count": viol,
                    "error": err or ref_err,
                })
                if (safe and not equiv or (not safe and not equiv and len(first_failures) < 25)) and len(first_failures) < 25:
                    first_failures.append({"case": i, "method": method, "safe": safe, "reason": reason, "diff": diff, "violation": viol})
    finally:
        conn.close()
    os.makedirs(os.path.dirname(out_csv), exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader(); w.writerows(rows)
    safe_rows = [r for r in rows if r["declared_safe"]]
    unsafe_rows = [r for r in rows if not r["declared_safe"]]
    summary = {
        "random_templates": n,
        "rows": len(rows),
        "safe_rows": len(safe_rows),
        "safe_equivalent": [sum(r["equivalent"] for r in safe_rows), len(safe_rows)],
        "unsafe_rows": len(unsafe_rows),
        "unsafe_non_equivalent": [sum(1 - r["equivalent"] for r in unsafe_rows), len(unsafe_rows)],
        "unsafe_differences": sum(max(0, int(r["symmetric_difference_count"])) for r in unsafe_rows),
        "unsafe_violations": sum(max(0, int(r["policy_violation_count"])) for r in unsafe_rows),
        "errors": sum(1 for r in rows if r["error"]),
        "first_failures": first_failures,
    }
    with open(out_json, "w") as f:
        json.dump(summary, f, indent=2)
    if summary["safe_equivalent"][0] != summary["safe_equivalent"][1]:
        raise SystemExit("safe randomized semantic fuzzing failure")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT))
    ap.add_argument("--db", default=None)
    ap.add_argument("--n", type=int, default=240)
    ap.add_argument("--seed", type=int, default=90210)
    args = ap.parse_args()
    root = Path(args.out)
    db = args.db or str(root / "data" / "pcqv_randomized_semantic_fuzz.db")
    if not Path(db).exists():
        prepare_database(db, seed=args.seed + 7, scale=0.04)
    run(db, str(root / "results" / "randomized_semantic_fuzz.csv"), str(root / "results" / "randomized_semantic_fuzz.json"), args.n, args.seed)

if __name__ == "__main__":
    main()
