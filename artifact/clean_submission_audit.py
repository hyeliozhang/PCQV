#!/usr/bin/env python3
"""Final clean-package audit for the PCQV ICDE submission package.

This gate is deliberately stricter than numerical reproduction: it rejects stale
round artifacts, old log/report names, rendering dumps from previous attempts,
missing final logs, and ambiguous evidence-ledger paths.  The goal is to make the
zip look like a clean reviewer-facing supplemental package, not a working
scratch directory.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROBLEMS: list[str] = []

ALLOWED_TOP_FILES = {
    "EVIDENCE.md",
    "STATUS.md",
    "FORMAT_CHECK.md",
    "SCOPE_GUARD.md",
    "SUPPLEMENTAL_SUBMISSION.md",
    "SUBMISSION_AUDIT_REPORT.md",
    "REPRODUCIBILITY.md",
    "requirements.txt",
    "Dockerfile",
}
ALLOWED_TOP_DIRS = {"paper", "artifact", "results", "data", "logs", "render_check_final"}
FORBIDDEN_STALE_TOKENS = ["round3", "finalsprint"]
FORBIDDEN_PRESENTATION_TOKENS = ["ultimate", "bestpaper", "best-paper", "best_paper", "best paper", "strong accept", "strong-accept", "top-tier"]


def fail(msg: str) -> None:
    PROBLEMS.append(msg)


def main() -> None:
    # Normalize Python caches created by audits before checking the package.
    import shutil
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for pyc in ROOT.rglob("*.pyc"):
        try:
            pyc.unlink()
        except FileNotFoundError:
            continue

    # Top-level structure should be reviewer-facing and minimal.
    for p in ROOT.iterdir():
        name = p.name
        if p.is_file() and name not in ALLOWED_TOP_FILES:
            fail(f"unexpected top-level file: {name}")
        if p.is_dir() and name not in ALLOWED_TOP_DIRS:
            fail(f"unexpected top-level directory: {name}")

    # Old sprint labels should not survive in file names or markdown docs.
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        low = rel.lower()
        if low.startswith("logs/bestgate") or low.startswith("render_check_bestgate"):
            fail(f"stale bestgate artifact path: {rel}")
        for token in FORBIDDEN_STALE_TOKENS:
            if token in low:
                fail(f"stale round token in path: {rel}")
        if p.is_file() and p.suffix.lower() in {".md", ".tex", ".py"}:
            txt = p.read_text(encoding="utf-8", errors="replace").lower()
            if p.name != "clean_submission_audit.py":
                for token in FORBIDDEN_STALE_TOKENS:
                    if token in txt:
                        fail(f"stale round token '{token}' in text file: {rel}")
                for token in FORBIDDEN_PRESENTATION_TOKENS:
                    if token in txt or token in low:
                        fail(f"internal presentation token '{token}' leaked: {rel}")
        if "__pycache__" in rel or p.suffix == ".pyc":
            fail(f"python cache leaked: {rel}")
        if p.suffix == ".zip":
            fail(f"nested zip leaked into package: {rel}")

    # Logs should be a short final set only.
    logs = sorted((ROOT / "logs").glob("*")) if (ROOT / "logs").exists() else []
    if not logs:
        fail("logs directory is empty; final verifier/compile logs should be included")
    for log in logs:
        if not log.name.startswith("final_") or log.suffix != ".log":
            fail(f"non-final log name: {log.relative_to(ROOT).as_posix()}")

    # Evidence ledger should name real files and the final verifier log.
    evidence_path = ROOT / "EVIDENCE.md"
    if not evidence_path.exists():
        fail("missing EVIDENCE.md")
    else:
        evidence = evidence_path.read_text(encoding="utf-8")
        if "logs/final_verify_final_claims.log" not in evidence:
            fail("EVIDENCE.md does not point to the final verifier log")
        for rel in re.findall(r"`([^`]+)`", evidence):
            if rel.startswith("python ") or "*" in rel or rel.endswith("/"):
                continue
            for part in [x.strip() for x in rel.split(",") if x.strip()]:
                if part == "results/clean_submission_audit.json":
                    continue
                if not (ROOT / part).exists():
                    fail(f"evidence ledger path missing: {part}")

    # The final PDF should be the only paper PDF and paper build transients must not leak.
    paper_pdfs = sorted((ROOT / "paper").glob("*.pdf")) if (ROOT / "paper").exists() else []
    if [p.name for p in paper_pdfs] != ["main.pdf"]:
        fail(f"unexpected paper PDFs: {[p.name for p in paper_pdfs]}")
    transient_exts = {".aux", ".bbl", ".blg", ".log", ".out", ".synctex.gz"}
    for p in (ROOT / "paper").glob("*"):
        if any(p.name.endswith(ext) for ext in transient_exts):
            fail(f"paper transient leaked: {p.relative_to(ROOT).as_posix()}")

    out = {"status": "PASS" if not PROBLEMS else "FAIL", "problem_count": len(PROBLEMS), "problems": PROBLEMS}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "clean_submission_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    if PROBLEMS:
        raise SystemExit("clean submission audit failed")


if __name__ == "__main__":
    main()
