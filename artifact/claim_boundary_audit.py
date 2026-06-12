#!/usr/bin/env python3
"""Check that manuscript claims stay within locally supported evidence."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PAPER = ROOT / "paper" / "main.tex"
PDF = ROOT / "paper" / "main.pdf"
EXTRA_BIN_DIRS = [Path(p) for p in os.environ.get("PCQV_POPPLER_BIN", "").split(os.pathsep) if p]
PROBLEMS: list[str] = []


def fail(msg: str) -> None:
    PROBLEMS.append(msg)


def load(rel: str) -> dict:
    path = ROOT / rel
    if not path.exists():
        fail(f"missing evidence file: {rel}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"cannot parse {rel}: {exc}")
        return {}


def resolve_command(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_BIN_DIRS:
        candidate = directory / (f"{name}.exe" if os.name == "nt" else name)
        if candidate.exists():
            return str(candidate)
    return name


def command_text(args: list[str]) -> str:
    resolved = [resolve_command(args[0]), *args[1:]]
    try:
        cp = subprocess.run(
            resolved,
            cwd=ROOT,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        fail(f"missing command: {args[0]}")
        return ""
    if cp.returncode != 0:
        fail(f"command failed {' '.join(args)}: {cp.stderr[:200]}")
    return cp.stdout


def pdf_pages() -> int:
    match = re.search(r"^Pages:\s+(\d+)", command_text(["pdfinfo", str(PDF)]), re.M)
    return int(match.group(1)) if match else -1


def page_text(page: int) -> str:
    return command_text(["pdftotext", "-f", str(page), "-l", str(page), str(PDF), "-"])


def main() -> None:
    tex = PAPER.read_text(encoding="utf-8")
    body = re.split(
        r"\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}",
        tex,
        maxsplit=1,
    )[0]
    low = body.lower()

    metrics = load("results/metrics.json")
    eff = load("results/efficiency_scalability_audit.json")
    memo = load("results/memo_integration_audit.json")
    proof = load("results/proof_carrying_rewrite_suite.json")
    cap = load("results/capsule_minimality_audit.json")
    cert = load("results/certificate_counterexample_audit.json")
    stat = load("results/statistical_confidence_audit.json")
    fmt = load("results/format_claim_gate_audit.json")
    clean = load("results/clean_submission_audit.json")
    integ = load("results/submission_integrity_audit.json")
    qual = load("results/artifact_quality_audit.json")
    codeq = load("results/code_quality_audit.json")
    portable = load("results/portable_sql_plan_audit.json")
    std = load("results/standard_external_metrics.json")
    job = load("results/joblike_external_metrics.json")
    manuscript = load("results/manuscript_consistency_audit.json")

    required_phrases = [
        "efficient and certified",
        "evaluation emphasizes efficiency and scalability",
        "five-size scale sweep",
        "memo-property bridge audit",
        "protected selectivity",
        "rule-level certificates",
    ]
    for phrase in required_phrases:
        if phrase not in low:
            fail(f"missing manuscript boundary phrase: {phrase}")

    forbidden_phrases = [
        "reviewer skepticism",
        "small prototype beats",
        "scalability-reviewable",
        "not used to cheat",
        "blanket domination",
        "complete optimizer contract",
    ]
    for phrase in forbidden_phrases:
        if phrase in low:
            fail(f"over-broad or process-oriented phrase remains: {phrase}")

    pages = pdf_pages()
    page12 = re.sub(r"[^A-Z]", "", page_text(12).upper())
    page13 = re.sub(r"[^A-Z]", "", page_text(13).upper())
    if pages != 14:
        fail(f"unexpected PDF page count: {pages}")
    if "AIGENERATED" in page12 or "REFERENCES" in page12:
        fail("required disclosure or references appear before the body limit")
    if "AIGENERATED" not in page13 or "REFERENCES" not in page13:
        fail("required disclosure and references should begin after the body")

    fill = fmt.get("details", {}).get("page12_fill", {})
    if fill.get("right_blank_inches", 99) > 1.35 or fill.get("left_blank_inches", 99) > 1.35:
        fail(f"page 12 fill is looser than expected: {fill}")

    safe_eq = metrics.get("safe_equivalent") == [1064, 1064]
    scale_ok = (
        eff.get("status") == "PASS"
        and eff.get("scale_measurements", 0) >= 600
        and eff.get("largest_scale_metrics", {}).get("pcqv_speedup_vs_view_barrier", 0) >= 10
    )
    external_ok = (
        std.get("standard_external_safe_equivalent") == [108, 108]
        and job.get("safe_equivalence") == job.get("safe_total") == 96
    )
    proof_ok = proof.get("status") == "PASS" and cap.get("status") == "PASS" and cert.get("all_status") == "PASS"
    stats_ok = stat.get("status") == "PASS" and stat.get("main_pcqv_vs_view_barrier_ci", {}).get("ci_low", 0) > 1.0
    transfer_ok = (
        memo.get("status") == "PASS"
        and memo.get("memo_candidate_records", 0) >= 6000
        and portable.get("status") == "PASS"
    )
    package_ok = all(obj.get("status") == "PASS" for obj in [fmt, clean, integ, qual, codeq, manuscript])

    checks = {
        "optimizer_contract_evidence": safe_eq and transfer_ok and "selinger-style" in low,
        "formal_scope_evidence": proof_ok and "analytical spjag fragment" in low,
        "policy_scope_evidence": proof_ok and "not a new access-control model" in low,
        "efficiency_and_scalability_evidence": scale_ok and external_ok,
        "artifact_reproducibility_evidence": package_ok and (ROOT / "Dockerfile").exists() and (ROOT / "requirements.txt").exists(),
        "writing_and_format_evidence": fmt.get("status") == "PASS" and "efficient and certified" in low,
        "statistical_and_external_evidence": stats_ok and external_ok,
        "planner_transfer_evidence": package_ok and scale_ok and proof_ok and transfer_ok,
    }
    for name, ok in checks.items():
        if not ok:
            fail(f"unsupported claim boundary: {name}")

    out = {
        "status": "PASS" if not PROBLEMS else "FAIL",
        "problem_count": len(PROBLEMS),
        "problems": PROBLEMS,
        "all_checks_supported": all(checks.values()) and not PROBLEMS,
        "checks": checks,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "claim_boundary_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    if PROBLEMS:
        raise SystemExit("claim boundary audit failed")


if __name__ == "__main__":
    main()
