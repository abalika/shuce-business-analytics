"""Exercise settings over real local HTTP in a clean extraction, using a stub provider."""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import threading
import urllib.error
import urllib.request
import uuid

ROOT=Path(__file__).resolve().parents[1]


def smoke(project,output):
    project=project.resolve()
    if project==ROOT or (project/'.env').exists() or output.exists():
        raise ValueError('Use a clean installed extraction without .env and a new report path')
    if not (project/'.venv/Scripts/python.exe').is_file():raise ValueError('Install the clean extraction first')
    fixture='settings-network-fixture-'+uuid.uuid4().hex
    calls=[]
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            supplied=self.headers.get('Authorization')
            payload=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))))
            if supplied!='Bearer '+fixture or self.path!='/v1/chat/completions':
                self.send_error(401);return
            if payload['max_tokens']!=32 or len(payload['messages'])!=1:
                self.send_error(400);return
            calls.append(True)
            content=json.dumps({'choices':[{'message':{'content':'{"ok":true}'}}]}).encode()
            self.send_response(200);self.send_header('Content-Type','application/json')
            self.end_headers();self.wfile.write(content)
    provider=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=provider.serve_forever,daemon=True).start()
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    data=project/'work'/('settings-check-'+uuid.uuid4().hex)
    checks=[];started=False
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def require(condition,label):
        if not condition:raise ValueError(label)
        checks.append(label)
    def ps(script,*args):
        result=subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(project/'scripts'/script),*map(str,args)],
            cwd=project,env={**os.environ,'PYTHONIOENCODING':'utf-8'},capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=50)
        if result.returncode:raise ValueError('Service command failed: '+script)
    def start():ps('launch.ps1','-NoBrowser','-Port',port,'-DataDir',data)
    def stop():ps('stop.ps1','-Port',port)
    def request(path,method='GET',body=None):
        req=urllib.request.Request(f'http://127.0.0.1:{port}'+path,method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Shuce-Client':'1','Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}'})
        with opener.open(req,timeout=20) as response:
            content=response.read()
            if fixture.encode() in content:raise ValueError('A response disclosed a fixture secret')
            return json.loads(content) if response.headers.get('Content-Type','').startswith('application/json') else content
    result={'ok':False,'time_utc':datetime.now(timezone.utc).isoformat(),'real_model_requests':0,'browser_inspected':False,'checks':checks}
    try:
        start();started=True
        status=request('/api/model-settings')
        require(not status['offline'] and not status['has_key'],'normal-mode-starts-without-credentials')
        require(b'openModelSettings' in request('/') and b'openModelSettings' in request('/explore'),'settings-entry-served-on-both-pages')
        require(len(request('/static/model-settings.js'))>1000,'settings-script-served')
        body={'base_url':f'http://127.0.0.1:{provider.server_port}/v1','model':'local-stub-only','api_key':fixture,'remember':False,'revision':status['revision']}
        tested=request('/api/model-settings/test','POST',body)
        require(tested['ok'] and len(calls)==1,'minimal-request-reaches-local-stub-provider')
        require(not request('/api/model-settings')['has_key'],'testing-does-not-save')
        saved=request('/api/model-settings','POST',body)
        require(saved['source']=='session' and request('/api/status')['ai']['configured'] and not (project/'.env').exists(),'session-save-is-immediate-without-disk-write')
        body.update(revision=saved['revision'],remember=True,api_key=None)
        saved=request('/api/model-settings','POST',body)
        require((project/'.env').exists() and saved['source']=='local_file','explicit-remember-writes-local-config')
        stop();started=False;start();started=True
        status=request('/api/model-settings')
        require(status['has_key'] and status['model']=='local-stub-only','remembered-config-survives-real-service-restart')
        cleared=request('/api/model-settings','DELETE',{'revision':status['revision']})
        require(not cleared['has_key'] and fixture not in (project/'.env').read_text(encoding='utf-8'),'clear-removes-local-key')
        stop();started=False;start();started=True
        require(not request('/api/status')['ai']['configured'],'cleared-config-stays-disabled-after-restart')
        require(fixture not in (project/'work'/f'server-{port}.log').read_text(encoding='utf-8'),'service-log-does-not-contain-fixture-secret')
        stop();started=False
        with socket.socket() as sock:require(sock.connect_ex(('127.0.0.1',port))!=0,'service-stopped')
        result.update(ok=True,stub_provider_requests=len(calls),service_stopped=True)
    except Exception as exc:
        result['failure']=str(exc) if isinstance(exc,ValueError) else type(exc).__name__
    finally:
        if started:
            try:stop()
            except Exception:result.update(ok=False,cleanup_failed=True)
        provider.shutdown();provider.server_close()
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    try:
        result=smoke(args.project,args.out)
        print(json.dumps(result,ensure_ascii=False))
        raise SystemExit(0 if result['ok'] else 1)
    except Exception:
        print(json.dumps({'ok':False,'detail':'Use a clean extraction with installed dependencies and no existing model config.'}))
        raise SystemExit(1)
