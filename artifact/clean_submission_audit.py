#!/usr/bin/env python3
"""Clean artifact-package audit for the PCQV repository."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results"
PROBLEMS: list[str] = []

ALLOWED_TOP_FILES = {
    ".cloudignore",
    ".gitignore",
    "ARTIFACT_VALIDATION.md",
    "Dockerfile",
    "EVIDENCE.md",
    "FORMAT_CHECK.md",
    "README.md",
    "REPRODUCIBILITY.md",
    "SCOPE_GUARD.md",
    "STATUS.md",
    "SUPPLEMENTAL_SUBMISSION.md",
    "requirements.txt",
}
ALLOWED_TOP_DIRS = {"artifact", "data", "paper", "results"}
IGNORED_TOP_DIRS = {".git"}
FORBIDDEN_STALE_TOKENS = ["round" + "3", "final" + "sprint"]
FORBIDDEN_PRESENTATION_TOKENS = [
    "ulti" + "mate",
    "best" + "paper",
    "best" + "-" + "paper",
    "best" + "_" + "paper",
    "best " + "paper",
    "strong " + "accept",
    "strong" + "-" + "accept",
    "top" + "-" + "tier",
]


def fail(msg: str) -> None:
    PROBLEMS.append(msg)


def main() -> None:
    for cache in ROOT.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    for pyc in ROOT.rglob("*.pyc"):
        try:
            pyc.unlink()
        except FileNotFoundError:
            continue

    for path in ROOT.iterdir():
        name = path.name
        if path.is_file() and name not in ALLOWED_TOP_FILES:
            fail(f"unexpected top-level file: {name}")
        if path.is_dir() and name not in ALLOWED_TOP_DIRS and name not in IGNORED_TOP_DIRS:
            fail(f"unexpected top-level directory: {name}")

    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(".git/"):
            continue
        low = rel.lower()
        for token in FORBIDDEN_STALE_TOKENS:
            if token in low:
                fail(f"stale process token in path: {rel}")
        if path.is_file() and path.suffix.lower() in {".md", ".tex", ".py"}:
            txt = path.read_text(encoding="utf-8", errors="replace").lower()
            if path.name != "clean_submission_audit.py":
                for token in FORBIDDEN_STALE_TOKENS:
                    if token in txt:
                        fail(f"stale process token '{token}' in text file: {rel}")
                for token in FORBIDDEN_PRESENTATION_TOKENS:
                    if token in txt or token in low:
                        fail(f"internal presentation token '{token}' leaked: {rel}")
        if "__pycache__" in rel or path.suffix == ".pyc":
            fail(f"python cache leaked: {rel}")
        if path.suffix == ".zip":
            fail(f"nested zip leaked into repository: {rel}")

    evidence_path = ROOT / "EVIDENCE.md"
    if not evidence_path.exists():
        fail("missing EVIDENCE.md")
    else:
        evidence = evidence_path.read_text(encoding="utf-8")
        for rel in re.findall(r"`([^`]+)`", evidence):
            if "\n" in rel:
                continue
            if rel.startswith("python ") or "*" in rel or rel.endswith("/"):
                continue
            for part in [x.strip() for x in rel.split(",") if x.strip()]:
                if part.startswith("http://") or part.startswith("https://"):
                    continue
                if not (ROOT / part).exists():
                    fail(f"evidence ledger path missing: {part}")

    paper_pdfs = sorted((ROOT / "paper").glob("*.pdf")) if (ROOT / "paper").exists() else []
    if [path.name for path in paper_pdfs] != ["main.pdf"]:
        fail(f"unexpected paper PDFs: {[path.name for path in paper_pdfs]}")
    transient_exts = {".aux", ".bbl", ".blg", ".log", ".out", ".synctex.gz"}
    for path in (ROOT / "paper").glob("*"):
        if any(path.name.endswith(ext) for ext in transient_exts):
            fail(f"paper build transient leaked: {path.relative_to(ROOT).as_posix()}")

    out = {
        "status": "PASS" if not PROBLEMS else "FAIL",
        "problem_count": len(PROBLEMS),
        "problems": PROBLEMS,
    }
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "clean_submission_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    if PROBLEMS:
        raise SystemExit("clean package audit failed")


if __name__ == "__main__":
    main()
