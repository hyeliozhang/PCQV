#!/usr/bin/env python3
"""Verify numerical, semantic, engineering, and packaging claims in the manuscript."""
from __future__ import annotations
import json, sys, csv, subprocess
from pathlib import Path
root=Path(__file__).resolve().parents[1]
res=root/'results'

def load(name):
    p=res/name
    if not p.exists():
        raise SystemExit(f'missing required result file: {p}')
    return json.load(open(p))


RERUN_LOCAL = '--rerun-local' in sys.argv

def run_local_audit(script_name):
    result_name = script_name.replace('.py', '.json')
    result_path = res / result_name
    if not RERUN_LOCAL:
        if not result_path.exists():
            raise SystemExit(f'missing cached local audit result: {result_path}')
        try:
            obj = json.load(open(result_path))
        except Exception as exc:
            raise SystemExit(f'cannot read cached local audit result {result_path}: {exc}')
        if obj.get('status') != 'PASS' or obj.get('problem_count', 0):
            print(json.dumps(obj, indent=2))
            raise SystemExit(f'cached local audit failed: {script_name}')
        print(f'cached {script_name}: PASS', flush=True)
        return
    script = root / 'artifact' / script_name
    print(f"running {script_name}...", flush=True)
    try:
        cp = subprocess.run([sys.executable, str(script)], cwd=root, text=True, capture_output=True, timeout=180)
    except subprocess.TimeoutExpired as exc:
        print(exc.stdout or "")
        print(exc.stderr or "", file=sys.stderr)
        raise SystemExit(f"local audit timed out: {script_name}")
    if cp.returncode != 0:
        print(cp.stdout)
        print(cp.stderr, file=sys.stderr)
        raise SystemExit(f'local audit failed: {script_name}')

run_local_audit('code_quality_audit.py')
run_local_audit('artifact_quality_audit.py')
run_local_audit('manuscript_consistency_audit.py')
run_local_audit('format_claim_gate_audit.py')
run_local_audit('figure_visual_audit.py')
run_local_audit('efficiency_scalability_audit.py')
run_local_audit('memo_integration_audit.py')
run_local_audit('clean_submission_audit.py')
run_local_audit('evidence_completeness_audit.py')
run_local_audit('claim_boundary_audit.py')
run_local_audit('manuscript_package_audit.py')
run_local_audit('submission_integrity_audit.py')

def load_obligations(json_name, csv_name):
    obj=load(json_name)
    if isinstance(obj, list):
        return obj
    rows=[]
    with open(res/csv_name, newline='') as f:
        for r in csv.DictReader(f):
            rows.append({
                'obligation': r.get('obligation'),
                'expected_safe': str(r.get('expected_safe')).lower()=='true',
                'cases': int(r.get('cases',0)),
                'passed': int(r.get('passed',0)),
                'failed': int(r.get('failed',0)),
                'status': r.get('status'),
            })
    return rows

metrics=load('metrics.json')
std=load('standard_external_metrics.json')
finite=load_obligations('finite_model_semantics.json','finite_model_semantics.csv')
fuzz=load('randomized_semantic_fuzz.json')
sqlfeat=load_obligations('sql_feature_semantics.json','sql_feature_semantics.csv')
opt=load('optimizer_search_audit.json')
job=load('joblike_external_metrics.json')
cert=load('certificate_counterexample_audit.json')
quality=load('artifact_quality_audit.json') if (res/'artifact_quality_audit.json').exists() else {'status':'MISSING'}
integrity=load('submission_integrity_audit.json') if (res/'submission_integrity_audit.json').exists() else {'status':'MISSING'}
unit=load('unit_tests.json') if (res/'unit_tests.json').exists() else {'status':'MISSING'}
capsule=load('capsule_minimality_audit.json')
statconf=load('statistical_confidence_audit.json')
multiseed=load('multiseed_semantic_regression.json')
portable=load('portable_sql_plan_audit.json')
codeq=load('code_quality_audit.json')
proofcert=load('proof_carrying_rewrite_suite.json')
manuscript=load('manuscript_consistency_audit.json')
package=load('manuscript_package_audit.json')
bestgate=load('format_claim_gate_audit.json')
figurevis=load('figure_visual_audit.json')
clean=load('clean_submission_audit.json')
effscale=load('efficiency_scalability_audit.json')
memo=load('memo_integration_audit.json')
evidence=load('evidence_completeness_audit.json')
claims=load('claim_boundary_audit.json')

checks=[]
checks += [
 ('main performance rows', metrics.get('num_performance_rows')==4320),
 ('main correctness rows', metrics.get('num_correctness_rows')==1152),
 ('main safe equivalence', metrics.get('safe_equivalent')==[1064,1064]),
 ('main negative violations nontrivial', metrics.get('negative_policy_violations',0)>=3000),
 ('standard external rows', std.get('standard_external_performance_rows')==180 and std.get('standard_external_correctness_rows')==180),
 ('standard safe equivalence', std.get('standard_external_safe_equivalent')==[108,108]),
 ('standard unsafe failures', std.get('standard_external_unsafe_equivalent')==[0,72]),
 ('finite model all obligations pass', all(r.get('status')=='PASS' for r in finite)),
 ('finite model safe exhaustive', sum(1 for r in finite if r.get('expected_safe'))==4 and all(r['failed']==0 for r in finite if r.get('expected_safe'))),
 ('finite model unsafe witnesses', sum(1 for r in finite if not r.get('expected_safe'))==5 and all(r['failed']>0 for r in finite if not r.get('expected_safe'))),
 ('sql feature all obligations pass', all(r.get('status')=='PASS' for r in sqlfeat)),
 ('sql feature safe exhaustive', sum(1 for r in sqlfeat if r.get('expected_safe'))==4 and all(r['failed']==0 for r in sqlfeat if r.get('expected_safe'))),
 ('sql feature unsafe witnesses', sum(1 for r in sqlfeat if not r.get('expected_safe'))==4 and all(r['failed']>0 for r in sqlfeat if not r.get('expected_safe'))),
 ('randomized fuzz templates', fuzz.get('random_templates',0)>=100),
 ('randomized fuzz safe equivalence', fuzz.get('safe_equivalent', [0,0])[0] == fuzz.get('safe_equivalent', [0,0])[1] and fuzz.get('safe_equivalent', [0,0])[0] >= 600),
 ('randomized fuzz unsafe signal', fuzz.get('unsafe_differences',0)>=25000 and fuzz.get('unsafe_violations',0)>=15000),
 ('randomized fuzz no execution errors', fuzz.get('errors')==0),
 ('optimizer exact search cases', opt.get('cases',0)>=60 and opt.get('exact_cases')==opt.get('cases') and opt.get('max_aliases')==8),
 ('optimizer greedy regret measured', opt.get('max_greedy_regret',0)>1.1 and opt.get('num_cases_where_greedy_suboptimal',0)>0),
 ('joblike external benchmark rows', job.get('timing_rows')==96 and job.get('correctness_rows')==96),
 ('joblike external all safe equivalent', job.get('safe_equivalence')==job.get('safe_total')==96),
 ('joblike pcqv beats view barrier', job.get('pcqv_speedup_vs_view_barrier',0)>2.0),
 ('certified rewrite audit pass', cert.get('all_status')=='PASS' and cert.get('safe_counterexamples')==0),
 ('certified unsafe witnesses nontrivial', cert.get('unsafe_counterexamples',0)>=4 and cert.get('rejected_certificates',0)>=200),
 ('artifact quality audit', quality.get('status')=='PASS'),
 ('submission integrity audit', integrity.get('status')=='PASS'),
 ('core unit-invariant tests', unit.get('status')=='PASS' and unit.get('tests',0)>=5),
 ('capsule minimality audit', capsule.get('status')=='PASS' and capsule.get('necessary_fields')==capsule.get('fields_tested')==8),
 ('statistical confidence audit', statconf.get('status')=='PASS' and statconf.get('records',0)>=10 and statconf.get('main_pcqv_vs_view_barrier_ci',{}).get('ci_low',0)>1.0),
 ('multi-seed semantic regression', multiseed.get('status')=='PASS' and multiseed.get('safe_equivalent')==[960,960] and multiseed.get('errors')==0 and multiseed.get('unsafe_differences',0)>30000),
 ('portable sql and plan audit', portable.get('status')=='PASS' and portable.get('generated_safe_sql',0)>=6000 and portable.get('failures')==0),
 ('source code quality audit', codeq.get('status')=='PASS' and codeq.get('python_files',0)>=20 and codeq.get('compile_failures')==0 and codeq.get('cache_artifacts')==0),
 ('proof-carrying rewrite suite', proofcert.get('status')=='PASS' and proofcert.get('safe_certificates_accepted')==proofcert.get('safe_certificates_total')==1064 and proofcert.get('unsafe_candidates_rejected',0)>=400 and proofcert.get('mandatory_obligations_covered')==proofcert.get('mandatory_obligations_total')),
 ('manuscript/package audit', package.get('status')=='PASS' and package.get('problem_count')==0 and package.get('pdf_pages')==14),
 ('figure visual and disclosure audit', figurevis.get('status')=='PASS' and figurevis.get('problem_count')==0),
 ('format/claim gate audit', bestgate.get('status')=='PASS' and bestgate.get('problem_count')==0 and bestgate.get('details',{}).get('body_section_count',99)<=8 and bestgate.get('details',{}).get('page12_fill',{}).get('right_blank_inches',99)<=1.35),
 ('clean artifact package audit', clean.get('status')=='PASS' and clean.get('problem_count')==0),
 ('manuscript table/figure consistency audit', manuscript.get('status')=='PASS' and not manuscript.get('problems')),
 ('efficiency and scalability audit', effscale.get('status')=='PASS' and effscale.get('scale_measurements',0)>=600 and effscale.get('largest_scale_metrics',{}).get('pcqv_speedup_vs_view_barrier',0)>10.0),
 ('memo integration bridge audit', memo.get('status')=='PASS' and memo.get('memo_candidate_records',0)>=6000 and memo.get('alias_property_records',0)>=15000),
 ('evidence completeness audit', evidence.get('status')=='PASS' and evidence.get('all_checks_supported')),
 ('claim boundary audit', claims.get('status')=='PASS' and claims.get('all_checks_supported')),
]
failed=[name for name, ok in checks if not ok]
for name, ok in checks:
    print(f"{name}: {'PASS' if ok else 'FAIL'}")
if failed:
    print('FAILED:', ', '.join(failed)); sys.exit(1)
print('all manuscript/PDF consistency, format/claim gate, manuscript/package coherence, efficiency/scalability, memo-integration, evidence-completeness, artifact, proof-carrying rewrite, multi-seed, portability, code-quality, minimality, confidence, certificate, externality, and packaging claims verified')
