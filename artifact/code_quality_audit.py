#!/usr/bin/env python3
"""Static code-quality audit for the PCQV artifact.

This is intentionally stdlib-only. It compiles every Python file, checks for
accidental generated-cache leakage, and reports coarse maintainability metrics
that are useful to artifact reviewers.
"""
from __future__ import annotations
import ast, json, re, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

# Normalize caches created by local py_compile or test runs; the distribution
# itself must remain cache-free.
for cache in (ROOT/'artifact').rglob('__pycache__'):
    shutil.rmtree(cache, ignore_errors=True)
for pyc in (ROOT/'artifact').rglob('*.pyc'):
    try:
        pyc.unlink()
    except FileNotFoundError:
        continue

def complexity(node: ast.AST) -> int:
    score=1
    for n in ast.walk(node):
        if isinstance(n,(ast.If, ast.For, ast.While, ast.Try, ast.BoolOp, ast.IfExp, ast.ExceptHandler, ast.comprehension, ast.Match)):
            score += 1
    return score

def main() -> None:
    py_files=[p for p in (ROOT/'artifact').rglob('*.py') if '__pycache__' not in str(p)]
    records=[]; failures=[]
    for p in py_files:
        txt=p.read_text(errors='replace')
        try:
            tree=ast.parse(txt, filename=str(p))
            compile(tree, str(p), 'exec')
            funcs=[n for n in ast.walk(tree) if isinstance(n,(ast.FunctionDef, ast.AsyncFunctionDef))]
            max_len=0; max_cplx=0
            for f in funcs:
                end=getattr(f,'end_lineno',f.lineno)
                max_len=max(max_len,end-f.lineno+1)
                max_cplx=max(max_cplx,complexity(f))
            records.append({'file':str(p.relative_to(ROOT)),'lines':len(txt.splitlines()),'functions':len(funcs),'max_function_lines':max_len,'max_function_complexity':max_cplx,'status':'PASS'})
        except Exception as e:
            rec={'file':str(p.relative_to(ROOT)),'lines':len(txt.splitlines()),'functions':0,'max_function_lines':0,'max_function_complexity':0,'status':'FAIL','error':str(e)[:200]}
            records.append(rec); failures.append(rec)
    cache=list((ROOT/'artifact').rglob('__pycache__'))+list((ROOT/'artifact').rglob('*.pyc'))
    status='PASS' if not failures and not cache else 'FAIL'
    obj={'status':status,'python_files':len(py_files),'total_lines':sum(r['lines'] for r in records),'max_file_lines':max(r['lines'] for r in records), 'max_function_lines':max(r['max_function_lines'] for r in records), 'max_function_complexity':max(r['max_function_complexity'] for r in records), 'compile_failures':len(failures), 'cache_artifacts':len(cache), 'records':records}
    out=ROOT/'results'/'code_quality_audit.json'; out.parent.mkdir(exist_ok=True); json.dump(obj,open(out,'w'),indent=2)
    nums=ROOT/'paper'/'generated_numbers.tex'
    if nums.exists():
        txt=nums.read_text(encoding='utf-8')
        for name, value in {'CodeAuditPyFiles': len(py_files), 'CodeAuditTotalLines': sum(r['lines'] for r in records)}.items():
            cmd=f"\\newcommand{{\\{name}}}{{{value}}}"
            if re.search(rf"\\newcommand{{\\{name}}}{{[^}}]*}}", txt):
                txt=re.sub(rf"\\newcommand{{\\{name}}}{{[^}}]*}}", lambda _m, cmd=cmd: cmd, txt)
            else:
                txt += "\n" + cmd + "\n"
        nums.write_text(txt, encoding='utf-8')
    print(json.dumps({k:v for k,v in obj.items() if k!='records'},indent=2))
    if status!='PASS': raise SystemExit('code quality audit failed')
if __name__=='__main__': main()
