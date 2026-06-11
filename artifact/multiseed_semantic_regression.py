#!/usr/bin/env python3
"""Multi-seed semantic regression for PCQV.

Runs the randomized semantic fuzzer over several independent seeds and fresh
small databases.  This is meant to guard against overfitting the oracle and
safe/unsafe encodings to a single random generator trajectory.
"""
from __future__ import annotations
import argparse, csv, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from randomized_semantic_fuzzer import run as run_fuzzer  # type: ignore
from pcqv.engine import prepare_database  # type: ignore


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(ROOT))
    ap.add_argument('--seeds', default='101,202,303,404')
    ap.add_argument('--n', type=int, default=40)
    args = ap.parse_args()
    root = Path(args.out)
    results = root / 'results'
    data = root / 'data'
    results.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True, exist_ok=True)
    seeds = [int(x) for x in args.seeds.split(',') if x.strip()]
    aggregate_rows = []
    summaries = []
    for seed in seeds:
        db = data / f'multiseed_fuzz_{seed}.sqlite'
        if db.exists():
            db.unlink()
        prepare_database(str(db), seed=seed + 17, scale=0.04)
        csv_path = results / f'multiseed_fuzz_{seed}.csv'
        json_path = results / f'multiseed_fuzz_{seed}.json'
        run_fuzzer(str(db), str(csv_path), str(json_path), args.n, seed)
        summary = json.load(open(json_path))
        summary['seed'] = seed
        summaries.append(summary)
        with open(csv_path, newline='') as f:
            for r in csv.DictReader(f):
                r['seed'] = seed
                aggregate_rows.append(r)
    out_csv = results / 'multiseed_semantic_regression.csv'
    if aggregate_rows:
        with open(out_csv, 'w', newline='') as f:
            fieldnames = ['seed'] + [k for k in aggregate_rows[0].keys() if k != 'seed']
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader(); w.writerows(aggregate_rows)
    total_safe = sum(s['safe_equivalent'][1] for s in summaries)
    safe_eq = sum(s['safe_equivalent'][0] for s in summaries)
    total_rows = sum(s['rows'] for s in summaries)
    total_errors = sum(s['errors'] for s in summaries)
    unsafe_diff = sum(s['unsafe_differences'] for s in summaries)
    unsafe_viol = sum(s['unsafe_violations'] for s in summaries)
    obj = {
        'status': 'PASS' if safe_eq == total_safe and total_errors == 0 and unsafe_diff > 0 else 'FAIL',
        'seeds': seeds,
        'templates_per_seed': args.n,
        'rows': total_rows,
        'safe_equivalent': [safe_eq, total_safe],
        'errors': total_errors,
        'unsafe_differences': unsafe_diff,
        'unsafe_violations': unsafe_viol,
        'per_seed': summaries,
    }
    json.dump(obj, open(results / 'multiseed_semantic_regression.json', 'w'), indent=2)
    print(json.dumps(obj, indent=2))
    if obj['status'] != 'PASS':
        raise SystemExit('multi-seed regression failed')

if __name__ == '__main__':
    main()
