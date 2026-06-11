#!/usr/bin/env python3
"""Generate the publication-quality empirical figure suite for the manuscript.

The figure is intentionally designed as one two-column IEEE figure rather than a
collection of small debug plots.  It uses vector PDF output, submission-safe
fonts, color-blind-safe method colors, direct annotations, and low-ink panels
so that the performance and scalability story is readable at print size.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import csv
import json
import math
import statistics

import matplotlib as mpl
mpl.rcParams.update({
    "pdf.use14corefonts": False,
    "ps.useafm": False,
    "pdf.fonttype": 42,
    "ps.fonttype": 42,
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Nimbus Roman", "Nimbus Roman No9 L", "DejaVu Serif"],
    "mathtext.fontset": "dejavuserif",
    "axes.labelsize": 7.6,
    "axes.titlesize": 7.6,
    "xtick.labelsize": 6.8,
    "ytick.labelsize": 6.8,
    "legend.fontsize": 6.6,
    "axes.linewidth": 0.55,
    "xtick.major.width": 0.50,
    "ytick.major.width": 0.50,
    "xtick.major.size": 2.6,
    "ytick.major.size": 2.6,
    "lines.linewidth": 1.35,
    "lines.markersize": 4.0,
    "figure.dpi": 150,
})
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, TwoSlopeNorm
from matplotlib.ticker import FuncFormatter, LogLocator, NullFormatter

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "results"
FIG = ROOT / "paper" / "figures"
FIG.mkdir(parents=True, exist_ok=True)

COLORS = {
    "pcqv": "#0B6FAE",
    "predicate_injection": "#159A74",
    "post_join_filter": "#E0A31A",
    "view_barrier": "#C44E22",
    "oblivious_forced": "#666666",
    "pcqv_forced": "#5D5D5D",
    "pcqv_no_stats": "#8A8A8A",
    "pcqv_greedy": "#A5A5A5",
    "pcqv_no_order": "#BDBDBD",
    "oracle_order": "#3F3F3F",
}
LABELS = {
    "pcqv": "PCQV",
    "predicate_injection": "Injection",
    "post_join_filter": "Post-filter",
    "view_barrier": "View barrier",
    "oblivious_forced": "Oblivious",
    "pcqv_forced": "Forced",
    "pcqv_no_stats": "No stats",
    "pcqv_greedy": "Greedy",
    "pcqv_no_order": "No order",
    "oracle_order": "Oracle",
}
MARKERS = {
    "pcqv": "o",
    "predicate_injection": "s",
    "post_join_filter": "^",
    "view_barrier": "D",
    "oblivious_forced": "v",
}
LINESTYLES = {
    "pcqv": "-",
    "predicate_injection": "--",
    "post_join_filter": "-.",
    "view_barrier": ":",
    "oblivious_forced": "-.",
}


def read_csv(name: str) -> list[dict[str, str]]:
    with open(RES / name, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def read_json(name: str) -> dict[str, object]:
    path = RES / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def count_csv_rows(name: str) -> int:
    path = RES / name
    if not path.exists():
        return 0
    with open(path, newline="", encoding="utf-8") as handle:
        return sum(1 for _ in csv.DictReader(handle))


def median(values) -> float:
    clean = [float(v) for v in values if v not in ("", None, "nan", "NaN")]
    if not clean:
        return float("nan")
    return statistics.median(clean)


def percentile(values, p: float) -> float:
    clean = sorted(float(v) for v in values if v not in ("", None, "nan", "NaN"))
    if not clean:
        return float("nan")
    if len(clean) == 1:
        return clean[0]
    k = (len(clean) - 1) * p / 100.0
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return clean[lo]
    return clean[lo] * (hi - k) + clean[hi] * (k - lo)


def fmt_axis(x, _pos=None) -> str:
    if x == 0:
        return "0"
    if 0 < abs(x) < 0.1:
        return f"{x:.2f}"
    if 0 < abs(x) < 1:
        return f"{x:.2f}".rstrip("0").rstrip(".")
    return f"{x:.0f}" if abs(x) >= 10 else f"{x:.1f}".rstrip("0").rstrip(".")


def light_grid(ax, axis: str = "both") -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#555555")
        ax.spines[side].set_linewidth(0.55)
    if axis:
        ax.grid(axis=axis, color="#D6DADF", lw=0.38, alpha=0.70)
        ax.set_axisbelow(True)
    ax.tick_params(pad=1.8)


def panel_label(ax, letter: str) -> None:
    ax.text(0.0, 1.035, letter, transform=ax.transAxes,
            ha="left", va="bottom", fontsize=7.9, fontweight="bold", color="#222222")


def method_values(data: list[dict[str, str]], method: str, field: str = "latency_ms") -> list[float]:
    return [float(r[field]) for r in data if r.get("method") == method]


def grouped_median(data: list[dict[str, str]], method: str, field: str) -> tuple[list[float], list[float]]:
    keys = sorted({float(r[field]) for r in data})
    ys = [median([r["latency_ms"] for r in data if r["method"] == method and float(r[field]) == key]) for key in keys]
    return keys, ys


def fmt_count(value: object) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "n/a"


def fmt_pair(value: object) -> str:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        return f"{fmt_count(value[0])} / {fmt_count(value[1])}"
    return "n/a"


def plot_latency_distribution(ax, perf: list[dict[str, str]]) -> None:
    methods = ["pcqv", "predicate_injection", "post_join_filter", "oblivious_forced", "view_barrier"]
    data = [method_values(perf, method) for method in methods]
    positions = list(range(len(methods), 0, -1))
    box = ax.boxplot(data, vert=False, positions=positions, widths=0.54, patch_artist=True,
                     showfliers=False, whis=(5, 95), medianprops={"color": "#111111", "linewidth": 0.95},
                     whiskerprops={"color": "#6B6B6B", "linewidth": 0.65},
                     capprops={"color": "#6B6B6B", "linewidth": 0.65})
    for patch, method in zip(box["boxes"], methods):
        patch.set(facecolor=COLORS[method], edgecolor="#333333", alpha=0.72, linewidth=0.55)
    pcqv_med = median(data[0])
    ax.axvline(pcqv_med, color=COLORS["pcqv"], lw=0.9, alpha=0.55)
    ax.set_xscale("log")
    ax.set_xlim(0.045, 42)
    ax.set_yticks(positions, [LABELS[m] for m in methods])
    ax.set_xlabel("Latency per query (ms, log scale)")
    ax.xaxis.set_major_locator(LogLocator(base=10, numticks=4))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.xaxis.set_major_formatter(FuncFormatter(fmt_axis))
    for pos, method, values in zip(positions, methods, data):
        med = median(values)
        q95 = percentile(values, 95)
        text = f"med {med:.3f}"
        if method != "pcqv":
            ratio = med / pcqv_med
            text += f" | {ratio:.2f}x" if ratio < 1.10 else f" | {ratio:.1f}x"
        ax.text(min(q95 * 1.20, 37.0), pos, text, fontsize=6.45, va="center", ha="left", color="#222222")
    light_grid(ax, axis="x")
    panel_label(ax, "A")


def plot_scale_sweep(ax, scale: list[dict[str, str]]) -> None:
    methods = ["pcqv", "predicate_injection", "oblivious_forced", "view_barrier"]
    for method in methods:
        xs, ys = grouped_median(scale, method, "scale")
        ax.plot(xs, ys, color=COLORS[method], linestyle=LINESTYLES[method], marker=MARKERS[method],
                mfc="white" if method != "pcqv" else COLORS[method], mec=COLORS[method], mew=0.85,
                lw=1.5, label=LABELS[method])
        ax.text(xs[-1] * 1.07, ys[-1], LABELS[method], color=COLORS[method], fontsize=6.55,
                fontweight="bold" if method in {"pcqv", "view_barrier"} else "normal",
                va="center", ha="left")
    scales = sorted({float(r["scale"]) for r in scale})
    last = scales[-1]
    ax.set_xscale("log", base=2)
    ax.set_yscale("log")
    ax.set_xlim(min(scales) * 0.88, last * 1.65)
    ax.set_xticks(scales)
    ax.set_xticklabels([f"{x:.2f}".rstrip("0").rstrip(".") for x in scales])
    ax.set_xlabel("Data-size scale factor (log2)")
    ax.set_ylabel("Median latency (ms, log)")
    ax.yaxis.set_major_locator(LogLocator(base=10, numticks=4))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_major_formatter(FuncFormatter(fmt_axis))
    light_grid(ax, axis="both")
    panel_label(ax, "B")


def plot_policy_stress_heatmap(ax, perf: list[dict[str, str]]) -> None:
    selectivities = sorted({float(r["selectivity"]) for r in perf})
    complexities = sorted({int(r["complexity"]) for r in perf}, reverse=True)
    matrix: list[list[float]] = []
    for complexity in complexities:
        row: list[float] = []
        for selectivity in selectivities:
            pc = median(r["latency_ms"] for r in perf if r["method"] == "pcqv" and float(r["selectivity"]) == selectivity and int(r["complexity"]) == complexity)
            view = median(r["latency_ms"] for r in perf if r["method"] == "view_barrier" and float(r["selectivity"]) == selectivity and int(r["complexity"]) == complexity)
            row.append(view / pc)
        matrix.append(row)
    flat = [value for row in matrix for value in row]
    cmap = LinearSegmentedColormap.from_list("policy_ratio", ["#A6611A", "#F7F7F7", "#018571"])
    norm = TwoSlopeNorm(vmin=min(min(flat), 0.65), vcenter=1.0, vmax=max(max(flat), 1.35))
    image = ax.imshow(matrix, aspect="auto", cmap=cmap, norm=norm)
    ax.set_xticks(range(len(selectivities)), [f"{x:.3f}".rstrip("0").rstrip(".") for x in selectivities])
    ax.set_yticks(range(len(complexities)), [str(x) for x in complexities])
    ax.set_xlabel("Policy selectivity")
    ax.set_ylabel("Complexity level")
    for i, row in enumerate(matrix):
        for j, value in enumerate(row):
            ax.text(j, i, f"{value:.1f}x", ha="center", va="center",
                    fontsize=6.75, color="white" if value >= 4.0 or value <= 0.8 else "#202020",
                    fontweight="bold" if value >= 4.0 else "normal")
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)
    cbar = plt.colorbar(image, ax=ax, fraction=0.045, pad=0.018)
    cbar.set_label("View / PCQV (>1 favors PCQV)", fontsize=6.45, labelpad=1.5)
    cbar.ax.tick_params(labelsize=6.45, length=2.0, pad=1.0)
    panel_label(ax, "C")


def plot_planner_ablation(ax, ablation: list[dict[str, str]]) -> None:
    methods = ["pcqv", "pcqv_forced", "pcqv_no_stats", "pcqv_greedy", "pcqv_no_order", "oracle_order"]
    vals = [median(r["latency_ms"] for r in ablation if r["method"] == method) for method in methods]
    pc = vals[0]
    positions = list(range(len(methods), 0, -1))
    for pos, method, val in zip(positions, methods, vals):
        color = COLORS[method]
        ax.hlines(pos, min(pc, val), max(pc, val), color=color, lw=3.1, alpha=0.72)
        ax.plot(val, pos, marker="o", ms=4.1, color=color, mec="#222222", mew=0.25)
        offset = max((max(vals) - min(vals)) * 0.035, 0.004)
        ax.text(val + offset, pos + 0.13, f"{val:.3f}", fontsize=6.25,
                ha="left", va="bottom", color="#222222")
    ax.axvline(pc, color=COLORS["pcqv"], lw=0.8, alpha=0.52)
    ax.set_yticks(positions, [LABELS[m] for m in methods])
    ax.set_ylim(0.55, len(methods) + 0.55)
    pad = max((max(vals) - min(vals)) * 0.22, 0.025)
    ax.set_xlim(max(0.0, min(vals) - pad), max(vals) + pad)
    ax.xaxis.set_major_formatter(FuncFormatter(fmt_axis))
    ax.set_xlabel("Median latency (ms)")
    light_grid(ax, axis="x")
    panel_label(ax, "D")


def plot_performance_suite() -> None:
    perf = read_csv("performance.csv")
    scale = read_csv("scale_sensitivity.csv")
    ablation = read_csv("ablation.csv")

    fig = plt.figure(figsize=(7.05, 4.18))
    gs = fig.add_gridspec(2, 2, left=0.088, right=0.970, top=0.958, bottom=0.112,
                          wspace=0.32, hspace=0.42, height_ratios=[1.0, 0.98])
    plot_latency_distribution(fig.add_subplot(gs[0, 0]), perf)
    plot_scale_sweep(fig.add_subplot(gs[0, 1]), scale)
    plot_policy_stress_heatmap(fig.add_subplot(gs[1, 0]), perf)
    plot_planner_ablation(fig.add_subplot(gs[1, 1]), ablation)

    pdf_path = FIG / "fig_performance_suite.pdf"
    png_path = FIG / "fig_performance_suite.png"
    fig.savefig(pdf_path)
    fig.savefig(png_path, dpi=420)
    plt.close(fig)
    print(f"wrote {pdf_path}")
    print(f"wrote {png_path}")


def main() -> None:
    plot_performance_suite()


if __name__ == "__main__":
    main()
