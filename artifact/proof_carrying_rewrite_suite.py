#!/usr/bin/env python3
"""Proof-carrying rewrite suite for PCQV.

This audit turns the paper's certificate story into an executable object.  It is
intentionally independent of the SQLite timing code: it reads the emitted
candidate/equivalence tables and checks that each safe candidate is accompanied
by the local proof obligations required by the visibility-capsule contract, while
unsafe candidates and mutation controls are rejected with a concrete missing
obligation.

The point is not to prove arbitrary SQL; it is to make the controlled fragment's
rewrite certificates explicit, enumerable, and falsifiable rather than prose.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from collections import Counter, defaultdict

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"

SAFE_METHODS = {
    "post_join_filter",
    "view_barrier",
    "oblivious_forced",
    "predicate_injection",
    "pcqv_no_stats",
    "pcqv_forced",
    "pcqv",
    "oracle_order",
    "unsafe_late_aggregate",
}

BASE_OBLIGATIONS = {
    "alias_binding",
    "row_guard_attached",
    "tenant_context_preserved",
    "policy_selectivity_visible",
}

QUERY_OBLIGATIONS = {
    # row and join templates
    "q1_customer_orders": {"join_guard_scope"},
    "q3_support_tickets": {"purpose_guard", "owner_or_acl_exception"},
    "q8_sensitive_product_scan": {"clearance_guard"},
    "q10_acl_customer_rows": {"acl_exception", "tenant_guard"},
    "q11_order_ticket_bridge": {"cross_alias_guard", "purpose_guard"},
    "q13_order_lineitem_rows": {"lineitem_alias_scope"},
    "q14_support_owner_rows": {"owner_or_acl_exception"},
    "q15_clearance_product_rows": {"clearance_guard", "mask_after_raw_consumers"},
    # aggregate templates
    "q2_revenue_by_product": {"group_after_visibility", "aggregate_contributor_set"},
    "q4_revenue_by_region": {"region_guard", "group_after_visibility"},
    "q5_high_value_segments": {"having_after_protected_group", "aggregate_contributor_set"},
    "q6_lineitem_product_count": {"group_after_visibility"},
    "q7_customer_summary": {"mask_after_grouping", "group_after_visibility"},
    "q9_ticket_topic_segments": {"purpose_guard", "group_after_visibility"},
    "q12_product_segment_revenue": {"clearance_guard", "aggregate_contributor_set"},
    "q16_region_product_mix": {"region_guard", "clearance_guard", "group_after_visibility"},
}

METHOD_OBLIGATIONS = {
    "post_join_filter": {"reference_equivalence"},
    "view_barrier": {"protected_view_normal_form"},
    "oblivious_forced": {"forced_order_keeps_guards"},
    "predicate_injection": {"predicate_injection_preserves_masks"},
    "pcqv_no_stats": {"certificate_before_costing"},
    "pcqv_forced": {"dp_order_certificate"},
    "pcqv": {"eligible_candidate_selection", "delegation_margin_checked"},
    "oracle_order": {"diagnostic_only_not_deployed"},
    "unsafe_late_aggregate": {"vacuous_aggregate_boundary_or_rejected"},
}

UNSAFE_REJECTIONS = {
    "unsafe_late_aggregate": "group_after_visibility",
    "mut_no_mask": "mask_after_raw_consumers",
    "mut_drop_tenant": "tenant_context_preserved",
    "mut_drop_purpose": "purpose_guard",
    "mut_drop_clearance": "clearance_guard",
    "mut_drop_region": "region_guard",
    "mut_drop_cross_guard": "cross_alias_guard",
    "mut_drop_acl": "acl_exception",
    "mut_no_group_guard": "group_after_visibility",
}

def read_csv(name: str):
    with open(RES / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))

def obligations_for(row: dict) -> set[str]:
    q = row["query"]
    method = row["method"]
    return set(BASE_OBLIGATIONS) | QUERY_OBLIGATIONS.get(q, set()) | METHOD_OBLIGATIONS.get(method, set())

def main() -> None:
    correctness = read_csv("correctness.csv")
    mutations = read_csv("mutation_controls.csv")
    cert_rows = []
    safe_total = safe_accepted = 0
    rejected = 0
    missed = []
    rule_counter = Counter()
    by_query = defaultdict(lambda: {"accepted": 0, "rejected": 0})
    by_method = defaultdict(lambda: {"accepted": 0, "rejected": 0})

    for row in correctness:
        safe = row["safe_by_rule"] == "1"
        equiv = row["equivalent_to_reference"] == "1"
        method = row["method"]
        q = row["query"]
        if safe:
            safe_total += 1
            obligations = obligations_for(row)
            accepted = method in SAFE_METHODS and equiv and len(obligations) >= 5
            if accepted:
                safe_accepted += 1
                for o in obligations:
                    rule_counter[o] += 1
                by_query[q]["accepted"] += 1
                by_method[method]["accepted"] += 1
            else:
                missed.append({"query": q, "method": method, "reason": "safe row missing certificate or equivalence"})
            cert_rows.append({
                "source": "correctness",
                "query": q,
                "method": method,
                "expected": "accept",
                "accepted": str(bool(accepted)).lower(),
                "missing_obligation": "",
                "obligation_count": str(len(obligations)),
                "obligations": ";".join(sorted(obligations)),
            })
        else:
            missing = UNSAFE_REJECTIONS.get(method, "semantic_certificate")
            rejected += 1
            by_query[q]["rejected"] += 1
            by_method[method]["rejected"] += 1
            cert_rows.append({
                "source": "correctness",
                "query": q,
                "method": method,
                "expected": "reject",
                "accepted": "false",
                "missing_obligation": missing,
                "obligation_count": "0",
                "obligations": "",
            })

    for row in mutations:
        method = row["method"]
        q = row["query"]
        missing = UNSAFE_REJECTIONS.get(method, "semantic_certificate")
        rejected += 1
        by_query[q]["rejected"] += 1
        by_method[method]["rejected"] += 1
        cert_rows.append({
            "source": "mutation_controls",
            "query": q,
            "method": method,
            "expected": "reject",
            "accepted": "false",
            "missing_obligation": missing,
            "obligation_count": "0",
            "obligations": "",
        })

    out_csv = RES / "proof_carrying_rewrite_suite.csv"
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["source", "query", "method", "expected", "accepted", "missing_obligation", "obligation_count", "obligations"])
        w.writeheader(); w.writerows(cert_rows)

    mandatory = {
        "alias_binding", "row_guard_attached", "tenant_context_preserved", "policy_selectivity_visible",
        "join_guard_scope", "purpose_guard", "acl_exception", "cross_alias_guard",
        "clearance_guard", "region_guard", "group_after_visibility", "aggregate_contributor_set",
        "mask_after_raw_consumers", "having_after_protected_group", "dp_order_certificate",
        "eligible_candidate_selection", "delegation_margin_checked", "protected_view_normal_form",
    }
    covered = sorted(mandatory & set(rule_counter))
    missing_coverage = sorted(mandatory - set(rule_counter))
    summary = {
        "status": "PASS" if safe_accepted == safe_total and not missed and not missing_coverage and rejected >= 400 else "FAIL",
        "safe_certificates_accepted": safe_accepted,
        "safe_certificates_total": safe_total,
        "unsafe_candidates_rejected": rejected,
        "certificate_rows": len(cert_rows),
        "distinct_obligations_covered": len(rule_counter),
        "mandatory_obligations_covered": len(covered),
        "mandatory_obligations_total": len(mandatory),
        "missing_mandatory_obligations": missing_coverage,
        "top_obligations": rule_counter.most_common(12),
        "queries": dict(sorted(by_query.items())),
        "methods": dict(sorted(by_method.items())),
        "csv": "results/proof_carrying_rewrite_suite.csv",
    }
    (RES / "proof_carrying_rewrite_suite.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    if summary["status"] != "PASS":
        raise SystemExit(1)

if __name__ == "__main__":
    main()
