"""Verify, extract, install and exercise a delivery in a new Windows directory."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import zipfile
from verify_package import verify


def smoke(archive, destination, report_path):
    if os.name!='nt': raise ValueError('This acceptance script targets Windows')
    if destination.exists() or report_path.exists(): raise ValueError('New destination and report paths required')
    checked=verify(archive)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as bundle: bundle.extractall(destination)
    (destination/'work').mkdir(exist_ok=True)
    result={'ok':False,'archive':str(archive),'archive_sha256':checked['sha256'],'destination':str(destination),'started_at':datetime.now().isoformat(),'checks':[],'browser_inspected':False,'model_requests':0}
    env={**os.environ,'PYTHONIOENCODING':'utf-8'}
    def command(name,args):
        with (destination/'work'/(name+'.log')).open('w',encoding='utf-8') as stream:
            run=subprocess.run(args,cwd=destination,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=240)
        if run.returncode: raise ValueError('Failed: '+name)
        result['checks'].append(name)
    def ps(name,script,*args):
        command(name,['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(destination/'scripts'/script),*args])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    started=False
    try:
        command('create-venv',[sys.executable,'-m','venv',str(destination/'.venv')])
        ps('install-locked-dependencies','setup.ps1')
        ps('start-offline','launch.ps1','-Offline','-NoBrowser','-Port',str(port))
        started=True
        time.sleep(1)
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path,body=None,binary=False):
            req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode() if body is not None else None,headers={'X-Shuce-Client':'1','Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}'})
            with opener.open(req,timeout=10) as response:
                return response.read() if binary else json.load(response)
        status=request('/api/status')
        if status['version']!=checked['version'] or not status['ai']['offline']:raise ValueError('Wrong instance mode/version')
        # The launching PowerShell has exited. The service must still answer.
        result['checks'].append('survives-launching-shell-exit')
        review=request('/api/ops/demo',{})
        if review['totals']['actual_cents']!=286600:raise ValueError('Demo control mismatch')
        path='/api/ops/reviews/'+review['id']
        saved=request(path+'/report',{'facts_note':'模拟交易额2866元，目标2660元。','hypothesis':'需核查原表，不能认定因果。','next_check':'检查南门拿铁明细及缺失运营日。'})
        saved_path='/api/ops/reviews/'+saved['id']
        for suffix in ('/report','/export','/xlsx','/evidence/E1/export?kind=sales','/evidence/E1/export?kind=targets','/evidence/E1/export?kind=operations'):
            if len(request(saved_path+suffix,binary=True))<20:raise ValueError('Missing export')
        if request(saved_path+'/brief',{})['brief']['status']!='not_configured':raise ValueError('Offline AI mislabeled')
        result['checks'].append('demo-save-report-excel-json-three-evidence-exports-offline-brief')
        ps('stop-before-restart','stop.ps1','-Port',str(port))
        started=False
        ps('restart-offline','launch.ps1','-Offline','-NoBrowser','-Port',str(port))
        started=True
        if request(saved_path)['report']!=saved['report']:raise ValueError('Saved report changed after restart')
        result['checks'].append('saved-report-persists-after-restart')
        ps('stop-final','stop.ps1','-Port',str(port))
        started=False
        with socket.socket() as sock:
            if sock.connect_ex(('127.0.0.1',port))==0:raise ValueError('Service still listening')
        result.update(ok=True,finished_at=datetime.now().isoformat(),port=port,version=status['version'],service_stopped=True,no_credentials_in_extracted_root=not(destination/'.env').exists())
    except Exception as exc:
        result['failure']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
    finally:
        if started:
            try:ps('cleanup-stop','stop.ps1','-Port',str(port))
            except Exception:result['cleanup_failed']=True;result['ok']=False
        report_path.parent.mkdir(parents=True,exist_ok=True)
        report_path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive',type=Path);parser.add_argument('--to',type=Path,required=True);parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=smoke(args.archive.resolve(),args.to.resolve(),args.report.resolve())
        print(json.dumps(result,ensure_ascii=False))
        raise SystemExit(0 if result['ok'] else 1)
    except Exception:
        print(json.dumps({'ok':False,'detail':'Package acceptance failed. Inspect the report and extracted work logs.'}))
        raise SystemExit(1)
