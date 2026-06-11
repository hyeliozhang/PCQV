# ICDE Scope Guard

PCQV is framed as a data-engineering paper, not a pure software-security or access-control implementation. The core research object is the interaction between protected query semantics and optimizer transformations.

## Data engineering challenge

Policies such as row guards, masks, purpose restrictions, ACL exceptions, cross-table guards, and aggregation release guards change which relational transformations are semantics-preserving and which plans are cost-effective. Classical optimizers reason about algebraic equivalence and cardinality in a policy-oblivious space; governance layers often add policies outside the optimizer. PCQV studies how protected semantics should be represented as optimizer-visible state.

## Contributions by section

- Semantics: protected-view denotation, visibility capsules, mask timing, group-release guards, and equivalence under protected bags.
- Optimization: rule admissibility, policy-aware selectivity, exact left-deep candidate reasoning, and comparison against strong predicate injection.
- Verification: protected-view metamorphic oracle, finite-model obligations, SQL-feature obligations, randomized fuzzing, and negative-control witnesses.
- Evaluation: controlled benchmark, standard-schema stress test, planning/cardinality analysis, mutation suite, and reproducibility checks.

## 1000-character ICDE scope explanation

This paper addresses query processing and optimization under policy-constrained semantics. The data-engineering challenge is that row filters, masks, tenant and purpose restrictions, ACL exceptions, cross-table guards, and aggregate release guards are usually enforced outside the optimizer, while rewrite and cost decisions are made in a policy-oblivious space. PCQV treats these policies as optimizer-visible relational properties: a policy visibility capsule records alias-local guards, cross-alias dependencies, mask timing, group-release guards, and protected selectivity. The paper contributes protected-view semantics, admissible protected rewrite conditions, a policy-aware candidate planner, metamorphic rewrite verification, and a reproducible benchmark with standard-schema stress tests, finite-model semantic audits, SQL-feature audits, randomized fuzzing, and negative controls. The work builds on ICDE/SIGMOD/VLDB/PVLDB research in query optimization, view rewriting, cardinality estimation, secure/policy-aware data management, and database testing.
