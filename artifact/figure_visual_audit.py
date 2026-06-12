#!/usr/bin/env python3
"""Audit manuscript figure quality and required disclosure placement.

This gate is intentionally visual-production oriented: it checks that manuscript
figures are regenerated from code, included as vector PDF, use embedded fonts,
use a polished two-column panel layout plus high-resolution PNG previews for
manual inspection, avoid Matplotlib's default blue-only style, and keep the
required disclosure concise.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper" / "main.tex"
FIG = ROOT / "paper" / "figures"
RESULTS = ROOT / "results"
PROBLEMS: list[str] = []
DETAILS: dict[str, object] = {}
EXPECTED = ["fig_performance_suite.pdf"]
EXTRA_BIN_DIRS = [Path(p) for p in os.environ.get("PCQV_POPPLER_BIN", "").split(os.pathsep) if p]


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


def run(args: list[str]) -> subprocess.CompletedProcess[str]:
    resolved = [resolve_command(args[0]), *args[1:]]
    try:
        return subprocess.run(
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
        return subprocess.CompletedProcess(args, 127, "", "")


def parse_pdfinfo(text: str) -> dict[str, object]:
    out: dict[str, object] = {}
    m = re.search(r"^Pages:\s+(\d+)", text, re.M)
    if m:
        out["pages"] = int(m.group(1))
    m = re.search(r"^Page size:\s+([0-9.]+) x ([0-9.]+) pts", text, re.M)
    if m:
        out["width_pt"] = float(m.group(1))
        out["height_pt"] = float(m.group(2))
    m = re.search(r"^File size:\s+(\d+) bytes", text, re.M)
    if m:
        out["file_size_bytes"] = int(m.group(1))
    return out


def png_size(path: Path) -> tuple[int, int] | None:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception as exc:
        fail(f"cannot read PNG preview {path.relative_to(ROOT)}: {exc}")
        return None


def main() -> None:
    tex = PAPER.read_text(encoding="utf-8")
    includes = re.findall(r"\\includegraphics(?:\[[^\]]*\])?\{figures/([^}]+)\}", tex)
    DETAILS["figure_includes"] = includes
    if includes != EXPECTED:
        fail(f"figure include order/list changed: expected {EXPECTED}, found {includes}")
    if any(not inc.endswith(".pdf") for inc in includes):
        fail("all manuscript figure includes must be explicit vector PDFs")
    if "figures/fig_performance_suite.pdf" not in tex or "Five-size scale sweep" not in tex:
        fail("scale-sweep evidence is not presented in the two-column empirical figure suite")

    script = (ROOT / "artifact" / "make_figures.py").read_text(encoding="utf-8")
    suite_tokens = [
        "panel_label(ax, \"A\")",
        "panel_label(ax, \"B\")",
        "panel_label(ax, \"C\")",
        "panel_label(ax, \"D\")",
        "plot_latency_distribution",
        "plot_scale_sweep",
        "plot_policy_stress_heatmap",
        "plot_planner_ablation",
    ]
    for token in suite_tokens:
        if token not in script:
            fail(f"performance-suite generator is missing panel token: {token}")

    required_style_tokens = [
        "pdf.fonttype", "ps.fonttype", "COLORS", "MARKERS", "LINESTYLES",
        "mfc=", "edgecolor", "boxplot", "imshow", "plot_planner_ablation",
        "fig_performance_suite.pdf", "plot_performance_suite",
    ]
    for token in required_style_tokens:
        if token not in script:
            fail(f"make_figures.py lacks publication-style token: {token}")
    forbidden_style_tokens = [
        "seaborn", "#1f77b4", "plt.style.use", "style=\"default\"",
        "FancyBboxPatch", "Reproducibility ledger",
    ]
    for token in forbidden_style_tokens:
        if token.lower() in script.lower():
            fail(f"make_figures.py still contains default/undesired style token: {token}")

    fig_details: dict[str, object] = {}
    for fig in EXPECTED:
        pdf = FIG / fig
        png = pdf.with_suffix(".png")
        if not pdf.exists():
            fail(f"missing figure PDF: paper/figures/{fig}")
            continue
        if not png.exists():
            fail(f"missing PNG preview for manual visual inspection: {png.relative_to(ROOT)}")
        info_cp = run(["pdfinfo", str(pdf)])
        info = parse_pdfinfo(info_cp.stdout)
        fonts = run(["pdffonts", str(pdf)]).stdout
        png_dims = png_size(png) if png.exists() else None
        fig_details[fig] = {"pdfinfo": info, "png_size": png_dims}
        if info.get("pages") != 1:
            fail(f"figure {fig} should be a single-page PDF")
        w = float(info.get("width_pt", 0))
        h = float(info.get("height_pt", 0))
        if not (450 <= w <= 535 and 220 <= h <= 315):
            fail(f"figure {fig} has non-camera-ready two-column dimensions: {w} x {h} pt")
        if int(info.get("file_size_bytes", 0)) < 8000:
            fail(f"figure {fig} looks too small to contain full vector/text detail")
        if "Type 3" in fonts:
            fail(f"figure {fig} contains Type 3 fonts")
        if png_dims is not None and (png_dims[0] < 2200 or png_dims[1] < 1100):
            fail(f"figure {fig} PNG preview resolution is too low for two-column visual inspection: {png_dims}")

    ack_match = re.search(
        r"\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}\s*(.*?)\s*\\bibliographystyle",
        tex,
        re.S,
    )
    if not ack_match:
        fail("missing required disclosure section")
    else:
        ack = " ".join(ack_match.group(1).split())
        DETAILS["disclosure_text"] = ack
        sentence_count = len([x for x in re.split(r"(?<=[.!?])\s+", ack) if x])
        if sentence_count != 1:
            fail(f"required disclosure should be one concise sentence, found {sentence_count}")
        if "AI was used only for language and grammar polishing." != ack:
            fail("required disclosure should match the language/grammar polishing statement")

    # Avoid common machine-generated promotional phrasing in the body.
    body = re.split(r"\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}", tex, maxsplit=1)[0]
    low = body.lower()
    for token in ["groundbreaking", "game-changing", "unprecedented", "revolutionary", "best " + "paper", "strong " + "accept"]:
        if token in low:
            fail(f"promotional or reviewer-internal phrase remains in manuscript body: {token}")

    DETAILS["figures"] = fig_details
    out = {"status": "PASS" if not PROBLEMS else "FAIL", "problem_count": len(PROBLEMS), "problems": PROBLEMS, "details": DETAILS}
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "figure_visual_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    if PROBLEMS:
        raise SystemExit("figure visual audit failed")


if __name__ == "__main__":
    main()
