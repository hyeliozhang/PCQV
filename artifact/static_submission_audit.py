#!/usr/bin/env python3
"""Static submission audit for ICDE PCQV package.

Checks paper-source hygiene that is easy to regress during last-mile edits:
no appendices, no formatting hacks, no repository/file-path leakage in the main
body, acceptable reference count, and expected artifact result files.
"""
from __future__ import annotations
import re, sys, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
tex = (ROOT/'paper'/'main.tex').read_text(encoding='utf-8')
body = re.split(r'\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}', tex, maxsplit=1)[0]
problems = []
forbidden_cmds = [r'\\vspace\s*\{\s*-', r'\\enlargethispage', r'\\fontsize', r'\\setlength\s*\{\\textfloatsep', r'\\addtolength', r'\\resizebox', r'\\scalebox', r'\\usepackage\s*\{geometry\}']
for pat in forbidden_cmds:
    if re.search(pat, tex):
        problems.append(f'formatting hack detected: {pat}')
if re.search(r'\\appendix|\\section\s*\{\s*Appendix', tex, flags=re.I):
    problems.append('appendix detected')
# Main-body leak check: allow citations and normal prose, reject explicit implementation paths and filenames.
leak_patterns = [r'artifact/', r'results/', r'benchmark/', r'logs/', r'\.py\b', r'\.csv\b', r'\.json\b', r'README', r'EVIDENCE', r'STATUS', r'SUPPLEMENTAL']
for pat in leak_patterns:
    if re.search(pat, body):
        problems.append(f'main-body implementation path/file leak: {pat}')
# bib count from entries.
bib = (ROOT/'paper'/'references.bib').read_text(encoding='utf-8')
refs = len(re.findall(r'@\w+\s*\{', bib))
if not (75 <= refs <= 100):
    problems.append(f'reference count outside 75-100: {refs}')
required = [
    'results/metrics.json', 'results/standard_external_metrics.json', 'results/finite_model_semantics.json',
    'results/randomized_semantic_fuzz.json', 'artifact/pcqv/engine.py', 'artifact/exhaustive_semantics_checker.py',
    'artifact/randomized_semantic_fuzzer.py', 'artifact/verify_final_claims.py', 'artifact/static_submission_audit.py',
]
missing = [p for p in required if not (ROOT/p).exists()]
if missing:
    problems.append('missing required artifact files: ' + ', '.join(missing))
summary = {'reference_count': refs, 'problems': problems, 'status': 'PASS' if not problems else 'FAIL'}
print(json.dumps(summary, indent=2))
if problems:
    sys.exit(1)
