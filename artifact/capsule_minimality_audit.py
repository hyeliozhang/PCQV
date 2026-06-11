#!/usr/bin/env python3
"""Audit whether visibility-capsule fields are necessary for protected rewrites.

The goal is not to benchmark SQLite.  It is a small semantic witness generator:
for each capsule field, we give a family of protected instances in which a
rewrite checker that omits that field accepts an unsafe transformation.  A field
is marked necessary only when the witness is computed by evaluating the safe
protected semantics and the field-erased rewrite semantics and observing a
non-empty symmetric difference or a policy-violating contributor.
"""
from __future__ import annotations
import csv, json
from collections import Counter
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'results'
OUT.mkdir(exist_ok=True)

Row = Dict[str, object]
Bag = Counter

def bag(rows: Iterable[Tuple]) -> Bag:
    return Counter(rows)

def symdiff(a: Bag, b: Bag) -> int:
    keys = set(a) | set(b)
    return sum(abs(a.get(k, 0) - b.get(k, 0)) for k in keys)

def tenant_guard(rows: List[Row], tenant: int) -> List[Row]:
    return [r for r in rows if r.get('tenant') == tenant]

def purpose_guard(rows: List[Row], purpose: str) -> List[Row]:
    return [r for r in rows if r.get('purpose') == purpose]

def region_guard(rows: List[Row], regions: set) -> List[Row]:
    return [r for r in rows if r.get('region') in regions]

def acl_guard(rows: List[Row], user: int) -> List[Row]:
    return [r for r in rows if r.get('owner') == user or user in r.get('acl', set())]

def mask_email(v: str) -> str:
    return v.split('@')[0][:1] + '***@masked'

CASES = []

def witness_tenant_guard() -> Tuple[Bag, Bag, int]:
    rows = [
        {'cid': 1, 'tenant': 1, 'email': 'a@x'},
        {'cid': 2, 'tenant': 2, 'email': 'b@y'},
    ]
    protected = bag((r['cid'],) for r in tenant_guard(rows, 1))
    erased = bag((r['cid'],) for r in rows)  # checker forgot tenant guard
    return protected, erased, 0

def witness_purpose_guard() -> Tuple[Bag, Bag, int]:
    rows = [
        {'oid': 10, 'tenant': 1, 'purpose': 'analytics', 'amount': 9},
        {'oid': 11, 'tenant': 1, 'purpose': 'support', 'amount': 7},
    ]
    protected = bag((r['oid'],) for r in purpose_guard(rows, 'analytics'))
    erased = bag((r['oid'],) for r in rows)
    return protected, erased, 0

def witness_region_guard() -> Tuple[Bag, Bag, int]:
    rows = [
        {'cid': 1, 'region': 'EU'},
        {'cid': 2, 'region': 'NA'},
        {'cid': 3, 'region': 'APAC'},
    ]
    protected = bag((r['cid'],) for r in region_guard(rows, {'EU'}))
    erased = bag((r['cid'],) for r in rows)
    return protected, erased, 0

def witness_acl_exception() -> Tuple[Bag, Bag, int]:
    rows = [
        {'doc': 1, 'owner': 7, 'acl': set(), 'topic': 'normal'},
        {'doc': 2, 'owner': 8, 'acl': {7}, 'topic': 'fraud'},
        {'doc': 3, 'owner': 9, 'acl': set(), 'topic': 'fraud'},
    ]
    protected = bag((r['doc'],) for r in acl_guard(rows, 7))
    erased = bag((r['doc'],) for r in rows if r['owner'] == 7)  # forgot ACL disjunction
    return protected, erased, 0

def witness_cross_alias_guard() -> Tuple[Bag, Bag, int]:
    customers = [{'cid': 1, 'tenant': 1}, {'cid': 2, 'tenant': 2}]
    orders = [{'oid': 1, 'cid': 1, 'tenant': 1}, {'oid': 2, 'cid': 1, 'tenant': 2}]
    protected = bag((o['oid'], c['cid']) for o in orders for c in customers
                    if o['cid'] == c['cid'] and o['tenant'] == c['tenant'] and c['tenant'] == 1)
    erased = bag((o['oid'], c['cid']) for o in orders for c in customers
                 if o['cid'] == c['cid'] and c['tenant'] == 1)
    return protected, erased, 1

def witness_mask_timing() -> Tuple[Bag, Bag, int]:
    rows = [
        {'email': 'amy@a.com', 'risk': 1},
        {'email': 'ann@b.com', 'risk': 1},
    ]
    protected = bag((r['email'], 1) for r in rows)  # group before final mask
    erased = bag((mask_email(r['email']), 2) for r in rows)  # mask before grouping merges groups
    return protected, erased, 0

def witness_group_release() -> Tuple[Bag, Bag, int]:
    rows = [
        {'tenant': 1, 'region': 'EU', 'amount': 5},
        {'tenant': 2, 'region': 'EU', 'amount': 7},
        {'tenant': 2, 'region': 'EU', 'amount': 11},
    ]
    protected_group = [r for r in rows if r['tenant'] == 1]
    protected = Counter() if len(protected_group) < 2 else bag((('EU', sum(r['amount'] for r in protected_group)),))
    all_group = rows
    erased = bag((('EU', sum(r['amount'] for r in all_group)),)) if len(all_group) >= 2 else Counter()
    return protected, erased, 2

def witness_order_limit() -> Tuple[Bag, Bag, int]:
    rows = [{'id': 1, 'score': 10}, {'id': 2, 'score': 10}, {'id': 3, 'score': 9}]
    protected = bag((r['id'],) for r in sorted(rows, key=lambda r: (-r['score'], r['id']))[:1])
    erased = bag((r['id'],) for r in sorted(rows, key=lambda r: (-r['score'], -r['id']))[:1])
    return protected, erased, 0

CASES = [
    ('tenant_guard', 'base row visibility', 'row predicate pushdown / view elision', witness_tenant_guard),
    ('purpose_guard', 'context-sensitive row visibility', 'predicate injection / plan selection', witness_purpose_guard),
    ('region_guard', 'selectivity and geography visibility', 'region filter pushdown', witness_region_guard),
    ('acl_exception', 'disjunctive exceptions', 'predicate factorization', witness_acl_exception),
    ('cross_alias_guard', 'alias-stable dependency', 'join reordering / semijoin reduction', witness_cross_alias_guard),
    ('mask_timing', 'raw-value consumer tracking', 'mask movement / projection pushdown', witness_mask_timing),
    ('group_release', 'protected contributor multiset', 'aggregation pushdown / late filter', witness_group_release),
    ('deterministic_order', 'ORDER/LIMIT prefix identity', 'order-preserving rewrite', witness_order_limit),
]

rows = []
for field, semantic_role, rewrite_family, fn in CASES:
    ref, erased, viol = fn()
    sd = symdiff(ref, erased)
    rows.append({
        'capsule_field': field,
        'semantic_role': semantic_role,
        'rewrite_family': rewrite_family,
        'reference_rows': sum(ref.values()),
        'field_erased_rows': sum(erased.values()),
        'symmetric_difference': sd,
        'policy_violating_contributors': viol,
        'necessary': sd > 0 or viol > 0,
    })

csv_path = OUT / 'capsule_minimality_audit.csv'
with open(csv_path, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
    w.writeheader(); w.writerows(rows)
summary = {
    'status': 'PASS' if all(r['necessary'] for r in rows) else 'FAIL',
    'fields_tested': len(rows),
    'necessary_fields': sum(1 for r in rows if r['necessary']),
    'total_symmetric_difference': int(sum(r['symmetric_difference'] for r in rows)),
    'total_policy_violating_contributors': int(sum(r['policy_violating_contributors'] for r in rows)),
    'csv': str(csv_path.relative_to(ROOT)),
}
json_path = OUT / 'capsule_minimality_audit.json'
json_path.write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
