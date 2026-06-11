#!/usr/bin/env python3
"""Optimizer-search audit for PCQV visibility capsules.

This script is independent of SQLite. It stress-tests the algorithmic part of
PCQV: a visibility-capsule cost model over policy-filtered aliases and guarded
join edges. It compares exact left-deep search against greedy policy-aware
search on synthetic optimizer states with up to eight aliases. The goal is to
separate algorithmic evidence from DBMS execution noise and quantify when
a greedy capsule heuristic misses the exact protected-cardinality optimum.
"""
from __future__ import annotations
from dataclasses import dataclass
from itertools import permutations, combinations
from pathlib import Path
import argparse, csv, json, math, random, statistics
from typing import Dict, Iterable, List, Tuple

@dataclass(frozen=True)
class CapsuleAlias:
    name: str
    base_card: float
    policy_sel: float
    local_sel: float

@dataclass(frozen=True)
class CapsuleGraph:
    aliases: Tuple[CapsuleAlias, ...]
    join_sel: Dict[Tuple[str, str], float]
    release_penalty: float

    def alias(self, name: str) -> CapsuleAlias:
        return next(a for a in self.aliases if a.name == name)

    def edge_sel(self, a: str, b: str) -> float:
        return self.join_sel.get(tuple(sorted((a,b))), 1.0)

    def protected_card(self, name: str) -> float:
        a = self.alias(name)
        return max(1.0, a.base_card * a.policy_sel * a.local_sel)


def make_graph(rng: random.Random, n: int) -> CapsuleGraph:
    aliases=[]
    for i in range(n):
        base = rng.choice([800, 1200, 2500, 5000, 12000, 50000]) * rng.uniform(0.7, 1.3)
        policy = 10 ** rng.uniform(-2.2, -0.05)  # policies range from highly selective to broad
        local = 10 ** rng.uniform(-1.2, -0.02)
        aliases.append(CapsuleAlias(chr(ord('a')+i), base, policy, local))
    edges={}
    # connected backbone plus additional edges
    for i in range(1,n):
        j=rng.randrange(i)
        sel=10 ** rng.uniform(-4.0, -1.0)
        edges[tuple(sorted((aliases[i].name, aliases[j].name)))] = sel
    for i,j in combinations(range(n),2):
        key=tuple(sorted((aliases[i].name, aliases[j].name)))
        if key not in edges and rng.random()<0.28:
            edges[key]=10 ** rng.uniform(-4.0, -0.8)
    return CapsuleGraph(tuple(aliases), edges, rng.uniform(1.0,1.15))


def order_cost(g: CapsuleGraph, order: Tuple[str,...]) -> float:
    prefix=[]; interm=1.0; total=0.0
    for i,a in enumerate(order):
        card=g.protected_card(a)
        if i==0:
            interm=card
        else:
            sels=[g.edge_sel(a,b) for b in prefix if g.edge_sel(a,b)<1.0]
            if not sels:
                interm *= card * 50.0
            else:
                # Multiple guarded edges compound selectivity but never below one row.
                s=1.0
                for x in sels: s*=x
                interm=max(1.0, interm*card*s)
        prefix.append(a)
        total += interm + card
    return max(1.0, total*g.release_penalty)


def exhaustive(g: CapsuleGraph) -> Tuple[Tuple[str,...], float, int]:
    names=tuple(a.name for a in g.aliases)
    best=None; bc=float('inf'); count=0
    for p in permutations(names):
        count += 1
        c=order_cost(g,p)
        if c<bc:
            best=p; bc=c
    assert best is not None
    return best, bc, count


def prefix_extend(g: CapsuleGraph, prefix: Tuple[str,...], prefix_intermediate: float, prefix_cost: float, a: str) -> Tuple[float, float]:
    card = g.protected_card(a)
    if not prefix:
        interm = card
    else:
        sels = [g.edge_sel(a,b) for b in prefix if g.edge_sel(a,b) < 1.0]
        if not sels:
            interm = prefix_intermediate * card * 50.0
        else:
            s = 1.0
            for x in sels: s *= x
            interm = max(1.0, prefix_intermediate * card * s)
    return interm, prefix_cost + interm + card

def dp_left_deep(g: CapsuleGraph) -> Tuple[Tuple[str,...], float, int]:
    # Exact Selinger-style DP for the left-deep cost recurrence used above.
    # Each subset keeps the cheapest prefix and its output cardinality.
    names=tuple(a.name for a in g.aliases)
    best: Dict[frozenset, Tuple[Tuple[str,...], float, float]] = {}  # subset -> (order, cumulative_cost_without_release, intermediate_card)
    states=0
    for a in names:
        interm, cost = prefix_extend(g, tuple(), 1.0, 0.0, a)
        best[frozenset([a])] = ((a,), cost, interm)
        states += 1
    for size in range(2,len(names)+1):
        for subset in combinations(names,size):
            S=frozenset(subset); best_order=(); best_cost=float('inf'); best_inter=1.0
            for a in subset:
                prev=S-{a}
                if prev not in best: continue
                prev_order, prev_cost, prev_inter = best[prev]
                inter, cost = prefix_extend(g, prev_order, prev_inter, prev_cost, a)
                states += 1
                if cost < best_cost:
                    best_order=prev_order+(a,); best_cost=cost; best_inter=inter
            best[S]=(best_order,best_cost,best_inter)
    allS=frozenset(names)
    o,c,_=best[allS]
    return o, max(1.0, c*g.release_penalty), states


def greedy(g: CapsuleGraph) -> Tuple[Tuple[str,...], float]:
    rem=[a.name for a in g.aliases]; order=[]
    while rem:
        a=min(rem, key=lambda x: order_cost(g, tuple(order+[x])))
        order.append(a); rem.remove(a)
    return tuple(order), order_cost(g, tuple(order))


def run(out_dir: Path, cases: int, seed: int) -> dict:
    rng=random.Random(seed)
    rows=[]
    for n in [4,5,6,7,8]:
        for k in range(cases):
            g=make_graph(rng,n)
            ex_o, ex_c, ex_states = exhaustive(g)
            gr_o, gr_c = greedy(g)
            rows.append({
                'aliases': n, 'case': k, 'exact_cost': ex_c, 'greedy_cost': gr_c,
                'exact_orders': ex_states, 'greedy_regret': gr_c/ex_c if ex_c else 1.0,
                'exact_order': ''.join(ex_o), 'greedy_order': ''.join(gr_o),
            })
    out_dir.mkdir(parents=True, exist_ok=True)
    with open(out_dir/'optimizer_search_audit.csv','w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0].keys())); w.writeheader(); w.writerows(rows)
    vals=sorted(float(r['greedy_regret']) for r in rows)
    summary={
        'cases': len(rows),
        'exact_cases': len(rows),
        'max_aliases': 8,
        'max_exact_orders': math.factorial(8),
        'median_greedy_regret': round(statistics.median(vals), 4),
        'p95_greedy_regret': round(vals[int(0.95*(len(vals)-1))], 4),
        'max_greedy_regret': round(max(vals), 4),
        'num_cases_where_greedy_suboptimal': sum(1 for v in vals if v > 1.0000001),
        'seed': seed,
    }
    with open(out_dir/'optimizer_search_audit.json','w') as f: json.dump(summary,f,indent=2)
    return summary

if __name__=='__main__':
    ap=argparse.ArgumentParser(); ap.add_argument('--out',default='results'); ap.add_argument('--cases',type=int,default=30); ap.add_argument('--seed',type=int,default=20270605)
    a=ap.parse_args(); print(json.dumps(run(Path(a.out),a.cases,a.seed),indent=2))
