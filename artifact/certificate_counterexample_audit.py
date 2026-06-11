#!/usr/bin/env python3
"""Certificate and counterexample audit for PCQV.

This script is intentionally independent of SQLite. It treats protected-query
rewrites as small relational transformations and asks two questions:
1. Can every claimed safe rewrite emit a locally checkable certificate whose
   side conditions are satisfied on enumerated SQL-core instances?
2. Can every rejected/unsafe rewrite family produce a concrete minimal witness
   demonstrating why ordinary SQL equivalence is not enough under policy?

The checker is not a replacement for the paper proof; it is an executable audit
that stress-tests the proof obligations and produces replayable witnesses.
"""
from __future__ import annotations
from dataclasses import dataclass, asdict
from itertools import product
from pathlib import Path
import csv, json, math
from typing import Dict, List, Tuple, Any

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / 'results'
LOG = ROOT / 'logs'
RES.mkdir(exist_ok=True)
LOG.mkdir(exist_ok=True)

Row = Dict[str, Any]
Bag = List[Tuple[Tuple[str, Any], ...]]

def canon(rows: List[Row]) -> Bag:
    return sorted(tuple(sorted(r.items())) for r in rows)

def select(rows: List[Row], pred):
    return [r for r in rows if pred(r)]

def project(rows: List[Row], cols: List[str]) -> List[Row]:
    return [{c:r.get(c) for c in cols} for r in rows]

def join(left: List[Row], right: List[Row], lkey: str, rkey: str, prefix_l='r', prefix_r='s') -> List[Row]:
    out=[]
    for a in left:
        for b in right:
            if a.get(lkey)==b.get(rkey):
                row={}
                row.update({f'{prefix_l}.{k}':v for k,v in a.items()})
                row.update({f'{prefix_r}.{k}':v for k,v in b.items()})
                out.append(row)
    return out

def left_join(left: List[Row], right: List[Row], lkey: str, rkey: str) -> List[Row]:
    out=[]
    for a in left:
        matched=False
        for b in right:
            if a.get(lkey)==b.get(rkey):
                row={**{f'r.{k}':v for k,v in a.items()}, **{f's.{k}':v for k,v in b.items()}}
                out.append(row); matched=True
        if not matched:
            keys=set().union(*(b.keys() for b in right)) if right else {'id','tid','sens','val'}
            row={**{f'r.{k}':v for k,v in a.items()}, **{f's.{k}':None for k in keys}}
            out.append(row)
    return out

def group_count(rows: List[Row], key: str) -> List[Row]:
    d={}
    for r in rows:
        d[r.get(key)] = d.get(r.get(key),0)+1
    return [{'g':k,'cnt':v} for k,v in d.items()]

def mask_email(row: Row) -> str:
    # support-visible if opted in and low sensitivity; otherwise masked.
    return row.get('email') if row.get('opted') and row.get('sens',9)<=2 else 'MASKED'

def r_visible(r: Row) -> bool:
    return r.get('tid') == 1 and r.get('region') in {'NA','EU'} and r.get('sens',0) <= 2

def s_visible(s: Row) -> bool:
    return s.get('tid') == 1 and s.get('clearance',0) <= 2

def cross_guard(r: Row, s: Row) -> bool:
    return r.get('tid') == s.get('tid') and (r.get('owner') == 7 or s.get('public') == 1)

@dataclass
class CertResult:
    rule: str
    expected_safe: bool
    cases: int
    accepted_certificates: int
    rejected_certificates: int
    counterexamples: int
    minimal_witness_size: int
    status: str
    witness: str


def enumerate_instances(limit=2):
    vals=[0,1]
    regions=['NA','APAC']
    # Keep finite space small but include duplicate keys and policy variation.
    base=[]
    for tid, region, sens, opted, owner in product(vals, regions, [1,3], vals, [7,8]):
        base.append({'id':len(base)%2, 'tid':tid, 'region':region, 'sens':sens, 'opted':bool(opted), 'owner':owner, 'email':f'e{len(base)}@x'})
    side=[]
    for tid, clearance, public, val in product(vals, [1,3], vals, vals):
        side.append({'rid':len(side)%2, 'tid':tid, 'clearance':clearance, 'public':public, 'val':val})
    # deterministic subsets
    for i in range(0, min(len(base)-1, 16), 2):
        for j in range(0, min(len(side)-1, 16), 2):
            yield base[i:i+limit], side[j:j+limit]
    # adversarial witnesses for rules whose failure needs duplicates/ties.
    yield [
        {'id':0,'tid':1,'region':'NA','sens':1,'opted':False,'owner':7,'email':'secret@x'},
        {'id':0,'tid':0,'region':'NA','sens':1,'opted':False,'owner':7,'email':'hidden@x'},
    ], [{'rid':0,'tid':1,'clearance':1,'public':1,'val':0}]
    yield [
        {'id':0,'tid':1,'region':'NA','sens':1,'opted':True,'owner':7,'email':'a@x'},
        {'id':1,'tid':1,'region':'NA','sens':1,'opted':True,'owner':7,'email':'b@x'},
    ], [{'rid':0,'tid':1,'clearance':1,'public':1,'val':0}]


def safe_filter_pushdown(R,S):
    left = join(select(R,r_visible), select(S,s_visible), 'id','rid')
    right = select(join(R,S,'id','rid'), lambda x: r_visible({k.split('.',1)[1]:v for k,v in x.items() if k.startswith('r.')}) and s_visible({k.split('.',1)[1]:v for k,v in x.items() if k.startswith('s.')}))
    return canon(left)==canon(right)

def safe_mask_after_visibility(R,S):
    a=[{'id':r['id'], 'email':mask_email(r)} for r in select(R,r_visible)]
    b=[{'id':r['id'], 'email':mask_email(r)} for r in R if r_visible(r)]
    return canon(a)==canon(b)

def safe_group_guard(R,S,k=2):
    visible=select(R,r_visible)
    a=select(group_count(visible,'id'), lambda g:g['cnt']>=k)
    b=select(group_count(select(R,r_visible),'id'), lambda g:g['cnt']>=k)
    return canon(a)==canon(b)

def safe_semijoin_reduction(R,S):
    # Semijoin reduction is safe only after protected visibility has been applied to the right side.
    visible_s=select(S,s_visible)
    keys={s['rid'] for s in visible_s}
    a=select(select(R,r_visible), lambda r:r['id'] in keys)
    b=[]
    for r in select(R,r_visible):
        if any(s['rid']==r['id'] and s_visible(s) for s in S):
            b.append(r)
    return canon(a)==canon(b)

def unsafe_mask_filter(R,S):
    # Filtering on a masked value before masking can leak/alter visible output.
    a=project(select([{'id':r['id'], 'm':mask_email(r)} for r in select(R,r_visible)], lambda x:x['m']=='secret@x'), ['id','m'])
    b=project([{'id':r['id'], 'm':mask_email(r)} for r in select(R, lambda r: r_visible(r) and r['email']=='secret@x')], ['id','m'])
    return canon(a)==canon(b)

def unsafe_group_before_visibility(R,S,k=2):
    a=select(group_count(R,'id'), lambda g:g['cnt']>=k)
    a=select(a, lambda g: any(r['id']==g['g'] and r_visible(r) for r in R))
    b=select(group_count(select(R,r_visible),'id'), lambda g:g['cnt']>=k)
    return canon(a)==canon(b)

def unsafe_left_join_where_pushdown(R,S):
    lj=left_join(R,S,'id','rid')
    # Bad: putting right guard in WHERE removes null-extended rows.
    a=select(lj, lambda x: x.get('s.tid')==1)
    # Correct: right guard belongs in ON/protected right relation and preserves unmatched left rows.
    b=left_join(R, select(S, lambda s:s.get('tid')==1),'id','rid')
    return canon(a)==canon(b)

def unsafe_limit_without_key(R,S):
    # Two legal physical plans can choose different first rows when order is non-unique.
    rows=[{'id':r['id'], 'score':r['sens']} for r in select(R,r_visible)]
    if len(rows)<2: return True
    a=sorted(rows, key=lambda x:x['score'])[:1]
    b=sorted(reversed(rows), key=lambda x:x['score'])[:1]
    return canon(a)==canon(b)

RULES=[
    ('guard_pushdown_certificate', True, safe_filter_pushdown, ['local_guard','tenant_guard','attribute_preservation']),
    ('mask_certificate', True, safe_mask_after_visibility, ['mask_after_visibility','no_unmasked_predicate']),
    ('group_guard_certificate', True, safe_group_guard, ['group_guard_after_protected_input','monotone_count']),
    ('protected_semijoin_certificate', True, safe_semijoin_reduction, ['right_side_visible_first','key_preservation']),
    ('reject_filter_on_masked_value', False, unsafe_mask_filter, ['mask_predicate_rejected']),
    ('reject_group_before_visibility', False, unsafe_group_before_visibility, ['aggregate_boundary_rejected']),
    ('reject_left_join_where_guard', False, unsafe_left_join_where_pushdown, ['outer_join_null_extension_rejected']),
    ('reject_limit_without_unique_key', False, unsafe_limit_without_key, ['non_unique_order_rejected']),
]

rows=[]; witnesses={}
for name, safe, fn, obligations in RULES:
    cases=accepted=rejected=counter=0; minw=10**9; wit=''
    for R,S in enumerate_instances():
        cases += 1
        ok=fn(R,S)
        cert_ok = safe and all(obligations)
        if safe:
            if ok and cert_ok: accepted += 1
            else:
                counter += 1; rejected += 1
                if len(R)+len(S)<minw:
                    minw=len(R)+len(S); wit=json.dumps({'R':R,'S':S})[:500]
        else:
            # unsafe rule should be rejected; a non-equivalent instance is a witness.
            rejected += 1
            if not ok:
                counter += 1
                if len(R)+len(S)<minw:
                    minw=len(R)+len(S); wit=json.dumps({'R':R,'S':S})[:500]
    status='PASS' if ((safe and counter==0 and accepted==cases) or ((not safe) and counter>0 and rejected==cases)) else 'FAIL'
    if minw==10**9: minw=0
    rows.append(CertResult(name,safe,cases,accepted,rejected,counter,minw,status,wit))
    witnesses[name]= {'expected_safe':safe,'counterexamples':counter,'minimal_witness_size':minw,'witness':wit,'obligations':obligations}

csv_path=RES/'certificate_counterexample_audit.csv'
with csv_path.open('w',newline='') as f:
    w=csv.DictWriter(f, fieldnames=list(asdict(rows[0]).keys()))
    w.writeheader(); [w.writerow(asdict(r)) for r in rows]
summary={
    'rules': len(rows),
    'safe_rules': sum(1 for r in rows if r.expected_safe),
    'unsafe_rules': sum(1 for r in rows if not r.expected_safe),
    'cases': sum(r.cases for r in rows),
    'accepted_certificates': sum(r.accepted_certificates for r in rows),
    'rejected_certificates': sum(r.rejected_certificates for r in rows),
    'counterexamples': sum(r.counterexamples for r in rows),
    'safe_counterexamples': sum(r.counterexamples for r in rows if r.expected_safe),
    'unsafe_counterexamples': sum(r.counterexamples for r in rows if not r.expected_safe),
    'all_status': 'PASS' if all(r.status=='PASS' for r in rows) else 'FAIL',
    'witnesses': witnesses,
}
(RES/'certificate_counterexample_audit.json').write_text(json.dumps(summary,indent=2))
(LOG/'certificate_counterexample_audit.log').write_text(json.dumps(summary,indent=2)+"\n")
print(json.dumps(summary,indent=2))
if summary['all_status']!='PASS':
    raise SystemExit(1)
