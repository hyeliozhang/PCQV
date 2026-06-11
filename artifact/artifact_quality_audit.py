#!/usr/bin/env python3
"""Static artifact quality audit for the PCQV package."""
from __future__ import annotations
from pathlib import Path
import ast, json, re, sys
ROOT=Path(__file__).resolve().parents[1]
required=[
 'paper/main.tex','paper/references.bib','artifact/pcqv/engine.py','artifact/run_all_repro.py',
 'artifact/run_standard_benchmarks.py','artifact/exhaustive_semantics_checker.py','artifact/sql_feature_semantics_checker.py',
 'artifact/randomized_semantic_fuzzer.py','artifact/optimizer_search_audit.py','artifact/static_submission_audit.py',
 'artifact/verify_final_claims.py','EVIDENCE.md','STATUS.md','FORMAT_CHECK.md','SUPPLEMENTAL_SUBMISSION.md'
]
leak_patterns=[r'artifact/',r'results/',r'\.py\b',r'\.csv\b',r'\.json\b',r'README',r'EVIDENCE',r'STATUS']
format_hacks=[r'\\vspace\s*\{\s*-',r'\\hspace\s*\{\s*-',r'\\fontsize',r'\\setlength\s*\{\\textfloatsep',r'\\addtolength',r'\\enlargethispage',r'\\resizebox']

def py_metrics(path: Path):
    txt=path.read_text(errors='ignore')
    tree=ast.parse(txt)
    funcs=sum(isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) for n in ast.walk(tree))
    classes=sum(isinstance(n,ast.ClassDef) for n in ast.walk(tree))
    todos=len(re.findall('T'+'ODO|FIX'+'ME|X'+'XX',txt))
    bare_pass=sum(isinstance(n,ast.Pass) for n in ast.walk(tree))
    return {'file':str(path.relative_to(ROOT)),'lines':txt.count('\n')+1,'functions':funcs,'classes':classes,'todos':todos,'pass_nodes':bare_pass}

def main():
    missing=[p for p in required if not (ROOT/p).exists()]
    py_files=list((ROOT/'artifact').rglob('*.py'))
    syntax=[]
    for p in py_files:
        syntax.append(py_metrics(p))
    tex=(ROOT/'paper'/'main.tex').read_text(errors='ignore')
    body=re.split(r'\\section\*\{(?:AI-Generated Content Acknowledgement|Guidelines for Artificial Intelligence \(AI\)-Generated Content)\}', tex, maxsplit=1)[0]
    leaks={pat: len(re.findall(pat, body)) for pat in leak_patterns}
    hacks={pat: len(re.findall(pat, tex)) for pat in format_hacks}
    refs=(ROOT/'paper'/'references.bib').read_text(errors='ignore') if (ROOT/'paper'/'references.bib').exists() else ''
    ref_count=len(re.findall(r'@\w+\s*\{', refs))
    result={'missing_required_files':missing,'python_files':len(py_files),'python_metrics':syntax,
            'body_implementation_leaks':leaks,'format_hacks':hacks,'bib_entries':ref_count,
            'status':'PASS' if not missing and all(v==0 for v in leaks.values()) and all(v==0 for v in hacks.values()) and 75<=ref_count<=100 and all(m['todos']==0 and m['pass_nodes']==0 for m in syntax) else 'FAIL'}
    out=ROOT/'results'/'artifact_quality_audit.json'; out.parent.mkdir(exist_ok=True); out.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
    if result['status']!='PASS': sys.exit(1)
if __name__=='__main__': main()
