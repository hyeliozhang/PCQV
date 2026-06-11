#!/usr/bin/env python3
from pathlib import Path
import json, os, sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'artifact'))
from pcqv.engine import prepare_database, run_performance, run_correctness, run_mutation_controls, run_planning_overhead, run_ablation, run_cardinality_study, run_rule_coverage, run_proof_obligations, run_explain_plans, run_scale_sensitivity, summarize
import subprocess

def main():
    res = ROOT/'results'; logs=ROOT/'logs'; res.mkdir(exist_ok=True); logs.mkdir(exist_ok=True)
    db_perf = res/'main_perf.sqlite'; db_corr = res/'main_corr.sqlite'
    prepare_database(str(db_perf), seed=7, scale=0.06)
    prepare_database(str(db_corr), seed=11, scale=0.02)
    run_performance(str(db_perf), str(res/'performance.csv'), str(res/'plans.json'), reps=1, scale_label='0.06')
    run_correctness(str(db_corr), str(res/'correctness.csv'), str(res/'sql_examples.json'))
    run_mutation_controls(str(db_corr), str(res/'mutation_controls.csv'), seconds_per_query=0.5)
    run_planning_overhead(str(db_perf), str(res/'planning_overhead.csv'), iterations=4)
    run_ablation(str(db_perf), str(res/'ablation.csv'), reps=1)
    run_cardinality_study(str(db_perf), str(res/'cardinality.csv'))
    run_rule_coverage(str(res/'rule_coverage.csv'))
    run_proof_obligations(str(res/'proof_obligations.csv'), str(res/'finite_model_semantics.json'))
    run_explain_plans(str(db_perf), str(res/'explain_plans.json'))
    # scale sensitivity is expensive; use the existing function but CPU-safe reps=1.
    run_scale_sensitivity(str(res), str(res/'scale_sensitivity.csv'), seed=19, reps=1)
    subprocess.check_call([sys.executable, str(ROOT/'artifact'/'run_joblike_benchmarks.py')])
    subprocess.check_call([sys.executable, str(ROOT/'artifact'/'certificate_counterexample_audit.py')])
    metrics = summarize(str(res), str(res/'metrics.json'), str(ROOT/'paper'/'generated_numbers.tex'))
    print(json.dumps(metrics, indent=2))
if __name__ == '__main__': main()
