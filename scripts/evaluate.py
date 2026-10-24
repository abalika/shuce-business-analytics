"""Run isolated acceptance checks; keep logs and return nonzero on any failure."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def evaluate(output):
    output.mkdir(parents=True,exist_ok=False)
    started = time.perf_counter()
    result = {'ok':False,'version':'2.5.0','started_at':datetime.now().isoformat(),'checks':[],'real_model_requests':0}
    def save():
        (output/'evaluation.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    def run(name, command):
        log = output/(name+'.log')
        with log.open('w',encoding='utf-8') as stream:
            completed = subprocess.run(command,cwd=ROOT,env={**os.environ,'PYTHONIOENCODING':'utf-8'},stdout=stream,stderr=subprocess.STDOUT,timeout=300)
        result['checks'].append({'name':name,'passed':completed.returncode==0,'exit_code':completed.returncode,'log':log.name,'sha256':hashlib.sha256(log.read_bytes()).hexdigest()})
        save()
        if completed.returncode: raise ValueError('Check failed: '+name)
    try:
        run('pytest',[sys.executable,'-m','pytest','tests','-q','--junitxml='+str(output/'pytest.xml')])
        node = shutil.which('node')
        if not node: raise ValueError('Node.js required for JavaScript acceptance')
        run('javascript-tests',[node,'--test','tests/review-drafts.test.cjs','tests/api-feedback.test.cjs'])
        for name in ('app','ops'):
            run('javascript-syntax-'+name,[node,'--check','web/'+name+'.js'])
        run('offline',[sys.executable,str(ROOT/'scripts/offline_eval.py'),'--out',str(output/'offline-demo')])
        run('benchmark-first',[sys.executable,str(ROOT/'scripts/benchmark.py'),'--out',str(output/'benchmark-first.json')])
        run('benchmark-repeat',[sys.executable,str(ROOT/'scripts/benchmark.py'),'--out',str(output/'benchmark-repeat.json'),'--previous',str(output/'benchmark-first.json')])
        result['benchmark'] = json.loads((output/'benchmark-repeat.json').read_text(encoding='utf-8'))
        evidence = json.loads((output/'offline-demo/evaluation.json').read_text(encoding='utf-8'))
        data = Path(evidence['work_data'])
        from app.maintenance import audit,make_backup,verify_backup,restore_backup
        before = audit(data)
        archive = ROOT/'work'/('evaluation-backup-'+output.name+'-'+datetime.now().strftime('%Y%m%d%H%M%S%f')+'.zip')
        backup = make_backup(data,archive)
        verified = verify_backup(archive)
        restored_path = archive.with_suffix('')
        restored = restore_backup(archive,restored_path)
        after = audit(restored_path)
        if before['counts'] != after['counts']: raise ValueError('Restore counts mismatch')
        (output/'backup-restore.json').write_text(json.dumps({'before':before,'backup':backup,'verify':verified,'restore':restored,'after':after},ensure_ascii=False,indent=2),encoding='utf-8')
        result['checks'].append({'name':'backup-verify-restore-recheck','passed':True,'counts':after['counts']})
        result.update(ok=True,finished_at=datetime.now().isoformat(),elapsed_ms=round((time.perf_counter()-started)*1000),browser_inspected=False,offline_controls={'actual_cents':evidence['actual_cents'],'target_cents':evidence['target_cents'],'raw_sql_categories_checked':evidence['raw_sql_categories_checked'],'provider_network_attempts':evidence['provider_network_attempts']})
        save()
        return result
    except Exception as exc:
        result['failure'] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        save()
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,help='New evidence directory; never overwritten')
    args = parser.parse_args()
    try:
        report = evaluate(args.out.resolve())
        print(json.dumps(report,ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok':False,'detail':'Acceptance failed; inspect the chosen output directory. Existing evidence was not overwritten.'}))
        raise SystemExit(1)
