"""Frozen offline contract benchmark. Never edits questions or expected answers."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SUITE = ROOT/'tests/benchmark-v1.json'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_metadata():
    def git(*args):
        result = subprocess.run(['git',*args],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',errors='replace')
        return result.stdout.strip() if result.returncode == 0 else None
    try:
        return {'commit':git('rev-parse','HEAD'), 'dirty':bool(git('status','--porcelain'))}
    except FileNotFoundError:
        return {'commit':None,'dirty':None}


def pointer(value, path):
    for key in path.strip('/').split('/'):
        value = value[int(key)] if isinstance(value,list) else value[key]
    return value


def evaluate(output, previous=None):
    if output.exists(): raise ValueError('Choose a new output file; evidence is immutable')
    suite = json.loads(SUITE.read_text(encoding='utf-8'))
    frozen_hash = digest(SUITE)
    guard = SUITE.with_suffix('.sha256').read_text(encoding='ascii').split()[0]
    if frozen_hash != guard: raise ValueError('Frozen question set fingerprint changed; create a new explicitly versioned suite')
    os.environ['SHUCE_OFFLINE']='1'
    os.environ['SHUCE_DATA_DIR']=str(ROOT/'work'/('benchmark-'+uuid.uuid4().hex)/'data')
    from fastapi.testclient import TestClient
    from app.main import app
    from app.version import VERSION
    started = time.perf_counter()
    network = []
    def blocked(*args,**kwargs):
        network.append(True)
        raise AssertionError('Model access forbidden during offline benchmark')
    rows=[]
    with TestClient(app,base_url='http://127.0.0.1',headers={'X-Shuce-Client':'1'}) as client, patch('app.ai.httpx.AsyncClient',blocked):
        datasets = {}
        for key in ('coffee_orders','coffee_content'):
            response = client.post('/api/scenarios/'+key)
            if response.status_code!=200: raise RuntimeError('Benchmark fixture initialization failed')
            datasets[key]=response.json()['id']
        for case in suite['cases']:
            t0=time.perf_counter()
            failures=[]
            try:
                response = client.post('/api/ops/demo') if case.get('review') else client.post('/api/analyze',json={'dataset_id':datasets[case['dataset']],'question':case['question']})
                if response.status_code!=200:
                    failures.append('http_contract')
                else:
                    result=response.json()
                    for path,expected in case.get('expect',{}).items():
                        try: actual=pointer(result,path)
                        except (KeyError,IndexError,TypeError):
                            failures.append('missing_field'); continue
                        if actual != expected: failures.append('numeric_mismatch' if isinstance(expected,(int,float)) and not isinstance(expected,bool) else 'contract_mismatch')
                    if case.get('nonempty') and not pointer(result,case['nonempty']): failures.append('guard_or_clarification')
                    if case.get('provider') and result['trace']['provider']!=case['provider']: failures.append('provider_mode')
            except Exception as exc:
                # Categories only: no provider/body/credential exception text.
                failures.append('exception_'+type(exc).__name__)
            rows.append({'id':case['id'],'passed':not failures,'failures':failures,'elapsed_ms':round((time.perf_counter()-t0)*1000,3)})
    passed=sum(r['passed'] for r in rows)
    report={'ok':passed==len(rows) and not network,'suite':suite['suite'],'scope':suite['scope'],
            'suite_sha256':frozen_hash,'runner_sha256':digest(Path(__file__)),
            'version':VERSION,'time_utc':datetime.now(timezone.utc).isoformat(),
            'git':git_metadata(),'total':len(rows),'passed':passed,'pass_rate':passed/len(rows),
            'failure_categories':dict(Counter(f for row in rows for f in row['failures'])),
            'elapsed_ms':round((time.perf_counter()-started)*1000,3),'results':rows,
            'mode':'offline-rule-and-deterministic-compute','model_id':None,
            'model_parameters':None,'model_requests':0,'provider_network_attempts':len(network),
            'cost':{'amount':0,'currency':'USD','basis':'No provider requests; local compute/electricity not estimated'},
            'comparison':{'available':False,'reason':'No previous run supplied'}}
    if previous:
        prior=json.loads(previous.read_text(encoding='utf-8'))
        compatible=all(prior.get(k)==report[k] for k in ('suite_sha256','runner_sha256','mode'))
        report['comparison']={'available':compatible,'reason':'Same frozen questions and assertion runner' if compatible else 'Question, assertion runner or mode differs; scores are not comparable'}
        if compatible:
            report['comparison'].update(previous_time_utc=prior['time_utc'],pass_rate_delta=report['pass_rate']-prior['pass_rate'],elapsed_ms_delta=round(report['elapsed_ms']-prior['elapsed_ms'],3),previous_git_commit=prior['git']['commit'])
    output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('x',encoding='utf-8') as stream: json.dump(report,stream,ensure_ascii=False,indent=2)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--previous',type=Path)
    args=parser.parse_args()
    try:
        report=evaluate(args.out,args.previous)
        print(json.dumps({k:report[k] for k in ('ok','total','passed','pass_rate','failure_categories','elapsed_ms','comparison')},ensure_ascii=False))
        raise SystemExit(0 if report['ok'] else 1)
    except Exception:
        print(json.dumps({'ok':False,'detail':'Benchmark failed; check frozen-suite fingerprint, previous report and new output path.'}))
        raise SystemExit(1)
