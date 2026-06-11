#!/usr/bin/env python3
"""Check that manuscript tables/figures match generated benchmark results.

The existing claim verifier checks result files. This audit specifically guards
against the failure mode where the PDF/manuscript carries stale hand-entered
numbers or stale figure assets after results have changed.
"""
from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAPER = ROOT / "paper" / "main.tex"
RESULTS = ROOT / "results"

PROBLEMS: list[str] = []
DETAILS: dict[str, object] = {}


def fail(message: str) -> None:
    PROBLEMS.append(message)


def fmt(value: float) -> str:
    return f"{value:.3f}"


def median(values: list[float]) -> float:
    if not values:
        raise ValueError("cannot take median of empty list")
    return statistics.median(values)


def require_contains(tex: str, needle: str, label: str) -> None:
    if needle not in tex:
        fail(f"missing or stale manuscript value for {label}: expected `{needle}`")


def main() -> None:
    tex = PAPER.read_text(encoding="utf-8")
    metrics = json.loads((RESULTS / "metrics.json").read_text(encoding="utf-8"))
    perf_rows = list(csv.DictReader((RESULTS / "performance.csv").open(newline="")))
    corr_rows = list(csv.DictReader((RESULTS / "correctness.csv").open(newline="")))

    stale_tokens = [
        "0.316", "2.736", "1.284", "1.324", "0.354 & 1.341",
        "0.382 & 0.924", "Q15 & 0.412", "Unsafe aggregate & 128 & 40 & 3,801",
        "figures/fig_method_latency.png", "figures/fig_selectivity_sensitivity.png",
        "figures/fig_policy_complexity.png", "figures/fig_ablation.png",
    ]
    for token in stale_tokens:
        if token in tex:
            fail(f"stale manuscript token still present: {token}")

    # Reviewer-facing coherence checks: RQ numbering, theorem scope, baseline labels,
    # and ratio-of-medians vs paired-median language must not drift.
    if "The evaluation asks five questions." not in tex:
        fail("evaluation setup does not state five RQs")
    for rq in ["RQ1:", "RQ2:", "RQ3:", "RQ4:", "RQ5:"]:
        if rq not in tex:
            fail(f"missing evaluation question label {rq}")
    if "RQ6:" in tex:
        fail("stale RQ6 label remains in manuscript")
    if "core soundness theorem is deliberately stated for the analytical SPJAG fragment" not in tex:
        fail("theorem scope is not explicitly limited to SPJAG")
    if "No policy & diagnostic" not in tex or "Unsafe aggregate & unsafe diag." not in tex:
        fail("baseline table still labels unsafe rows as ordinary references")
    if "ratio of global medians" not in tex or "paired statistic is the conservative number" not in tex:
        fail("JOB speedup language does not distinguish paired median from ratio of medians")

    # The paper now uses a single two-column, camera-ready performance suite.
    # The individual plot PDFs are still regenerated for preview/debugging, but the
    # reviewer-facing manuscript should include the suite rather than five small
    # one-column Matplotlib panels.
    require_contains(tex, "figures/fig_performance_suite.pdf", "two-column empirical figure suite")
    if not (ROOT / "paper" / "figures" / "fig_performance_suite.pdf").exists():
        fail("missing regenerated performance-suite figure asset: paper/figures/fig_performance_suite.pdf")
    for stale_fig in [
        "figures/fig_method_latency.pdf", "figures/fig_selectivity_sensitivity.pdf",
        "figures/fig_policy_complexity.pdf", "figures/fig_scale_sweep.pdf", "figures/fig_ablation.pdf",
    ]:
        if stale_fig in tex:
            fail(f"paper should use the unified performance suite rather than stale separate include: {stale_fig}")

    method_labels = {
        "pcqv_no_stats": r"\pcqv{} no stats",
        "pcqv": r"\pcqv{}",
        "pcqv_forced": r"\pcqv{} forced",
        "oracle_order": "Oracle order",
        "predicate_injection": "Injection",
        "oblivious_forced": "Oblivious",
        "post_join_filter": "Post-filter",
        "view_barrier": "View barrier",
        "no_policy": "No policy (diag.)",
    }
    performance_order = [
        "pcqv", "predicate_injection", "oblivious_forced",
        "post_join_filter", "view_barrier", "no_policy",
    ]
    expected_perf_rows: list[str] = []
    for method in performance_order:
        med = fmt(float(metrics["performance_median_ms"][method]))
        p95 = fmt(float(metrics["performance_p95_ms"][method]))
        row = f"{method_labels[method]} & {med} & {p95} \\\\" 
        expected_perf_rows.append(row)
        require_contains(tex, row, f"performance table row {method}")
    DETAILS["performance_rows"] = expected_perf_rows

    by_method = ["pcqv", "predicate_injection", "oblivious_forced", "view_barrier"]
    query_order = [
        ("Q1", "q1_customer_orders"),
        ("Q5", "q5_high_value_segments"),
        ("Q6", "q6_lineitem_product_count"),
        ("Q10", "q10_acl_customer_rows"),
        ("Q11", "q11_order_ticket_bridge"),
        ("Q12", "q12_product_segment_revenue"),
        ("Q13", "q13_order_lineitem_rows"),
        ("Q14", "q14_support_owner_rows"),
        ("Q15", "q15_clearance_product_rows"),
        ("Q16", "q16_region_product_mix"),
    ]
    expected_query_rows: list[str] = []
    for short, query in query_order:
        vals = []
        for method in by_method:
            xs = [float(r["latency_ms"]) for r in perf_rows if r["query"] == query and r["method"] == method]
            vals.append(fmt(median(xs)))
        row = f"{short} & {' & '.join(vals)} \\\\"
        expected_query_rows.append(row)
        require_contains(tex, row, f"representative query row {short}")
    DETAILS["query_rows"] = expected_query_rows

    selectivity_order = [("0.025", "0.025"), ("0.050", "0.05"), ("0.100", "0.1"), ("0.250", "0.25"), ("0.500", "0.5")]
    expected_selectivity_rows: list[str] = []
    for display, raw in selectivity_order:
        vals = []
        for method in by_method:
            xs = [float(r["latency_ms"]) for r in perf_rows if r["selectivity"] == raw and r["method"] == method]
            vals.append(fmt(median(xs)))
        row = f"{display} & {' & '.join(vals)} \\\\"
        expected_selectivity_rows.append(row)
        require_contains(tex, row, f"selectivity row {display}")
    DETAILS["selectivity_rows"] = expected_selectivity_rows

    expected_complexity_rows: list[str] = []
    for level in ["1", "2", "3", "4", "5", "6"]:
        vals = []
        for method in by_method:
            xs = [float(r["latency_ms"]) for r in perf_rows if r["complexity"] == level and r["method"] == method]
            vals.append(fmt(median(xs)))
        row = f"{level} & {' & '.join(vals)} \\\\"
        expected_complexity_rows.append(row)
        require_contains(tex, row, f"complexity row {level}")
    DETAILS["complexity_rows"] = expected_complexity_rows

    corr = {r["method"]: r for r in corr_rows}
    unsafe = [r for r in corr_rows if r["method"] == "unsafe_late_aggregate"]
    unsafe_tests = len(unsafe)
    unsafe_equiv = sum(r["equivalent_to_reference"] == "1" for r in unsafe)
    unsafe_violations = sum(int(r["policy_violation_count"] or 0) for r in unsafe)
    unsafe_row = f"Unsafe aggregate & {unsafe_tests} & {unsafe_equiv} & {unsafe_violations:,} \\\\"
    require_contains(tex, unsafe_row, "unsafe aggregate correctness row")
    DETAILS["unsafe_aggregate_row"] = unsafe_row
    if metrics.get("negative_policy_violations") != unsafe_violations:
        fail("metrics negative_policy_violations disagrees with correctness.csv")

    stat = json.loads((RESULTS / "statistical_confidence_audit.json").read_text(encoding="utf-8"))
    job_paired = stat["joblike_pcqv_vs_view_barrier_ci"]["median"]
    generated_numbers = (ROOT / "paper" / "generated_numbers.tex").read_text(encoding="utf-8")
    job_macro = f"\\newcommand{{\\JobPairedSpeedView}}{{{job_paired:.3f}}}"
    if job_macro not in generated_numbers:
        fail("JobPairedSpeedView macro is stale")
    if "\\JobPairedSpeedView{}$\\times$" not in tex:
        fail("JOB paired speedup in manuscript should use the generated macro")

    rand = json.loads((RESULTS / "randomized_semantic_fuzz.json").read_text(encoding="utf-8"))
    opt = json.loads((RESULTS / "optimizer_search_audit.json").read_text(encoding="utf-8"))
    generated_numbers = (ROOT / "paper" / "generated_numbers.tex").read_text(encoding="utf-8")
    rand_macro_expectations = {
        "RandomFuzzTemplates": rand["random_templates"],
        "RandomFuzzRows": rand["rows"],
        "RandomFuzzSafeEq": rand["safe_equivalent"][0],
        "RandomFuzzSafeTotal": rand["safe_equivalent"][1],
        "RandomFuzzDiffs": rand["unsafe_differences"],
        "RandomFuzzViolations": rand["unsafe_violations"],
        "OptimizerSearchCases": opt["cases"],
    }
    for name, value in rand_macro_expectations.items():
        if f"\\newcommand{{\\{name}}}{{{value}}}" not in generated_numbers:
            fail(f"generated random/optimizer macro stale: {name}")
    for macro in ["RandomFuzzRows", "RandomFuzzTemplates", "RandomFuzzDiffs", "OptimizerSearchCases"]:
        if f"\\{macro}{{}}" not in tex:
            fail(f"manuscript should use generated macro: {macro}")

    generated_numbers = (ROOT / "paper" / "generated_numbers.tex").read_text(encoding="utf-8")
    macro_expectations = {
        "NumPerformanceRows": metrics["num_performance_rows"],
        "NumCorrectnessRows": metrics["num_correctness_rows"],
        "PcqvMedianMs": fmt(float(metrics["performance_median_ms"]["pcqv"])),
        "PcqvSpeedView": metrics["pcqv_speedup_vs_view_barrier_median"],
        "PcqvSpeedPost": metrics["pcqv_speedup_vs_post_filter_median"],
        "PcqvSpeedObliv": metrics["pcqv_speedup_vs_oblivious_median"],
        "PcqvPredRatio": metrics["pcqv_vs_predicate_injection_ratio_median"],
    }
    for name, value in macro_expectations.items():
        needle = f"\\newcommand{{\\{name}}}{{{value}}}"
        if needle not in generated_numbers:
            fail(f"generated_numbers.tex stale for {name}: expected {needle}")

    status = "PASS" if not PROBLEMS else "FAIL"
    out = {"status": status, "problems": PROBLEMS, "details": DETAILS}
    (RESULTS / "manuscript_consistency_audit.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps({"status": status, "problem_count": len(PROBLEMS)}, indent=2))
    if PROBLEMS:
        raise SystemExit("manuscript consistency audit failed")


if __name__ == "__main__":
    main()
