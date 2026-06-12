#!/usr/bin/env python3
"""Format/claim gate audit for manuscript structure, page use, references, and PDF hygiene.

This audit catches quality issues that are not numerical-result errors: a
report-like section tree, unused page-limit capacity, appendix/page-limit risk,
rasterized figures, too few real citations, Type 3 fonts, and stale final-page
placement. It uses only the Python standard library plus the Poppler command-line
utilities already used by the submission checks.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper" / "main.tex"
PDF = ROOT / "paper" / "main.pdf"
BIB = ROOT / "paper" / "references.bib"
RESULTS = ROOT / "results"
EXTRA_BIN_DIRS = [Path(p) for p in os.environ.get("PCQV_POPPLER_BIN", "").split(os.pathsep) if p]
PROBLEMS: list[str] = []
DETAILS: dict[str, object] = {}


def fail(msg: str) -> None:
    PROBLEMS.append(msg)


def resolve_command(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for directory in EXTRA_BIN_DIRS:
        candidate = directory / (f"{name}.exe" if os.name == "nt" else name)
        if candidate.exists():
            return str(candidate)
    return name


def run(cmd: list[str], cwd: Path = ROOT) -> subprocess.CompletedProcess[str]:
    resolved = [resolve_command(cmd[0]), *cmd[1:]]
    try:
        return subprocess.run(
            resolved,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except FileNotFoundError:
        fail(f"missing command: {cmd[0]}")
        return subprocess.CompletedProcess(cmd, 127, "", f"missing {cmd[0]}")


def parse_pdfinfo_pages() -> int | None:
    cp = run(["pdfinfo", str(PDF)])
    if cp.returncode != 0:
        fail(f"pdfinfo failed: {cp.stderr.strip()[:200]}")
        return None
    m = re.search(r"^Pages:\s+(\d+)", cp.stdout, re.MULTILINE)
    if not m:
        fail("pdfinfo did not report page count")
        return None
    return int(m.group(1))


def pdftotext_page(page: int) -> str:
    cp = run(["pdftotext", "-f", str(page), "-l", str(page), str(PDF), "-"])
    if cp.returncode != 0:
        fail(f"pdftotext failed for page {page}: {cp.stderr.strip()[:200]}")
        return ""
    return cp.stdout


def read_pgm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    # Binary PGM: P5\n[#comment\n]w h\nmaxval\n<bytes>
    pos = 0
    def next_token() -> bytes:
        nonlocal pos
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        if pos < len(data) and data[pos:pos+1] == b"#":
            while pos < len(data) and data[pos:pos+1] not in b"\r\n":
                pos += 1
            return next_token()
        start = pos
        while pos < len(data) and data[pos] not in b" \t\r\n":
            pos += 1
        return data[start:pos]
    magic = next_token()
    if magic != b"P5":
        raise ValueError(f"unexpected PGM magic {magic!r}")
    width = int(next_token())
    height = int(next_token())
    maxval = int(next_token())
    if maxval != 255:
        raise ValueError(f"unsupported PGM maxval {maxval}")
    while pos < len(data) and data[pos] in b" \t\r\n":
        pos += 1
    pixels = data[pos:pos + width * height]
    if len(pixels) != width * height:
        raise ValueError("PGM pixel payload has unexpected size")
    return width, height, pixels


def page_fill_inches(page: int = 12, dpi: int = 120) -> dict[str, float]:
    with tempfile.TemporaryDirectory() as tmp_s:
        tmp = Path(tmp_s)
        prefix = tmp / "page"
        cp = run(["pdftoppm", "-f", str(page), "-l", str(page), "-r", str(dpi), "-gray", str(PDF), str(prefix)])
        if cp.returncode != 0:
            fail(f"pdftoppm failed for page {page}: {cp.stderr.strip()[:200]}")
            return {}
        pgms = list(tmp.glob("*.pgm"))
        if not pgms:
            fail("pdftoppm produced no PGM render")
            return {}
        width, height, pixels = read_pgm(pgms[0])
        def last_nonwhite(x0: int, x1: int) -> int:
            last = 0
            for y in range(height):
                row = pixels[y * width:(y + 1) * width]
                dark = sum(1 for val in row[x0:x1] if val < 245)
                if dark > 5:
                    last = y
            return last
        left = last_nonwhite(0, width // 2)
        right = last_nonwhite(width // 2, width)
        both = last_nonwhite(0, width)
        return {
            "left_blank_inches": round((height - left - 1) / dpi, 3),
            "right_blank_inches": round((height - right - 1) / dpi, 3),
            "overall_blank_inches": round((height - both - 1) / dpi, 3),
            "width_px": width,
            "height_px": height,
            "dpi": dpi,
        }


def main() -> None:
    tex = PAPER.read_text(encoding="utf-8")
    body = re.split(r"\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}", tex, maxsplit=1)[0]
    bib = BIB.read_text(encoding="utf-8")

    pages = parse_pdfinfo_pages()
    DETAILS["pdf_pages"] = pages
    if pages != 14:
        fail(f"expected 14 total pages, found {pages}")

    # Official-page-limit placement: body must end on page 12; ack/references start page 13.
    page12 = pdftotext_page(12)
    page13 = pdftotext_page(13)
    compact12 = re.sub(r"[^A-Z]", "", page12.upper())
    compact13 = re.sub(r"[^A-Z]", "", page13.upper())
    if "AIGENERATED" in compact12 or "REFERENCES" in compact12:
        fail("AI acknowledgement or references appear on body page 12")
    if "AIGENERATED" not in compact13 or "REFERENCES" not in compact13:
        fail("page 13 does not begin the acknowledgement/references material")

    fill = page_fill_inches(12)
    DETAILS["page12_fill"] = fill
    if fill:
        if fill.get("left_blank_inches", 99) > 1.35:
            fail(f"left column on page 12 has too much bottom whitespace: {fill['left_blank_inches']} in")
        if fill.get("right_blank_inches", 99) > 1.35:
            fail(f"right column on page 12 has too much bottom whitespace: {fill['right_blank_inches']} in")

    section_count = len(re.findall(r"^\\section\{", body, re.MULTILINE))
    subsection_count = len(re.findall(r"^\\subsection\{", body, re.MULTILINE))
    DETAILS["body_section_count"] = section_count
    DETAILS["body_subsection_count"] = subsection_count
    if section_count > 8:
        fail(f"body has report-like section count: {section_count}")
    if subsection_count > 16:
        fail(f"body has report-like subsection count: {subsection_count}")
    for bad in ["Appendix", "\\appendix", "artifact/", "results/", "README"]:
        if bad in body:
            fail(f"body contains report/artifact marker: {bad}")
    if "ICDE-Style" in body:
        fail("body contains venue-pandering subsection wording: ICDE-Style")
    overclaim_patterns = [
        r"smallest",
        r"minimal optimizer-facing",
        r"compact complete optimizer",
        r"not used to cheat",
        r"blanket domination",
        "best " + "paper",
        "strong " + "accept",
    ]
    for pattern in overclaim_patterns:
        if re.search(pattern, body, flags=re.IGNORECASE):
            fail(f"overclaim or reviewer-facing internal wording remains: {pattern}")

    includes = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}", tex)
    DETAILS["figure_includes"] = includes
    if not includes:
        fail("no figures included")
    for inc in includes:
        if not inc.endswith(".pdf"):
            fail(f"non-vector or extensionless figure include: {inc}")
        if not (ROOT / "paper" / inc).exists():
            fail(f"missing figure include target: paper/{inc}")

    # Bibliography hygiene: enough cited work, every citation has a BibTeX entry, and no fake placeholder keys.
    keys = re.findall(r"@\w+\s*\{\s*([^,]+)", bib)
    cited: list[str] = []
    for group in re.findall(r"\\cite\w*\s*\{([^}]+)\}", tex):
        cited.extend(k.strip() for k in group.split(",") if k.strip())
    cited_unique = sorted(set(cited))
    DETAILS["cited_references"] = len(cited_unique)
    DETAILS["bib_entries"] = len(keys)
    missing = sorted(set(cited_unique) - set(keys))
    if missing:
        fail(f"missing BibTeX entries for citations: {missing[:10]}")
    if len(cited_unique) < 70:
        fail(f"expected at least 70 cited references, found {len(cited_unique)}")
    if len(keys) < 70:
        fail(f"expected at least 70 BibTeX entries, found {len(keys)}")
    placeholder_pattern = "TO" + "DO|PLACEHOLDER|citation needed|TBD"
    if re.search(placeholder_pattern, tex + bib, flags=re.IGNORECASE):
        fail("placeholder or unfinished citation text remains")

    fonts = run(["pdffonts", str(PDF)]).stdout
    DETAILS["has_type3_fonts"] = "Type 3" in fonts
    DETAILS["font_types"] = sorted(set(re.findall(r"\bType\s+\d|\bType\s+1|\bTrueType", fonts)))
    if "Type 3" in fonts:
        fail("PDF contains Type 3 fonts")

    status = "PASS" if not PROBLEMS else "FAIL"
    out = {"status": status, "problem_count": len(PROBLEMS), "problems": PROBLEMS, "details": DETAILS}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "format_claim_gate_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    if PROBLEMS:
        raise SystemExit("format/claim gate audit failed")


if __name__ == "__main__":
    main()
