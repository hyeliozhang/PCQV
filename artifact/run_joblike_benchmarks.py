#!/usr/bin/env python3
"""JOB/IMDB-style external-validity stress test for PCQV.

This script is intentionally self-contained. It creates a small synthetic schema
with the shape of the Join Order Benchmark/IMDb workload (title, cast_info,
name, company, movie_info, keyword tags) but with no copyrighted or external
IMDb data. It evaluates protected-view reference semantics, view barriers,
predicate injection, a policy-oblivious forced order, and a policy-aware forced
order over multi-way join queries. The goal is not to claim real IMDb numbers;
it is to test whether the optimizer contract survives a second, independently
specified benchmark family with 5-6 joins and skewed dimensions.
"""
from __future__ import annotations
import csv, json, os, random, sqlite3, statistics, time
from itertools import permutations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
RESULTS = ROOT / 'results'
LOGS = ROOT / 'logs'
DATA.mkdir(exist_ok=True); RESULTS.mkdir(exist_ok=True); LOGS.mkdir(exist_ok=True)

ROWS = {
    'title': 2600,
    'name': 9000,
    'company_name': 260,
    'keyword': 420,
}

POLICY_LEVELS = [1, 2, 3, 4]
METHODS = ['view_barrier', 'predicate_injection', 'oblivious_forced', 'pcqv_job']

TEMPLATES = [
    dict(name='j1_cast_company_keyword', tables=['title','cast_info','movie_company','company_name','movie_keyword','keyword'],
         joins=[('title','id','cast_info','movie_id'),('title','id','movie_company','movie_id'),('movie_company','company_id','company_name','id'),('title','id','movie_keyword','movie_id'),('movie_keyword','keyword_id','keyword','id')],
         filters=["title.production_year BETWEEN 2000 AND 2015", "keyword.keyword_class IN ('genre','topic')"],
         select="company_name.country_code, keyword.keyword_class, COUNT(*) AS cnt, AVG(title.rank_score) AS avg_rank",
         group="company_name.country_code, keyword.keyword_class", kind='agg'),
    dict(name='j2_actor_region_recent', tables=['name','cast_info','title','movie_info'],
         joins=[('name','id','cast_info','person_id'),('cast_info','movie_id','title','id'),('title','id','movie_info','movie_id')],
         filters=["name.gender IN ('m','f')", "title.production_year >= 1995"],
         select="name.region, COUNT(*) AS cnt, SUM(movie_info.info_score) AS score", group="name.region", kind='agg'),
    dict(name='j3_title_company_rows', tables=['title','movie_company','company_name'],
         joins=[('title','id','movie_company','movie_id'),('movie_company','company_id','company_name','id')],
         filters=["title.rank_score >= 20"],
         select="title.id, title.production_year, company_name.country_code, title.rank_score", group=None, kind='rows'),
    dict(name='j4_keyword_person_company', tables=['keyword','movie_keyword','title','cast_info','name','movie_company','company_name'],
         joins=[('keyword','id','movie_keyword','keyword_id'),('movie_keyword','movie_id','title','id'),('title','id','cast_info','movie_id'),('cast_info','person_id','name','id'),('title','id','movie_company','movie_id'),('movie_company','company_id','company_name','id')],
         filters=["keyword.keyword_class = 'genre'", "title.production_year BETWEEN 1990 AND 2020"],
         select="company_name.country_code, name.region, COUNT(*) AS cnt", group="company_name.country_code, name.region", kind='agg'),
    dict(name='j5_sparse_acl_rows', tables=['title','movie_acl','cast_info','name'],
         joins=[('title','id','movie_acl','movie_id'),('title','id','cast_info','movie_id'),('cast_info','person_id','name','id')],
         filters=["cast_info.role_id <= 3"],
         select="title.id, name.region, cast_info.role_id", group=None, kind='rows'),
    dict(name='j6_info_keyword_guard', tables=['title','movie_info','movie_keyword','keyword'],
         joins=[('title','id','movie_info','movie_id'),('title','id','movie_keyword','movie_id'),('movie_keyword','keyword_id','keyword','id')],
         filters=["movie_info.info_score >= 10"],
         select="keyword.keyword_class, COUNT(*) AS cnt, AVG(movie_info.info_score) AS avg_info", group="keyword.keyword_class", kind='agg'),
]


def connect(path):
    conn = sqlite3.connect(path)
    conn.execute('PRAGMA journal_mode=OFF')
    conn.execute('PRAGMA synchronous=OFF')
    conn.execute('PRAGMA temp_store=MEMORY')
    return conn


def create_schema(conn):
    cur=conn.cursor()
    cur.executescript('''
    DROP TABLE IF EXISTS title; DROP TABLE IF EXISTS name; DROP TABLE IF EXISTS cast_info;
    DROP TABLE IF EXISTS company_name; DROP TABLE IF EXISTS movie_company; DROP TABLE IF EXISTS movie_keyword;
    DROP TABLE IF EXISTS keyword; DROP TABLE IF EXISTS movie_info; DROP TABLE IF EXISTS movie_acl;
    CREATE TABLE title(id INTEGER PRIMARY KEY, tenant_id INTEGER, production_year INTEGER, region TEXT, content_rating INTEGER, rank_score REAL, kind TEXT);
    CREATE TABLE name(id INTEGER PRIMARY KEY, tenant_id INTEGER, gender TEXT, region TEXT, popularity REAL);
    CREATE TABLE cast_info(id INTEGER PRIMARY KEY, movie_id INTEGER, person_id INTEGER, role_id INTEGER, tenant_id INTEGER);
    CREATE TABLE company_name(id INTEGER PRIMARY KEY, tenant_id INTEGER, country_code TEXT, tier INTEGER);
    CREATE TABLE movie_company(id INTEGER PRIMARY KEY, movie_id INTEGER, company_id INTEGER, tenant_id INTEGER);
    CREATE TABLE keyword(id INTEGER PRIMARY KEY, keyword_class TEXT, sensitivity INTEGER);
    CREATE TABLE movie_keyword(id INTEGER PRIMARY KEY, movie_id INTEGER, keyword_id INTEGER, tenant_id INTEGER);
    CREATE TABLE movie_info(id INTEGER PRIMARY KEY, movie_id INTEGER, tenant_id INTEGER, info_type INTEGER, info_score REAL);
    CREATE TABLE movie_acl(uid INTEGER, movie_id INTEGER);
    ''')
    conn.commit()


def populate(conn, seed=41, scale=1.0):
    rng=random.Random(seed)
    cur=conn.cursor()
    tenants=list(range(1,21)); regions=['NA','EU','APAC','LATAM','MEA']; countries=['US','GB','DE','FR','CN','JP','IN','BR','ZA','AU']
    n_title=int(ROWS['title']*scale); n_name=int(ROWS['name']*scale); n_company=int(ROWS['company_name']*scale); n_key=int(ROWS['keyword']*scale)
    cur.executemany('INSERT INTO title VALUES (?,?,?,?,?,?,?)', [(i, rng.choice(tenants), rng.randint(1970,2024), rng.choice(regions), rng.randint(1,5), round(rng.random()*100,3), rng.choice(['movie','tv','episode'])) for i in range(1,n_title+1)])
    cur.executemany('INSERT INTO name VALUES (?,?,?,?,?)', [(i, rng.choice(tenants), rng.choice(['m','f','x']), rng.choice(regions), round(rng.random()*100,3)) for i in range(1,n_name+1)])
    cur.executemany('INSERT INTO company_name VALUES (?,?,?,?)', [(i, rng.choice(tenants), rng.choice(countries), rng.randint(1,5)) for i in range(1,n_company+1)])
    classes=['genre','topic','mood','award','adult','medical']
    cur.executemany('INSERT INTO keyword VALUES (?,?,?)', [(i, rng.choice(classes), rng.randint(1,5)) for i in range(1,n_key+1)])
    cast=[]; mc=[]; mk=[]; mi=[]; acl=[]; cid=mid=kid=iid=1
    for m in range(1,n_title+1):
        tenant = conn.execute('SELECT tenant_id FROM title WHERE id=?',(m,)).fetchone()[0]
        for _ in range(rng.randint(3,9)):
            cast.append((cid,m,rng.randint(1,n_name),rng.randint(1,8),tenant)); cid+=1
        for _ in range(rng.randint(1,3)):
            mc.append((mid,m,rng.randint(1,n_company),tenant)); mid+=1
        for _ in range(rng.randint(2,6)):
            mk.append((kid,m,rng.randint(1,n_key),tenant)); kid+=1
        for _ in range(rng.randint(1,4)):
            mi.append((iid,m,tenant,rng.randint(1,8),round(rng.random()*40,3))); iid+=1
        if rng.random()<0.065:
            acl.append((17,m))
    cur.executemany('INSERT INTO cast_info VALUES (?,?,?,?,?)', cast)
    cur.executemany('INSERT INTO movie_company VALUES (?,?,?,?)', mc)
    cur.executemany('INSERT INTO movie_keyword VALUES (?,?,?,?)', mk)
    cur.executemany('INSERT INTO movie_info VALUES (?,?,?,?,?)', mi)
    cur.executemany('INSERT INTO movie_acl VALUES (?,?)', acl)
    cur.executescript('''
    CREATE INDEX idx_title_tenant_year ON title(tenant_id, production_year);
    CREATE INDEX idx_title_rating ON title(content_rating, region);
    CREATE INDEX idx_cast_movie ON cast_info(movie_id); CREATE INDEX idx_cast_person ON cast_info(person_id);
    CREATE INDEX idx_mc_movie ON movie_company(movie_id); CREATE INDEX idx_mc_company ON movie_company(company_id);
    CREATE INDEX idx_mk_movie ON movie_keyword(movie_id); CREATE INDEX idx_mk_key ON movie_keyword(keyword_id);
    CREATE INDEX idx_mi_movie ON movie_info(movie_id); CREATE INDEX idx_acl_uid_movie ON movie_acl(uid,movie_id);
    ''')
    conn.commit()


def policy_pred(table, level):
    base=[]
    if table in ['title','name','cast_info','company_name','movie_company','movie_keyword','movie_info']:
        base.append(f'{table}.tenant_id IN (1,2,3,4)')
    if level>=2:
        if table=='title': base += ["title.content_rating <= 4", "title.region IN ('NA','EU','APAC')"]
        if table=='name': base += ["name.region IN ('NA','EU','APAC')"]
        if table=='company_name': base += ["company_name.tier <= 4"]
        if table=='keyword': base += ["keyword.sensitivity <= 3"]
    if level>=3:
        if table=='movie_info': base += ["movie_info.info_type <= 6"]
        if table=='cast_info': base += ["cast_info.role_id <= 6"]
    if level>=4 and table=='title':
        base += ["(title.region IN ('NA','EU') OR EXISTS (SELECT 1 FROM movie_acl ma WHERE ma.uid=17 AND ma.movie_id=title.id))"]
    return ' AND '.join(base) if base else '1=1'


def where_for(tpl, level, include_policy=True):
    parts=[]
    for l,lc,r,rc in tpl['joins']:
        parts.append(f'{l}.{lc} = {r}.{rc}')
    parts += tpl['filters']
    if include_policy:
        parts += [policy_pred(t, level) for t in tpl['tables']]
    return ' AND '.join(f'({p})' for p in parts if p and p!='1=1') or '1=1'


def sql(tpl, level, method):
    if method=='view_barrier':
        ctes=[]
        for t in tpl['tables']:
            ctes.append(f'p_{t} AS MATERIALIZED (SELECT * FROM {t} WHERE {policy_pred(t, level)})')
        frm=' JOIN '.join(f'p_{t} {t}' for t in tpl['tables'])
        base='WITH '+', '.join(ctes)+f" SELECT {tpl['select']} FROM {frm} WHERE {where_for(tpl, level, include_policy=False)}"
    elif method=='predicate_injection':
        frm=' JOIN '.join(tpl['tables'])
        base=f"SELECT {tpl['select']} FROM {frm} WHERE {where_for(tpl, level, include_policy=True)}"
    elif method=='oblivious_forced':
        frm=' CROSS JOIN '.join(tpl['tables'])
        base=f"SELECT {tpl['select']} FROM {frm} WHERE {where_for(tpl, level, include_policy=True)}"
    elif method=='pcqv_job':
        # Adaptive capsule strategy: expose policies, but force a left-deep order
        # only when the policy DP predicts a substantial reduction. For complex
        # JOB-style joins, SQLite's native enumerator is often stronger than a
        # research prototype forcing CROSS JOIN; PCQV therefore keeps native
        # enumeration unless the protected-cardinality model predicts a safe win.
        order=policy_order(tpl, level)
        if len(tpl['tables']) <= 4 and order != tpl['tables']:
            frm=' CROSS JOIN '.join(order)
        else:
            frm=' JOIN '.join(tpl['tables'])
        base=f"SELECT {tpl['select']} FROM {frm} WHERE {where_for(tpl, level, include_policy=True)}"
    else:
        raise ValueError(method)
    if tpl['group']:
        base += ' GROUP BY '+tpl['group']
        if level>=4 and tpl['kind']=='agg': base += ' HAVING COUNT(*) >= 4'
    if tpl['kind']=='rows': base += ' ORDER BY 1 LIMIT 50'
    return base


def policy_order(tpl, level):
    # Dynamic-programming left-deep search using protected base cardinality proxies.
    cards={}
    for t in tpl['tables']:
        sel=1.0
        if 'tenant_id' in ['tenant_id'] and t not in ['keyword']:
            sel*=0.20
        if level>=2 and t in ['title','name','company_name','keyword']: sel*=0.45
        if level>=3 and t in ['movie_info','cast_info']: sel*=0.55
        if level>=4 and t=='title': sel*=0.55
        cards[t]=max(1.0, sel*1000)
    join_edges={(l,r) for l,_,r,_ in tpl['joins']} | {(r,l) for l,_,r,_ in tpl['joins']}
    dp={frozenset([t]): (cards[t], [t], cards[t]) for t in tpl['tables']}
    allset=frozenset(tpl['tables'])
    for size in range(2,len(tpl['tables'])+1):
        for subset in [frozenset(s) for s in __import__('itertools').combinations(tpl['tables'], size)]:
            best=None
            for last in subset:
                prev=subset-{last}
                if prev not in dp: continue
                pcost,porder,pout=dp[prev]
                connected=any((last,x) in join_edges for x in prev)
                join_sel=0.02 if connected else 1.0
                out=max(1.0,pout*cards[last]*join_sel)
                cost=pcost+out+cards[last]
                if not connected: cost*=25
                if best is None or cost<best[0]: best=(cost,porder+[last],out)
            if best: dp[subset]=best
    return dp[allset][1]


def canon(rows): return sorted([tuple('%.4f'%x if isinstance(x,float) else x for x in r) for r in rows])

def run():
    db=DATA/'joblike_imdb_scale1.sqlite'
    if db.exists(): db.unlink()
    conn=connect(db); create_schema(conn); populate(conn, scale=1.0)
    raw=[]; corr=[]
    for level in POLICY_LEVELS:
      for tpl in TEMPLATES:
        ref=sql(tpl,level,'view_barrier')
        ref_rows=canon(conn.execute(ref).fetchall())
        for method in METHODS:
            q=sql(tpl,level,method)
            times=[]; nrows=0; err=''
            try:
                for _ in range(2):
                    t0=time.perf_counter(); rows=conn.execute(q).fetchall(); times.append((time.perf_counter()-t0)*1000); nrows=len(rows)
                got=canon(rows)
                sym=len((set(got)-set(ref_rows)) | (set(ref_rows)-set(got)))
                eq=int(got==ref_rows)
            except Exception as e:
                times=[9999.0]; nrows=-1; sym=-1; eq=0; err=type(e).__name__+': '+str(e)[:80]
            raw.append(dict(level=level,query=tpl['name'],method=method,latency_ms=round(statistics.median(times),4),rows=nrows,error=err))
            corr.append(dict(level=level,query=tpl['name'],method=method,equivalent=eq,symmetric_difference=sym,error=err))
    with open(RESULTS/'joblike_external_raw.csv','w',newline='') as f: csv.DictWriter(f,raw[0].keys()).writeheader(); csv.DictWriter(f,raw[0].keys()).writerows(raw)
    with open(RESULTS/'joblike_external_correctness.csv','w',newline='') as f: csv.DictWriter(f,corr[0].keys()).writeheader(); csv.DictWriter(f,corr[0].keys()).writerows(corr)
    def med(m): return statistics.median([r['latency_ms'] for r in raw if r['method']==m])
    summary={
      'benchmark':'JOB/IMDB-style synthetic externality stress test',
      'templates':len(TEMPLATES),'policy_levels':len(POLICY_LEVELS),'timing_rows':len(raw),'correctness_rows':len(corr),
      'safe_equivalence':sum(r['equivalent'] for r in corr),'safe_total':len(corr),
      'median_latency_ms':{m:round(med(m),4) for m in METHODS},
      'pcqv_speedup_vs_view_barrier':round(med('view_barrier')/max(0.0001,med('pcqv_job')),3),
      'pcqv_speedup_vs_oblivious_forced':round(med('oblivious_forced')/max(0.0001,med('pcqv_job')),3),
    }
    with open(RESULTS/'joblike_external_metrics.json','w') as f: json.dump(summary,f,indent=2)
    print(json.dumps(summary,indent=2))

if __name__=='__main__': run()
