#!/usr/bin/env python3
"""SQL portability and plan-shape audit for PCQV.

Generates SQL for all controlled templates across representative policy contexts,
checks for submission-unfriendly or dialect-fragile constructs, and confirms that
SQLite can produce a query plan for every emitted safe candidate.  This does not
claim PostgreSQL/DuckDB execution; it is a mechanical gate that the compiler is
not secretly emitting DDL, PRAGMAs, temp tables, file paths, or ad-hoc debugging SQL.
"""
from __future__ import annotations
import csv, json, re, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from pcqv.engine import (Context, PolicyManager, SQLCompiler, Stats, connect,
                         context_for_selectivity, method_safety, prepare_database,
                         query_templates)

SAFE_METHODS = ['reference','post_join_filter','view_barrier','predicate_injection','pcqv_forced','pcqv','oracle_order']
DISALLOWED = re.compile(r"\b(PRAGMA|ATTACH|DETACH|CREATE|DROP|INSERT|UPDATE|DELETE|ALTER|VACUUM|ANALYZE|LOAD_EXTENSION)\b", re.I)
PATHLIKE = re.compile(r"(/mnt/|artifact/|results/|\.py\b|\.csv\b|\.json\b)")


def explain(conn: sqlite3.Connection, sql: str) -> list[str]:
    rows = conn.execute('EXPLAIN QUERY PLAN ' + sql).fetchall()
    return [str(r[-1]) for r in rows]


def main() -> None:
    root = ROOT
    res = root / 'results'; data = root / 'data'
    res.mkdir(exist_ok=True); data.mkdir(exist_ok=True)
    db = data / 'portable_sql_plan_audit.sqlite'
    if db.exists(): db.unlink()
    prepare_database(str(db), seed=707, scale=0.06)
    conn = connect(str(db)); stats = Stats(conn); compiler = SQLCompiler(stats, conn)
    out_rows=[]
    failures=[]
    contexts=[]
    for sel in [0.025, 0.10, 0.50]:
        for purpose in ['analytics','support','billing']:
            contexts.append((sel, purpose, context_for_selectivity(sel, purpose=purpose)))
    try:
        for q in query_templates():
            for complexity in range(1,7):
                for sel,purpose,ctx in contexts:
                    pol = PolicyManager(complexity, ctx)
                    for method in SAFE_METHODS:
                        if method != 'reference':
                            safe,_ = method_safety(q, method)
                            if not safe:
                                continue
                        record={'query':q.name,'complexity':complexity,'selectivity':sel,'purpose':purpose,'method':method}
                        try:
                            sql = compiler.compile(q, pol, method)
                            disallowed = bool(DISALLOWED.search(sql))
                            pathlike = bool(PATHLIKE.search(sql))
                            semicolons = sql.count(';')
                            plan = explain(conn, sql)
                            joined = ' | '.join(plan)
                            record.update({
                                'status':'PASS' if not disallowed and not pathlike and semicolons==0 and plan else 'FAIL',
                                'plan_nodes':len(plan),
                                'uses_index': int('INDEX' in joined.upper()),
                                'uses_scan': int('SCAN' in joined.upper()),
                                'uses_cte_or_subquery': int('SUBQUERY' in joined.upper() or 'CO-ROUTINE' in joined.upper() or 'MATERIALIZE' in joined.upper()),
                                'disallowed':int(disallowed),'pathlike':int(pathlike),'semicolons':semicolons,
                                'error':'',
                            })
                        except Exception as e:
                            record.update({'status':'FAIL','plan_nodes':0,'uses_index':0,'uses_scan':0,'uses_cte_or_subquery':0,'disallowed':0,'pathlike':0,'semicolons':0,'error':str(e).splitlines()[0][:200]})
                        out_rows.append(record)
                        if record['status']!='PASS': failures.append(record)
    finally:
        conn.close()
    with open(res/'portable_sql_plan_audit.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(out_rows[0].keys()))
        w.writeheader(); w.writerows(out_rows)
    obj={
        'status':'PASS' if not failures else 'FAIL',
        'generated_safe_sql':len(out_rows),
        'failures':len(failures),
        'plan_nodes':sum(int(r['plan_nodes']) for r in out_rows),
        'index_plans':sum(int(r['uses_index']) for r in out_rows),
        'scan_plans':sum(int(r['uses_scan']) for r in out_rows),
        'cte_or_subquery_plans':sum(int(r['uses_cte_or_subquery']) for r in out_rows),
        'failure_examples':failures[:10],
    }
    json.dump(obj, open(res/'portable_sql_plan_audit.json','w'), indent=2)
    print(json.dumps(obj, indent=2))
    if obj['status']!='PASS': raise SystemExit('portable sql plan audit failed')

if __name__=='__main__': main()
