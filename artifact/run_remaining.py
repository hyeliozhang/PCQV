#!/usr/bin/env python3
from pathlib import Path
import sys, json
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'artifact'))
from pcqv.engine import run_ablation, run_cardinality_study, run_rule_coverage, run_proof_obligations, run_explain_plans, run_scale_sensitivity, summarize
res=ROOT/'results'
db_perf=res/'main_perf.sqlite'; db_corr=res/'main_corr.sqlite'
print('ablation'); run_ablation(str(db_perf), str(res/'ablation.csv'), reps=1)
print('cardinality'); run_cardinality_study(str(db_perf), str(res/'cardinality.csv'))
print('rule/proof'); run_rule_coverage(str(res/'rule_coverage.csv')); run_proof_obligations(str(res/'proof_obligations.csv'), str(res/'finite_model_semantics.json'))
print('explain'); run_explain_plans(str(db_perf), str(res/'explain_plans.json'))
print('scale'); run_scale_sensitivity(str(res), str(res/'scale_sensitivity.csv'), seed=19, reps=1)
print('summary'); print(json.dumps(summarize(str(res), str(res/'metrics.json'), str(ROOT/'paper'/'generated_numbers.tex')), indent=2))
