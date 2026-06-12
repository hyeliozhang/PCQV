#!/usr/bin/env python3
"""Strict submission/package audit for PCQV.

This is a local quality gate that catches problems reviewers and proceedings
chairs routinely penalize: missing template files, leaked build caches, source
paths in the manuscript body, broken reproducibility scripts, stale generated
numbers, and accidental inclusion of template hacks.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper" / "main.tex"

PROBLEMS = []

def fail(msg: str) -> None:
    PROBLEMS.append(msg)

# Normalize the working tree before auditing; unit tests and py_compile may
# create caches, but the distribution must not contain them.
import shutil
for cache in Path(__file__).resolve().parents[1].rglob("__pycache__"):
    shutil.rmtree(cache, ignore_errors=True)
for pyc in Path(__file__).resolve().parents[1].rglob("*.pyc"):
    try:
        pyc.unlink()
    except FileNotFoundError:
        continue

# Required project structure.
required = [
    "paper/main.tex", "paper/main.pdf", "paper/references.bib", "paper/IEEEtran.cls", "paper/IEEEtran.bst",
    "artifact/pcqv/engine.py", "artifact/run_all_repro.py", "artifact/run_standard_benchmarks.py",
    "artifact/certificate_counterexample_audit.py", "artifact/proof_carrying_rewrite_suite.py", "artifact/manuscript_consistency_audit.py",
    "artifact/exhaustive_semantics_checker.py", "artifact/sql_feature_semantics_checker.py",
    "artifact/randomized_semantic_fuzzer.py", "artifact/optimizer_search_audit.py", "artifact/run_joblike_benchmarks.py",
    "artifact/static_submission_audit.py", "artifact/artifact_quality_audit.py", "artifact/manuscript_package_audit.py", "artifact/format_claim_gate_audit.py", "artifact/figure_visual_audit.py", "artifact/clean_submission_audit.py", "artifact/memo_integration_audit.py", "artifact/evidence_completeness_audit.py", "artifact/claim_boundary_audit.py", "artifact/verify_final_claims.py",
    "artifact/tests/test_core_invariants.py", "results/metrics.json", "results/standard_external_metrics.json",
    "results/finite_model_semantics.json", "results/sql_feature_semantics.json", "results/randomized_semantic_fuzz.json",
    "results/optimizer_search_audit.json", "results/joblike_external_metrics.json",
    "results/certificate_counterexample_audit.json", "results/proof_carrying_rewrite_suite.json", "results/manuscript_consistency_audit.json", "results/manuscript_package_audit.json", "results/format_claim_gate_audit.json", "results/figure_visual_audit.json", "results/clean_submission_audit.json", "results/memo_integration_audit.json", "results/evidence_completeness_audit.json", "results/claim_boundary_audit.json", "results/code_quality_audit.json", "EVIDENCE.md", "STATUS.md", "FORMAT_CHECK.md",
    "SUPPLEMENTAL_SUBMISSION.md", "SCOPE_GUARD.md", "REPRODUCIBILITY.md", "requirements.txt", "Dockerfile",
]
for rel in required:
    if not (ROOT / rel).exists():
        fail(f"missing required file: {rel}")

# No Python caches or LaTeX transient files in the clean distribution.
for path in ROOT.rglob("*"):
    rel = path.relative_to(ROOT).as_posix()
    if "__pycache__" in rel or path.suffix == ".pyc":
        fail(f"cache artifact leaked: {rel}")
    if rel.startswith("paper/") and path.suffix in {".aux", ".log", ".blg", ".bbl", ".out", ".synctex.gz"}:
        fail(f"paper build transient leaked: {rel}")

tex = PAPER.read_text(encoding="utf-8")
body = re.split(r"\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}", tex, maxsplit=1)[0]

# Body should not read like an artifact README.
for token in ["artifact/", "results/", ".py", ".csv", ".json", "README", "EVIDENCE", "STATUS"]:
    if token in body:
        fail(f"implementation path/token appears in body: {token}")

# Template/format hacks.
for pattern in [r"\\vspace\s*\{\s*-", r"\\enlargethispage", r"\\addtolength", r"\\setlength\s*\{\\text", r"\\fontsize", r"\\renewcommand\s*\{\\baselinestretch", r"\\usepackage\s*\{geometry\}"]:
    if re.search(pattern, tex):
        fail(f"format hack pattern detected: {pattern}")

# Bibliography target and duplicate keys.
bib = (ROOT / "paper" / "references.bib").read_text(encoding="utf-8")
keys = re.findall(r"@\w+\s*\{\s*([^,]+)", bib)
if not 75 <= len(keys) <= 100:
    fail(f"bib entry count outside target 75-100: {len(keys)}")
if len(keys) != len(set(keys)):
    dups = sorted({k for k in keys if keys.count(k) > 1})
    fail(f"duplicate bib keys: {dups[:10]}")
cited = []
for group in re.findall(r"\\cite\w*\s*\{([^}]+)\}", tex):
    cited.extend(k.strip() for k in group.split(",") if k.strip())
cited_unique = sorted(set(cited))
missing_bib = [k for k in cited_unique if k not in set(keys)]
if missing_bib:
    fail(f"citation keys missing from references.bib: {missing_bib[:10]}")
if not 70 <= len(cited_unique) <= 90:
    fail(f"cited reference count outside target 70-90: {len(cited_unique)}")

# Generated numbers should agree with metrics.
try:
    metrics = json.loads((ROOT / "results" / "metrics.json").read_text())
    nums = (ROOT / "paper" / "generated_numbers.tex").read_text()
    for cmd, val in {
        "NumPerformanceRows": metrics["num_performance_rows"],
        "NumCorrectnessRows": metrics["num_correctness_rows"],
        "SafeEqPassed": metrics["safe_equivalent"][0],
        "SafeEqTotal": metrics["safe_equivalent"][1],
    }.items():
        if f"\\newcommand{{\\{cmd}}}{{{val}}}" not in nums:
            fail(f"generated number stale: {cmd}")
except Exception as e:
    fail(f"failed to check generated numbers: {e}")


# Manuscript tables/figures should not carry stale values after result changes.
try:
    manuscript = json.loads((ROOT / "results" / "manuscript_consistency_audit.json").read_text())
    if manuscript.get("status") != "PASS" or manuscript.get("problems"):
        fail("manuscript consistency audit did not pass")
except Exception as e:
    fail(f"failed to check manuscript consistency audit: {e}")

# Format/claim gate should pass: compact structure, full page-limit use, vector figures, and bibliography hygiene.
try:
    bestgate = json.loads((ROOT / "results" / "format_claim_gate_audit.json").read_text())
    if bestgate.get("status") != "PASS" or bestgate.get("problem_count"):
        fail("format/claim gate audit did not pass")
except Exception as e:
    fail(f"failed to check format/claim gate audit: {e}")


# Clean package gate should pass: no stale logs/reports/renders and a consistent evidence ledger.
try:
    clean = json.loads((ROOT / "results" / "clean_submission_audit.json").read_text())
    if clean.get("status") != "PASS" or clean.get("problem_count"):
        fail("clean package audit did not pass")
except Exception as e:
    fail(f"failed to check clean package audit: {e}")

# Manuscript/package coherence audit should pass as a second independent gate.
try:
    package = json.loads((ROOT / "results" / "manuscript_package_audit.json").read_text())
    if package.get("status") != "PASS" or package.get("problem_count"):
        fail("manuscript/package audit did not pass")
except Exception as e:
    fail(f"failed to check manuscript/package audit: {e}")

# Compile Python files without executing them.
import py_compile
py_errors = []
for py in sorted((ROOT / "artifact").rglob("*.py")):
    try:
        py_compile.compile(str(py), doraise=True)
    except py_compile.PyCompileError as e:
        py_errors.append({"file": py.relative_to(ROOT).as_posix(), "error": str(e)[-1000:]})
if py_errors:
    fail(f"python compile errors: {py_errors}")
# Remove caches created by the compile check so the distribution stays clean.
for cache in ROOT.rglob("__pycache__"):
    import shutil
    shutil.rmtree(cache, ignore_errors=True)

out = {"status": "PASS" if not PROBLEMS else "FAIL", "problems": PROBLEMS, "bib_entry_count": len(keys), "cited_reference_count": len(cited_unique) if 'cited_unique' in globals() else None}
(ROOT / "results" / "submission_integrity_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
print(json.dumps(out, indent=2))
if PROBLEMS:
    sys.exit(1)
