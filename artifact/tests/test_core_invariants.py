#!/usr/bin/env python3
"""Unit-level invariant tests for the PCQV artifact.

These tests are intentionally small and deterministic.  They complement the
large CSV benchmarks by checking the core contract directly: safe encodings
match the protected-view reference, unsafe mutations are observable on at least
some cases, policy-aware order enumeration is deterministic, and compiled SQL
contains the policy features the paper claims are optimizer-visible.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
import sys
sys.path.insert(0, str(ROOT / "artifact"))

from pcqv.engine import (  # noqa: E402
    Context,
    PolicyManager,
    SQLCompiler,
    Stats,
    execute_rows,
    prepare_database,
    query_templates,
)

def norm(rows):
    return sorted([tuple("<NULL>" if x is None else x for x in row) for row in rows], key=repr)


class PCQVCoreInvariantTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "unit.sqlite")
        prepare_database(cls.db_path, seed=314159, scale=0.01)
        cls.conn = sqlite3.connect(cls.db_path)
        cls.compiler = SQLCompiler(Stats(cls.conn), cls.conn)
        cls.templates = query_templates()
        cls.ctx = Context(allowed_tenants=(1, 2), allowed_regions=("NA", "EU"), min_group=4)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_policy_compiler_exposes_visibility_features(self):
        policy = PolicyManager(6, self.ctx)
        customer_policy = policy.policy_where("customers", "c")
        order_policy = policy.policy_where("orders", "o")
        product_policy = policy.policy_where("products", "p")
        self.assertIn("tid", customer_policy)
        self.assertTrue("region" in customer_policy or "acl" in customer_policy.lower())
        self.assertIn("purpose_tag", order_policy)
        self.assertIn("sensitivity", product_policy)

    def test_safe_methods_match_reference_on_representative_templates(self):
        safe_methods = ["predicate_injection", "view_barrier", "post_join_filter", "pcqv", "pcqv_forced", "pcqv_no_stats"]
        for q in self.templates[:8]:
            policy = PolicyManager(6, self.ctx)
            reference = norm(execute_rows(self.conn, self.compiler.compile(q, policy, "view_barrier")))
            for method in safe_methods:
                with self.subTest(query=q.name, method=method):
                    got = norm(execute_rows(self.conn, self.compiler.compile(q, policy, method)))
                    self.assertEqual(reference, got)

    def test_unsafe_mutations_are_not_vacuous(self):
        unsafe_methods = ["unsafe_late_aggregate", "mut_drop_tenant", "mut_no_mask", "mut_drop_acl", "mut_no_group_guard"]
        observed = 0
        total = 0
        for q in self.templates:
            policy = PolicyManager(6, self.ctx)
            reference = norm(execute_rows(self.conn, self.compiler.compile(q, policy, "view_barrier")))
            for method in unsafe_methods:
                total += 1
                try:
                    got = norm(execute_rows(self.conn, self.compiler.compile(q, policy, method)))
                except Exception:
                    # A mutation that generates invalid SQL is still a diagnostic failure.
                    observed += 1
                    continue
                if got != reference:
                    observed += 1
        self.assertGreaterEqual(observed, max(8, total // 5))

    def test_policy_aware_order_is_deterministic_and_valid(self):
        policy = PolicyManager(6, self.ctx)
        for q in self.templates:
            aliases = {t.alias for t in q.tables}
            first = [t.alias for t in self.compiler.policy_aware_order(q, policy)]
            second = [t.alias for t in self.compiler.policy_aware_order(q, policy)]
            self.assertEqual(first, second)
            self.assertEqual(set(first), aliases)
            self.assertEqual(len(first), len(aliases))

    def test_pcqv_choice_is_safe_candidate(self):
        policy = PolicyManager(6, self.ctx)
        for q in self.templates:
            choice = self.compiler.choose_pcqv_mode(q, policy)
            self.assertIn(choice["mode"], {"native", "forced"})
            self.assertEqual(set(choice["template_order"]), {t.alias for t in q.tables})
            self.assertEqual(set(choice["policy_aware_order"]), {t.alias for t in q.tables})


if __name__ == "__main__":
    unittest.main(verbosity=2)
