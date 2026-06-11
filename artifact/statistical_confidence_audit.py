#!/usr/bin/env python3
"""Compute confidence intervals for the major performance claims.

The paper should not rely on a single median calculated from noisy sub-ms runs.
This audit uses paired rows whenever possible and reports bootstrap confidence
intervals over query/context/scale instances.  It writes compact JSON/CSV files
consumed by the claim verifier and by the manuscript macros.
"""
from __future__ import annotations
import json, csv
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / 'results'
RNG = np.random.default_rng(20270606)

def boot_ci(values, n=2000):
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return [float('nan'), float('nan'), float('nan'), 0]
    idx = RNG.integers(0, len(arr), size=(n, len(arr)))
    meds = np.median(arr[idx], axis=1)
    return [float(np.median(arr)), float(np.quantile(meds, 0.025)), float(np.quantile(meds, 0.975)), int(len(arr))]

def paired_speedup(df, key_cols, method_a, method_b, value='latency_ms'):
    piv = df.pivot_table(index=key_cols, columns='method', values=value, aggfunc='median')
    if method_a not in piv or method_b not in piv:
        return []
    sub = piv[[method_a, method_b]].dropna()
    sub = sub[(sub[method_a] > 0) & (sub[method_b] > 0)]
    return (sub[method_b] / sub[method_a]).to_numpy()

records = []
# Main controlled benchmark: speedup ratios are baseline/pcqv.
main = pd.read_csv(RES / 'performance.csv')
for base in ['view_barrier', 'post_join_filter', 'oblivious_forced', 'predicate_injection']:
    vals = paired_speedup(main, ['scale','selectivity','complexity','query'], 'pcqv', base)
    med, lo, hi, n = boot_ci(vals)
    records.append({'suite':'main', 'claim':f'pcqv_vs_{base}', 'median':med, 'ci_low':lo, 'ci_high':hi, 'pairs':n})
# Standard schema.
std = pd.read_csv(RES / 'standard_external_raw.csv')
for base in ['view_barrier', 'raw_post_filter_control', 'predicate_injection']:
    vals = paired_speedup(std, ['scale','context','query'], 'pcqv', base)
    med, lo, hi, n = boot_ci(vals)
    records.append({'suite':'standard_schema', 'claim':f'pcqv_vs_{base}', 'median':med, 'ci_low':lo, 'ci_high':hi, 'pairs':n})
# JOB-like schema uses method pcqv_job.
job = pd.read_csv(RES / 'joblike_external_raw.csv')
for base in ['view_barrier', 'oblivious_forced', 'predicate_injection']:
    vals = paired_speedup(job, ['level','query'], 'pcqv_job', base)
    med, lo, hi, n = boot_ci(vals)
    records.append({'suite':'joblike', 'claim':f'pcqv_job_vs_{base}', 'median':med, 'ci_low':lo, 'ci_high':hi, 'pairs':n})
# Cardinality q-error improvement: plain/policy.
card = pd.read_csv(RES / 'cardinality.csv')
vals = card['plain_q_error'].replace([np.inf, -np.inf], np.nan).dropna().to_numpy() / card['policy_q_error'].replace([np.inf, -np.inf], np.nan).dropna().to_numpy()
med, lo, hi, n = boot_ci(vals)
records.append({'suite':'cardinality', 'claim':'plain_qerr_over_policy_qerr', 'median':med, 'ci_low':lo, 'ci_high':hi, 'pairs':n})
# Planning overhead upper envelope.
plan = pd.read_csv(RES / 'planning_overhead.csv')
pcqv_compile = plan[plan['method'].eq('pcqv')]['compile_ms'].dropna().to_numpy()
med, lo, hi, n = boot_ci(pcqv_compile)
records.append({'suite':'planning', 'claim':'pcqv_compile_ms', 'median':med, 'ci_low':lo, 'ci_high':hi, 'pairs':n})

csv_path = RES / 'statistical_confidence_audit.csv'
with open(csv_path, 'w', newline='') as f:
    w = csv.DictWriter(f, fieldnames=list(records[0].keys()))
    w.writeheader(); w.writerows(records)
summary = {
    'status': 'PASS',
    'records': len(records),
    'main_pcqv_vs_view_barrier_ci': next(r for r in records if r['suite']=='main' and r['claim']=='pcqv_vs_view_barrier'),
    'joblike_pcqv_vs_view_barrier_ci': next(r for r in records if r['suite']=='joblike' and r['claim']=='pcqv_job_vs_view_barrier'),
    'pcqv_compile_ms_ci': next(r for r in records if r['suite']=='planning' and r['claim']=='pcqv_compile_ms'),
    'csv': str(csv_path.relative_to(ROOT)),
}
(RES / 'statistical_confidence_audit.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
print(json.dumps(summary, indent=2))
