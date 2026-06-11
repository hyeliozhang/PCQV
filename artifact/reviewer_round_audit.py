#!/usr/bin/env python3
"""Reviewer-round audit for manuscript coherence, claims, and PDF layout.

This gate is intentionally independent of the numerical benchmark verifier.  It
checks issues that senior reviewers and area chairs often catch only after a
full read: RQ drift, theorem-scope overclaiming, unsafe baseline labeling,
paired-vs-global speedup ambiguity, missing evidence files, appendix/page-limit
risk, and stale code-quality macros.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper" / "main.tex"
PDF = ROOT / "paper" / "main.pdf"
RESULTS = ROOT / "results"
MIKTEX_BIN = Path(r"C:\Users\ASUS\AppData\Local\Programs\MiKTeX\miktex\bin\x64")
PROBLEMS: list[str] = []


def fail(message: str) -> None:
    PROBLEMS.append(message)


def load_json(rel: str) -> dict:
    path = ROOT / rel
    if not path.exists():
        fail(f"missing JSON evidence: {rel}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pragma: no cover - defensive audit code
        fail(f"cannot parse {rel}: {exc}")
        return {}


def resolve_command(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    candidate = MIKTEX_BIN / f"{name}.exe"
    return str(candidate) if candidate.exists() else name


def command_text(cmd: list[str]) -> str:
    resolved = [resolve_command(cmd[0]), *cmd[1:]]
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
        if cp.returncode != 0:
            fail(f"command failed: {' '.join(cmd)} :: {cp.stderr.strip()[:200]}")
        return cp.stdout
    except FileNotFoundError:
        fail(f"command not available: {cmd[0]}")
        return ""


def pdf_pages() -> int | None:
    out = command_text(["pdfinfo", str(PDF)])
    m = re.search(r"^Pages:\s+(\d+)", out, flags=re.MULTILINE)
    return int(m.group(1)) if m else None


def main() -> None:
    tex = PAPER.read_text(encoding="utf-8")
    body = re.split(r"\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}", tex, maxsplit=1)[0]

    # Submission-layout checks.
    pages = pdf_pages()
    if pages != 14:
        fail(f"expected 14 total PDF pages after 12-page body + ack/references, found {pages}")
    if "\\appendix" in tex.lower() or "Appendix" in body:
        fail("appendix marker appears in main body")
    if (
        "\\clearpage\n\n\\section*{AI-Generated Content Acknowledgement}" not in tex
        and "\\clearpage\n\n\\section*{Guidelines for Artificial Intelligence (AI)-Generated Content}" not in tex
    ):
        fail("AI-generated content acknowledgement does not start after an explicit clear page")
    if "\\bibliography{references}" not in tex:
        fail("bibliography call missing")

    # Reviewer coherence checks.
    checks = {
        "five RQs": "The evaluation asks five questions.",
        "SPJAG theorem scope": "core soundness theorem is deliberately stated for the analytical SPJAG fragment",
        "audit-surface caveat": "audit surface", 
        "unsafe no-policy label": "No policy & diagnostic",
        "unsafe aggregate label": "Unsafe aggregate & unsafe diag.",
        "diagnostic ablation caveat": "not presented as deployed methods",
        "paired/global speedup distinction": "ratio of global medians",
        "conservative paired speedup claim": "paired statistic is the conservative number",
        "transfer path": "portable efficiency/scalability interface",
    }
    for name, needle in checks.items():
        if needle not in tex:
            fail(f"missing reviewer-coherence text: {name}")
    if "RQ6:" in tex:
        fail("stale RQ6 text remains")
    if re.search(r"\bsmallest\b", body, flags=re.IGNORECASE):
        fail("global-minimality wording remains in the body; use compact/necessary wording instead")
    if "materialized protected-view plan" in tex:
        fail("stale materialized protected-view wording remains")
    if "Mask push & mask deterministic" in tex:
        fail("stale oversimplified mask-push rule remains")

    # Result and macro consistency beyond table-level checks.
    metrics = load_json("results/metrics.json")
    stat = load_json("results/statistical_confidence_audit.json")
    codeq = load_json("results/code_quality_audit.json")
    nums = (ROOT / "paper" / "generated_numbers.tex").read_text(encoding="utf-8")
    if metrics:
        for value in [
            metrics.get("num_performance_rows"),
            metrics.get("safe_equivalent", [None, None])[0],
            metrics.get("pcqv_speedup_vs_view_barrier_median"),
        ]:
            if value is not None and str(value) not in tex + nums:
                fail(f"important metric value not present in manuscript/macros: {value}")
    if stat:
        job = stat.get("joblike_pcqv_vs_view_barrier_ci", {})
        med = job.get("median")
        lo = job.get("ci_low")
        hi = job.get("ci_high")
        if med is not None:
            nums = (ROOT / "paper" / "generated_numbers.tex").read_text(encoding="utf-8")
            if f"\\newcommand{{\\JobPairedSpeedView}}{{{float(med):.3f}}}" not in nums:
                fail("JobPairedSpeedView macro is stale")
            if "\\JobPairedSpeedView{}$\\times$" not in tex:
                fail("JOB paired median speedup in text should use the generated macro")
        for val in [lo, hi]:
            if val is not None and f"{float(val):.2f}" not in nums:
                fail(f"JOB CI endpoint missing from generated macros: {val}")
    if codeq:
        py_files = codeq.get("python_files")
        total_lines = codeq.get("total_lines")
        if f"\\newcommand{{\\CodeAuditPyFiles}}{{{py_files}}}" not in nums:
            fail("CodeAuditPyFiles macro is stale")
        if f"\\newcommand{{\\CodeAuditTotalLines}}{{{total_lines}}}" not in nums:
            fail("CodeAuditTotalLines macro is stale")

    # Evidence ledger should point to files that actually exist.
    evidence = (ROOT / "EVIDENCE.md").read_text(encoding="utf-8") if (ROOT / "EVIDENCE.md").exists() else ""
    for rel in re.findall(r"`([^`]+)`", evidence):
        # Ignore globs and directories; check concrete paths only.
        if "*" in rel or rel.endswith("/") or rel.startswith("python "):
            continue
        if "," in rel:
            for part in [x.strip() for x in rel.split(",")]:
                if part and not (ROOT / part).exists():
                    fail(f"evidence ledger path missing: {part}")
        elif not (ROOT / rel).exists():
            fail(f"evidence ledger path missing: {rel}")

    # Bibliography hygiene.
    bib = (ROOT / "paper" / "references.bib").read_text(encoding="utf-8")
    keys = re.findall(r"@\w+\s*\{\s*([^,]+)", bib)
    cited = []
    for group in re.findall(r"\\cite\w*\s*\{([^}]+)\}", tex):
        cited.extend(k.strip() for k in group.split(",") if k.strip())
    if len(set(cited)) < 70:
        fail(f"fewer than 70 cited references: {len(set(cited))}")
    if sorted(set(cited) - set(keys)):
        fail(f"citations missing from BibTeX: {sorted(set(cited) - set(keys))[:5]}")

    status = "PASS" if not PROBLEMS else "FAIL"
    out = {
        "status": status,
        "problem_count": len(PROBLEMS),
        "problems": PROBLEMS,
        "pdf_pages": pages,
        "cited_references": len(set(cited)),
        "bib_entries": len(keys),
    }
    (RESULTS / "reviewer_round_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    if PROBLEMS:
        raise SystemExit("reviewer-round audit failed")


if __name__ == "__main__":
    main()
