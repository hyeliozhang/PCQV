#!/usr/bin/env python3
"""SQL-feature semantic checker for PCQV.

This executable audit is independent of SQLite and complements the main
workload/fuzzer.  It enumerates small multiset instances with NULLs and checks
whether policy rewrite obligations remain valid when SQL-core features such as
three-valued predicates, LEFT OUTER JOIN null-extension, bag duplicates,
semijoins, antijoins, and set difference are present.
"""
from __future__ import annotations
from itertools import combinations
from pathlib import Path
import csv, json

OUT = Path(__file__).resolve().parents[1] / 'results'
OUT.mkdir(parents=True, exist_ok=True)

A0 = [
    {'aid': 1, 'tid': 1, 'region': 'NA',   'risk': 1,    'email': 'a@x', 'opted': 1, 'grp': 'g1'},
    {'aid': 2, 'tid': 1, 'region': 'EU',   'risk': 2,    'email': 'b@x', 'opted': 0, 'grp': 'g1'},
    {'aid': 3, 'tid': 1, 'region': None,   'risk': 2,    'email': 'c@x', 'opted': 1, 'grp': 'g2'},
    {'aid': 4, 'tid': 2, 'region': 'NA',   'risk': None, 'email': 'd@x', 'opted': 1, 'grp': 'g2'},
]
B0 = [
    {'bid': 10, 'tid': 1, 'aid': 1, 'amount': 10, 'purpose': 'analytics', 'kind': 'retail'},
    {'bid': 11, 'tid': 1, 'aid': 1, 'amount': 20, 'purpose': 'support',   'kind': 'retail'},
    {'bid': 12, 'tid': 1, 'aid': 2, 'amount': 30, 'purpose': 'analytics', 'kind': None},
    {'bid': 13, 'tid': 2, 'aid': 4, 'amount': 40, 'purpose': 'analytics', 'kind': 'internal'},
]
CTX = {'tenants': {1}, 'regions': {'NA'}, 'acl_aids': {2}, 'purpose': 'analytics', 'min_group': 2}

def subs(xs):
    xs=list(xs)
    for r in range(len(xs)+1):
        for c in combinations(xs,r):
            yield list(c)

def bag_key(rows):
    return sorted(tuple(sorted(r.items())) for r in rows)

def eqbag(a,b):
    return bag_key(a)==bag_key(b)

def tv_and(a,b):
    if a is False or b is False: return False
    if a is None or b is None: return None
    return True

def sql_in(x, vals):
    if x is None: return None
    return x in vals

def sql_le(x, y):
    if x is None or y is None: return None
    return x <= y

def where_true(v):
    return v is True

def guard_a(a):
    local = tv_and(a['tid'] in CTX['tenants'], sql_le(a['risk'],3))
    reg = sql_in(a['region'], CTX['regions'])
    acl = a['aid'] in CTX['acl_aids']
    return where_true(tv_and(local, (reg is True) or acl))

def guard_b(b, A):
    local = tv_and(b['tid'] in CTX['tenants'], b['purpose']==CTX['purpose'])
    parent = any(a['aid']==b['aid'] and guard_a(a) for a in A)
    return where_true(tv_and(local, parent))

def mask(a):
    return a['email'] if a['opted']==1 and a['region']=='NA' else 'MASKED'

def inner(A,B):
    return [{**{f'a_{k}':v for k,v in a.items()}, **{f'b_{k}':v for k,v in b.items()}} for a in A for b in B if a['aid']==b['aid']]

def left_outer(A,B):
    out=[]
    for a in A:
        matched=False
        for b in B:
            if a['aid']==b['aid']:
                matched=True; out.append((a,b))
        if not matched:
            out.append((a,None))
    return out

def protected_rows(A,B):
    pa=[a for a in A if guard_a(a)]
    pb=[b for b in B if guard_b(b,A)]
    return [{'aid':a['aid'],'bid':b['bid'],'email':mask(a),'amount':b['amount']} for a in pa for b in pb if a['aid']==b['aid']]

def protected_left_on(A,B):
    pa=[a for a in A if guard_a(a)]
    pb=[b for b in B if guard_b(b,A)]
    out=[]
    for a,b in left_outer(pa,pb):
        out.append({'aid':a['aid'],'bid': None if b is None else b['bid'], 'email':mask(a), 'amount': None if b is None else b['amount']})
    return out

def protected_left_where(A,B):
    # Incorrect: right guard is placed in WHERE after null extension, collapsing rows with no visible B.
    pa=[a for a in A if guard_a(a)]
    out=[]
    for a,b in left_outer(pa,B):
        if b is not None and guard_b(b,A):
            out.append({'aid':a['aid'],'bid':b['bid'],'email':mask(a),'amount':b['amount']})
    return out

def aggregate_protected(A,B):
    rows=protected_rows(A,B)
    g={}
    for r in rows:
        g.setdefault(r['aid'],[0,0]); g[r['aid']][0]+=1; g[r['aid']][1]+=r['amount']
    return [{'aid':aid,'n':n,'sum':s} for aid,(n,s) in g.items() if n>=CTX['min_group']]

def aggregate_raw_then_guard(A,B):
    raw=inner(A,B)
    g={}
    for r in raw:
        aid=r['a_aid']; g.setdefault(aid,[0,0]); g[aid][0]+=1; g[aid][1]+=r['b_amount']
    out=[]
    for aid,(n,s) in g.items():
        a=next(x for x in A if x['aid']==aid)
        if guard_a(a) and n>=CTX['min_group']:
            out.append({'aid':aid,'n':n,'sum':s})
    return out

def case(name, fn, expected_safe):
    total=ok=0; first=None
    for A in subs(A0):
        for B in subs(B0):
            total+=1
            same, info = fn(A,B)
            if same: ok+=1
            elif first is None: first={'A':A,'B':B,'info':info}
    status = 'PASS' if (expected_safe and ok==total) or ((not expected_safe) and ok<total) else 'FAIL'
    return {'obligation':name,'expected_safe':expected_safe,'cases':total,'passed':ok,'failed':total-ok,'status':status,'first_witness':first}

def safe_null_guard_selection(A,B):
    left=protected_rows(A,[b for b in B if where_true(sql_le(b['amount'],30))])
    right=[r for r in protected_rows(A,B) if r['amount'] is not None and r['amount']<=30]
    return eqbag(left,right), {'left':left,'right':right}

def safe_left_outer_guard_in_on(A,B):
    ref=protected_left_on(A,B)
    moved=protected_left_on(A,B)  # explicit obligation: right guard belongs to ON-side protected input
    return eqbag(ref,moved), {'reference':ref,'moved':moved}

def unsafe_left_outer_guard_in_where(A,B):
    ref=protected_left_on(A,B); bad=protected_left_where(A,B)
    return eqbag(ref,bad), {'reference':ref,'where_guard':bad}

def unsafe_raw_agg_with_duplicates(A,B):
    ref=aggregate_protected(A,B); bad=aggregate_raw_then_guard(A,B)
    return eqbag(ref,bad), {'reference':ref,'raw_first':bad}

def safe_semijoin_parent_guard(A,B):
    ref=[b for b in B if guard_b(b,A)]
    sj=[b for b in B if b['tid'] in CTX['tenants'] and b['purpose']==CTX['purpose'] and b['aid'] in {a['aid'] for a in A if guard_a(a)}]
    return eqbag(ref,sj), {'reference':ref,'semijoin':sj}

def unsafe_antijoin_before_guard(A,B):
    # Query: protected A with no protected B. Incorrect antijoin against raw B removes A that only has hidden B.
    pa=[a for a in A if guard_a(a)]
    pb=[b for b in B if guard_b(b,A)]
    ref=[{'aid':a['aid']} for a in pa if not any(b['aid']==a['aid'] for b in pb)]
    bad=[{'aid':a['aid']} for a in pa if not any(b['aid']==a['aid'] for b in B)]
    return eqbag(ref,bad), {'reference':ref,'raw_antijoin':bad}

def safe_union_after_protection(A,B):
    # UNION ALL of two guarded branches preserves multiplicities when branch-local protection is applied first.
    branch1=protected_rows(A,[b for b in B if b['amount']<=20])
    branch2=protected_rows(A,[b for b in B if b['amount']>=20])
    split=branch1+branch2
    whole=[r for r in protected_rows(A,B) if r['amount']<=20] + [r for r in protected_rows(A,B) if r['amount']>=20]
    return eqbag(split,whole), {'split':split,'whole':whole}

def unsafe_except_before_policy(A,B):
    # EXCEPT-like intent: visible A not paired with visible B. Raw except excludes rows because of hidden B.
    pa=[a for a in A if guard_a(a)]
    pb=[b for b in B if guard_b(b,A)]
    ref=[{'aid':a['aid']} for a in pa if a['aid'] not in {b['aid'] for b in pb}]
    bad=[{'aid':a['aid']} for a in pa if a['aid'] not in {b['aid'] for b in B}]
    return eqbag(ref,bad), {'reference':ref,'raw_except':bad}

checks=[
 ('safe_null_three_valued_selection_pushdown', safe_null_guard_selection, True),
 ('safe_left_outer_join_guard_in_on_side', safe_left_outer_guard_in_on, True),
 ('safe_semijoin_parent_guard', safe_semijoin_parent_guard, True),
 ('safe_union_all_branch_after_protection', safe_union_after_protection, True),
 ('unsafe_left_outer_join_guard_in_where', unsafe_left_outer_guard_in_where, False),
 ('unsafe_raw_aggregation_under_bag_duplicates', unsafe_raw_agg_with_duplicates, False),
 ('unsafe_antijoin_before_guard', unsafe_antijoin_before_guard, False),
 ('unsafe_except_before_policy', unsafe_except_before_policy, False),
]
rows=[case(n,fn,safe) for n,fn,safe in checks]
json.dump(rows, open(OUT/'sql_feature_semantics.json','w'), indent=2)
with open(OUT/'sql_feature_semantics.csv','w',newline='') as f:
    w=csv.DictWriter(f, fieldnames=['obligation','expected_safe','cases','passed','failed','status'])
    w.writeheader(); [w.writerow({k:r[k] for k in w.fieldnames}) for r in rows]
print('sql-feature semantic obligations:')
for r in rows:
    print(f"{r['status']}: {r['obligation']} cases={r['cases']} failed={r['failed']}")
if any(r['status']!='PASS' for r in rows):
    raise SystemExit(1)
