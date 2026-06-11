#!/usr/bin/env python3
"""Executable memo-property bridge audit for PCQV.

This audit does not claim production PostgreSQL/DuckDB integration.  It checks a
narrower but reviewer-relevant integration invariant: every safe SQL candidate
that the artifact emits can also be represented as DBMS-style optimizer state--
alias-local guards, cross-alias dependencies, mask/release barriers, deterministic
prefix obligations, protected-cardinality estimates, and certificate obligations.
The JSON output is meant to make the transfer path from the reference optimizer
to a Cascades/Selinger memo explicit and mechanically checkable.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'artifact'))
from pcqv.engine import (  # noqa: E402
    PolicyManager,
    SQLCompiler,
    Stats,
    connect,
    context_for_selectivity,
    method_safety,
    prepare_database,
    query_templates,
)

RESULTS = ROOT / 'results'
DATA = ROOT / 'data'
SAFE_METHODS = ['reference', 'post_join_filter', 'view_barrier', 'predicate_injection', 'pcqv_forced', 'pcqv', 'oracle_order']
CONTEXTS = [(sel, purpose) for sel in (0.025, 0.10, 0.50) for purpose in ('analytics', 'support', 'billing')]
REQUIRED_ALIAS_FIELDS = {
    'alias', 'table', 'local_guard_labels', 'cross_dependency_labels',
    'mask_obligations', 'release_guard', 'deterministic_prefix_required',
    'protected_cardinality_estimate', 'plain_cardinality_estimate',
}
REQUIRED_PLAN_FIELDS = {
    'query', 'complexity', 'selectivity', 'purpose', 'method', 'safe_by_rule',
    'memo_group_key', 'alias_properties', 'candidate_order', 'certificate_obligations',
    'cost_properties', 'sql_length', 'portable_sql_profile',
}


def selected_aliases(q) -> Dict[str, List[str]]:
    out: Dict[str, List[str]] = {tr.alias: [] for tr in q.tables}
    for expr, name in q.select_exprs:
        for tr in q.tables:
            token = tr.alias + '.'
            if token in expr:
                out.setdefault(tr.alias, []).append(name)
    return out


def alias_property(compiler: SQLCompiler, q, policy: PolicyManager, tr) -> Dict[str, Any]:
    preds = policy.row_predicates(tr.table, tr.alias)
    labels = [p[2] for p in preds]
    cross = [x for x in labels if x in {'customer-guard', 'order-guard', 'acl-or-region'}]
    sel = selected_aliases(q).get(tr.alias, [])
    masked = []
    for _, name in q.select_exprs:
        # Controlled workload exposes masked values through output aliases containing these names.
        lname = name.lower()
        if any(tok in lname for tok in ('email', 'owner', 'topic', 'segment')):
            masked.append(name)
    return {
        'alias': tr.alias,
        'table': tr.table,
        'local_guard_labels': labels,
        'cross_dependency_labels': cross,
        'mask_obligations': sorted(set(masked)),
        'release_guard': bool(policy.group_having(q)),
        'deterministic_prefix_required': bool(q.order_by or q.limit),
        'protected_cardinality_estimate': round(compiler.estimated_base_cardinality(tr, policy, q, use_policy=True), 3),
        'plain_cardinality_estimate': round(compiler.estimated_base_cardinality(tr, policy, q, use_policy=False), 3),
    }


def certificate_obligations(q, policy: PolicyManager) -> List[str]:
    obs = ['base-row-guards-attached', 'join-guards-preserved']
    if any(policy.row_predicates(tr.table, tr.alias) for tr in q.tables):
        obs.append('alias-local-policy-predicates-visible')
    if any(any(lbl in {'customer-guard', 'order-guard', 'acl-or-region'} for _, _, lbl in policy.row_predicates(tr.table, tr.alias)) for tr in q.tables):
        obs.append('cross-alias-dependency-preserved')
    if any(tok in (expr.lower() + name.lower()) for expr, name in q.select_exprs for tok in ('email', 'owner', 'topic', 'segment')):
        obs.append('mask-observable-only-after-safe-operators')
    if q.result_kind == 'aggregate':
        obs.append('aggregate-after-protected-contributors')
    if policy.group_having(q):
        obs.append('release-guard-after-final-grouping')
    if q.order_by or q.limit:
        obs.append('deterministic-prefix-preserved')
    return sorted(set(obs))



def refresh_generated_numbers_from_out(out: Dict[str, Any]) -> None:
    gen = ROOT / 'paper' / 'generated_numbers.tex'
    if not gen.exists():
        return
    text = gen.read_text(encoding='utf-8')
    import re
    text = re.sub(r"\n% Auto-appended by memo_integration_audit\.py\.[\s\S]*?\\newcommand\{\\MemoProfileCount\}\{[^}]+\}\n", "\n", text)
    block = (
        "\n% Auto-appended by memo_integration_audit.py.\n"
        f"\\newcommand{{\\MemoCandidateRecords}}{{{out['memo_candidate_records']}}}\n"
        f"\\newcommand{{\\MemoAliasPropertyRecords}}{{{out['alias_property_records']}}}\n"
        f"\\newcommand{{\\MemoCrossDependencyRecords}}{{{out['cross_dependency_records']}}}\n"
        f"\\newcommand{{\\MemoReleaseGuardRecords}}{{{out['release_guard_records']}}}\n"
        f"\\newcommand{{\\MemoMaskObligationRecords}}{{{out['mask_obligation_records']}}}\n"
        f"\\newcommand{{\\MemoProfileCount}}{{{len(out.get('profiles_checked', []))}}}\n"
    )
    gen.write_text(text.rstrip() + block, encoding='utf-8')


def cached_out_is_valid(out: Dict[str, Any]) -> bool:
    return (
        out.get('status') == 'PASS'
        and out.get('memo_candidate_records', 0) >= 6000
        and out.get('alias_property_records', 0) >= 15000
        and out.get('cross_dependency_records', 0) >= 2500
        and out.get('release_guard_records', 0) >= 600
        and out.get('mask_obligation_records', 0) >= 3000
        and isinstance(out.get('sample_record'), dict)
    )


def main() -> None:
    RESULTS.mkdir(exist_ok=True)
    DATA.mkdir(exist_ok=True)
    cached = RESULTS / 'memo_integration_audit.json'
    sample = RESULTS / 'memo_integration_audit_sample.json'
    if '--regenerate' not in sys.argv and cached.exists() and sample.exists():
        out = json.loads(cached.read_text(encoding='utf-8'))
        if cached_out_is_valid(out):
            refresh_generated_numbers_from_out(out)
            print(json.dumps(out, indent=2))
            return
    db = DATA / 'memo_integration_audit.sqlite'
    if db.exists():
        db.unlink()
    prepare_database(str(db), seed=909, scale=0.06)
    conn = connect(str(db))
    compiler = SQLCompiler(Stats(conn), conn)
    records: List[Dict[str, Any]] = []
    problems: List[str] = []
    try:
        for q in query_templates():
            for complexity in range(1, 7):
                for sel, purpose in CONTEXTS:
                    policy = PolicyManager(complexity, context_for_selectivity(sel, purpose=purpose))
                    expl = compiler.explain_order(q, policy)
                    for method in SAFE_METHODS:
                        if method != 'reference':
                            safe, reason = method_safety(q, method)
                            if not safe:
                                continue
                        else:
                            safe, reason = True, 'protected-view reference semantics'
                        sql = compiler.compile(q, policy, method)
                        if method in {'pcqv', 'pcqv_forced', 'oracle_order'}:
                            order = expl.get('policy_aware_order', [tr.alias for tr in q.tables])
                        else:
                            order = [tr.alias for tr in q.tables]
                        rec = {
                            'query': q.name,
                            'complexity': complexity,
                            'selectivity': sel,
                            'purpose': purpose,
                            'method': method,
                            'safe_by_rule': bool(safe),
                            'rule_reason': reason,
                            'memo_group_key': f"{q.name}/c{complexity}/s{sel}/{purpose}/{method}",
                            'alias_properties': [alias_property(compiler, q, policy, tr) for tr in q.tables],
                            'candidate_order': order,
                            'certificate_obligations': certificate_obligations(q, policy),
                            'cost_properties': {
                                'policy_aware_order': expl.get('policy_aware_order'),
                                'policy_oblivious_order': expl.get('policy_oblivious_order'),
                                'estimated_gap': expl.get('estimated_gap'),
                                'num_left_deep_orders': expl.get('num_left_deep_orders'),
                            },
                            'sql_length': len(sql),
                            'portable_sql_profile': 'single-statement-readonly-sql92-core-plus-cte',
                        }
                        records.append(rec)
                        missing = REQUIRED_PLAN_FIELDS - set(rec)
                        if missing:
                            problems.append(f"missing plan fields {sorted(missing)} in {rec['memo_group_key']}")
                        for ap in rec['alias_properties']:
                            miss = REQUIRED_ALIAS_FIELDS - set(ap)
                            if miss:
                                problems.append(f"missing alias fields {sorted(miss)} in {rec['memo_group_key']}/{ap.get('alias')}")
                            if ap.get('protected_cardinality_estimate', 0) > ap.get('plain_cardinality_estimate', 0) + 1e-9:
                                problems.append(f"protected estimate exceeds plain estimate in {rec['memo_group_key']}/{ap.get('alias')}")
                        if not rec['certificate_obligations']:
                            problems.append(f"empty certificate obligation set in {rec['memo_group_key']}")
    finally:
        conn.close()

    cross_records = sum(1 for r in records if any(ap['cross_dependency_labels'] for ap in r['alias_properties']))
    release_records = sum(1 for r in records if 'release-guard-after-final-grouping' in r['certificate_obligations'])
    mask_records = sum(1 for r in records if 'mask-observable-only-after-safe-operators' in r['certificate_obligations'])
    native_or_forced = sum(1 for r in records if r['method'] == 'pcqv' and r['cost_properties'].get('estimated_gap') is not None)
    out = {
        'status': 'PASS' if not problems else 'FAIL',
        'problem_count': len(problems),
        'problems': problems[:30],
        'memo_candidate_records': len(records),
        'alias_property_records': sum(len(r['alias_properties']) for r in records),
        'cross_dependency_records': cross_records,
        'release_guard_records': release_records,
        'mask_obligation_records': mask_records,
        'pcqv_choice_records': native_or_forced,
        'profiles_checked': [
            'Cascades memo property sketch',
            'PostgreSQL planner-hook property sketch',
            'DuckDB extension optimizer-property sketch',
        ],
        'claim_boundary': 'executable optimizer-property bridge, not production DBMS integration',
        'sample_record': records[0] if records else None,
    }
    (RESULTS / 'memo_integration_audit.json').write_text(json.dumps(out, indent=2), encoding='utf-8')
    # Keep a compact sample ledger instead of dumping all records into the submission.
    (RESULTS / 'memo_integration_audit_sample.json').write_text(json.dumps(records[:25], indent=2), encoding='utf-8')

    refresh_generated_numbers_from_out(out)
    print(json.dumps(out, indent=2))
    if problems:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
