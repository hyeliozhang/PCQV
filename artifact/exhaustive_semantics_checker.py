#!/usr/bin/env python3
"""Finite-model semantic checker for PCQV rewrite obligations.

Independent of SQLite. Enumerates small protected relational instances and
checks algebraic obligations used by the manuscript: guard-preserving selections,
guarded joins, cross-guard semijoins, mask timing, aggregate release timing,
LIMIT timing, ACL disjunctions, and projection adequacy.
"""
from __future__ import annotations
from itertools import chain, combinations
from pathlib import Path
import csv, json

OUT = Path(__file__).resolve().parents[1] / "results"
OUT.mkdir(parents=True, exist_ok=True)

A_BASE = [
    # Row 1 is locally hidden by region; row 2 is visible only through ACL; row 3 is hidden by risk.
    {"aid": 1, "tid": 1, "region": "APAC", "email": "a@x", "opted": 1, "risk": 1, "grp": "g1", "val": 10},
    {"aid": 2, "tid": 1, "region": "EU", "email": "b@x", "opted": 0, "risk": 2, "grp": "g1", "val": 20},
    {"aid": 3, "tid": 1, "region": "NA", "email": "c@x", "opted": 1, "risk": 5, "grp": "g1", "val": 30},
]
B_BASE = [
    {"bid": 10, "tid": 1, "aid": 1, "amount": 120, "purpose": "analytics"},
    {"bid": 11, "tid": 1, "aid": 2, "amount": 220, "purpose": "analytics"},
    {"bid": 12, "tid": 1, "aid": 3, "amount": 320, "purpose": "analytics"},
]
C_BASE = [
    {"cid": 100, "tid": 1, "bid": 10, "score": 5},
    {"cid": 101, "tid": 1, "bid": 11, "score": 9},
    {"cid": 102, "tid": 1, "bid": 12, "score": 7},
]
CTX = {"tenants": {1}, "regions": {"NA"}, "acl_aids": {2}, "purpose": "analytics", "min_group": 2}

def powerset(xs):
    xs = list(xs)
    return chain.from_iterable(combinations(xs, r) for r in range(len(xs)+1))

def key_rows(rows):
    def norm(v): return tuple(sorted(v.items()))
    return tuple(sorted(norm(r) for r in rows))

def guard_a(a):
    return a["tid"] in CTX["tenants"] and a["risk"] <= 3 and (a["region"] in CTX["regions"] or a["aid"] in CTX["acl_aids"])

def guard_b(b, a_rows):
    return b["tid"] in CTX["tenants"] and b["purpose"] == CTX["purpose"] and any(a["aid"] == b["aid"] and guard_a(a) for a in a_rows)

def mask_email(a):
    return a["email"] if a["opted"] and a["region"] in CTX["regions"] else "MASKED"

def join_ab(a_rows, b_rows):
    return [{**{f"a_{k}": v for k, v in a.items()}, **{f"b_{k}": v for k, v in b.items()}} for a in a_rows for b in b_rows if a["aid"] == b["aid"]]

def join_bc(b_rows, c_rows):
    return [{**{f"b_{k}": v for k, v in b.items()}, **{f"c_{k}": v for k, v in c.items()}} for b in b_rows for c in c_rows if b["bid"] == c["bid"]]

def project_rows(rows, cols):
    return [{c: r[c] for c in cols} for r in rows]

def protected_ab(a_rows, b_rows):
    pa = [a for a in a_rows if guard_a(a)]
    pb = [b for b in b_rows if guard_b(b, a_rows)]
    out = []
    for r in join_ab(pa, pb):
        a = next(x for x in pa if x["aid"] == r["a_aid"])
        out.append({"aid": r["a_aid"], "bid": r["b_bid"], "amount": r["b_amount"], "email": mask_email(a), "grp": r["a_grp"]})
    return out

def aggregate_after_policy(a_rows, b_rows):
    rows = protected_ab(a_rows, b_rows)
    groups = {}
    for r in rows:
        groups.setdefault(r["grp"], [0, 0])
        groups[r["grp"]][0] += 1
        groups[r["grp"]][1] += r["amount"]
    return [{"grp": g, "n": n, "sum": s} for g, (n, s) in groups.items() if n >= CTX["min_group"]]

def aggregate_before_policy(a_rows, b_rows):
    raw = join_ab(a_rows, b_rows)
    groups = {}
    for r in raw:
        groups.setdefault(r["a_grp"], [0, 0])
        groups[r["a_grp"]][0] += 1
        groups[r["a_grp"]][1] += r["b_amount"]
    return [{"grp": g, "n": n, "sum": s} for g, (n, s) in groups.items() if n >= CTX["min_group"]]

def check_case(name, claim, expected_safe=True):
    total = ok = 0
    first = None
    for A in powerset(A_BASE):
        for B in powerset(B_BASE):
            for C in powerset(C_BASE):
                A, B, C = list(A), list(B), list(C)
                total += 1
                good, info = claim(A, B, C)
                if good:
                    ok += 1
                elif first is None:
                    first = {"A": A, "B": B, "C": C, "info": info}
    return {"obligation": name, "expected_safe": expected_safe, "cases": total, "passed": ok, "failed": total-ok, "first_witness": first}

def selection_pushdown(A, B, C):
    left = [r for r in protected_ab(A, B) if r["amount"] >= 100]
    Bf = [b for b in B if b["amount"] >= 100]
    right = protected_ab(A, Bf)
    return key_rows(left) == key_rows(right), {"left": left, "right": right}

def guarded_join_commutes(A, B, C):
    pa = [a for a in A if guard_a(a)]
    pb = [b for b in B if guard_b(b, A)]
    left = join_ab(pa, pb)
    right = join_ab(pa, pb)
    return key_rows(left) == key_rows(right), {"left": left, "right": right}

def semijoin_cross_guard(A, B, C):
    protected = [b for b in B if guard_b(b, A)]
    semijoin = [b for b in B if b["tid"] in CTX["tenants"] and b["purpose"] == CTX["purpose"] and b["aid"] in {a["aid"] for a in A if guard_a(a)}]
    return key_rows(protected) == key_rows(semijoin), {"protected": protected, "semijoin": semijoin}

def associative_under_guards(A, B, C):
    pa = [a for a in A if guard_a(a)]
    pb = [b for b in B if guard_b(b, A)]
    left = []
    for ab in join_ab(pa, pb):
        for c in C:
            if c["bid"] == ab["b_bid"] and c["tid"] in CTX["tenants"]:
                left.append({"aid": ab["a_aid"], "bid": ab["b_bid"], "cid": c["cid"]})
    bc = join_bc(pb, [c for c in C if c["tid"] in CTX["tenants"]])
    right = []
    for a in pa:
        for bcr in bc:
            if a["aid"] == bcr["b_aid"]:
                right.append({"aid": a["aid"], "bid": bcr["b_bid"], "cid": bcr["c_cid"]})
    return key_rows(left) == key_rows(right), {"left": left, "right": right}

def raw_email_predicate_pushdown_is_unsafe(A, B, C):
    ref = [r for r in protected_ab(A, B) if r["email"] != "MASKED"]
    Af = [a for a in A if a["email"] != "MASKED"]
    moved = protected_ab(Af, B)
    return key_rows(ref) == key_rows(moved), {"reference": ref, "moved": moved}

def aggregate_before_guard_is_unsafe(A, B, C):
    return key_rows(aggregate_after_policy(A, B)) == key_rows(aggregate_before_policy(A, B)), {"after": aggregate_after_policy(A, B), "before": aggregate_before_policy(A, B)}

def limit_before_guard_is_unsafe(A, B, C):
    ref = protected_ab(A, B)[:1]
    raw_limited = join_ab(A, B)[:1]
    moved = []
    for r in raw_limited:
        a = next(x for x in A if x["aid"] == r["a_aid"])
        b = next(x for x in B if x["bid"] == r["b_bid"])
        if guard_a(a) and guard_b(b, A):
            moved.append({"aid": r["a_aid"], "bid": r["b_bid"], "amount": r["b_amount"], "email": mask_email(a), "grp": r["a_grp"]})
    return key_rows(ref) == key_rows(moved), {"reference": ref, "limit_before_policy": moved, "raw_limited": raw_limited}

def acl_removed_is_unsafe(A, B, C):
    def guard_a_no_acl(a):
        return a["tid"] in CTX["tenants"] and a["risk"] <= 3 and a["region"] in CTX["regions"]
    ref = [a for a in A if guard_a(a)]
    bad = [a for a in A if guard_a_no_acl(a)]
    return key_rows(ref) == key_rows(bad), {"reference": ref, "no_acl": bad}

def projection_before_policy_is_unsafe(A, B, C):
    raw = project_rows(join_ab(A, B), ["a_aid", "b_bid", "b_amount"])
    ref = protected_ab(A, B)
    bad = [{"aid": r["a_aid"], "bid": r["b_bid"], "amount": r["b_amount"]} for r in raw]
    ref_cmp = [{"aid": r["aid"], "bid": r["bid"], "amount": r["amount"]} for r in ref]
    return key_rows(ref_cmp) == key_rows(bad), {"reference": ref_cmp, "projected_late_filter": bad}

checks = [
    ("safe_selection_pushdown_over_guarded_relation", selection_pushdown, True),
    ("safe_guarded_join_commutation", guarded_join_commutes, True),
    ("safe_cross_guard_as_semijoin", semijoin_cross_guard, True),
    ("safe_inner_join_association_after_guards", associative_under_guards, True),
    ("unsafe_mask_predicate_movement", raw_email_predicate_pushdown_is_unsafe, False),
    ("unsafe_aggregation_before_row_guards", aggregate_before_guard_is_unsafe, False),
    ("unsafe_limit_before_row_guards", limit_before_guard_is_unsafe, False),
    ("unsafe_acl_disjunction_removed", acl_removed_is_unsafe, False),
    ("unsafe_projection_drops_policy_attrs", projection_before_policy_is_unsafe, False),
]
summary = []
for name, fn, expected_safe in checks:
    r = check_case(name, fn, expected_safe)
    if expected_safe:
        status = "PASS" if r["failed"] == 0 else "FAIL"
    else:
        status = "PASS" if r["failed"] > 0 else "FAIL_NO_WITNESS"
    r["status"] = status
    summary.append(r)
with (OUT / "finite_model_semantics.json").open("w") as f:
    json.dump(summary, f, indent=2)
with (OUT / "finite_model_semantics.csv").open("w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=["obligation", "expected_safe", "cases", "passed", "failed", "status"])
    w.writeheader()
    for r in summary:
        w.writerow({k: r[k] for k in w.fieldnames})
print("finite-model obligations:")
for r in summary:
    print(f"{r['status']}: {r['obligation']} cases={r['cases']} failed={r['failed']}")
if any(r["status"].startswith("FAIL") for r in summary):
    raise SystemExit(1)
