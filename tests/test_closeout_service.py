import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('shuce_service', ROOT/'scripts/service.py')
service = importlib.util.module_from_spec(spec)
spec.loader.exec_module(service)


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


@pytest.mark.parametrize('port',['0','1023','7863','65536'])
def test_reserved_ports_rejected(port):
    with pytest.raises(Exception): service.port_number(port)


def test_distinct_directory_and_mode_have_distinct_identity(tmp_path):
    assert service.identity(tmp_path,True) != service.identity(tmp_path,False)
    assert service.identity(tmp_path,True) != service.identity(tmp_path/'other',True)


@pytest.mark.skipif(os.name != 'nt',reason='Windows launcher acceptance')
def test_stop_rejects_pid_owned_by_another_command():
    port = free_port(); nonce = uuid.uuid4().hex
    state = service.state_path(port); state.parent.mkdir(exist_ok=True)
    state.write_text(json.dumps({'pid':os.getpid(),'nonce':nonce,'config':str(ROOT/'work'/f'service-config-{port}-{nonce}.json')}),encoding='utf-8')
    try:
        with pytest.raises(ValueError): service.stop(port)
        assert os.getpid() > 0
    finally: state.unlink()


@pytest.mark.skipif(os.name != 'nt',reason='Windows launcher acceptance')
def test_powershell_detached_lifecycle_and_persistence(tmp_path):
    port = free_port(); data = tmp_path/'中文 空格 data'
    env = {**os.environ,'PYTHONIOENCODING':'utf-8'}
    def command(script,*args,check=True):
        result = subprocess.run(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'scripts'/script),*args],cwd=ROOT,env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=45)
        if check: assert result.returncode == 0, result.stdout + result.stderr
        return result
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,post=False):
        req = urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=b'{}' if post else None,headers={'X-Shuce-Client':'1','Content-Type':'application/json','Origin':f'http://localhost:{port}'})
        with opener.open(req,timeout=5) as response: return json.load(response)
    before_backups = set((ROOT/'backups').glob(f'pre-start-{port}-*.zip'))
    try:
        command('launch.ps1','-Offline','-NoBrowser','-Port',str(port),'-DataDir',str(data))
        time.sleep(1)  # Starting shell has already exited; detached child must survive.
        first = request('/api/status')
        assert first['ai']['offline'] and first['ai']['configured'] is False
        review = request('/api/ops/demo',True)
        assert review['totals']['actual_cents'] == 286600
        command('launch.ps1','-Offline','-NoBrowser','-Port',str(port),'-DataDir',str(data))
        assert request('/api/status')['pid'] == first['pid']
        assert set((ROOT/'backups').glob(f'pre-start-{port}-*.zip')) == before_backups
        # Mode mismatch cannot silently reuse or stop a running instance.
        rejected = command('launch.ps1','-NoBrowser','-Port',str(port),'-DataDir',str(data),check=False)
        assert rejected.returncode != 0 and request('/api/status')['pid'] == first['pid']
        command('stop.ps1','-Port',str(port))
        assert not service.listening(port)
        command('launch.ps1','-Offline','-NoBrowser','-Port',str(port),'-DataDir',str(data))
        assert request('/api/ops/reviews/'+review['id'])['id'] == review['id']
        assert len(set((ROOT/'backups').glob(f'pre-start-{port}-*.zip')) - before_backups) == 1
    finally:
        command('stop.ps1','-Port',str(port))
    command('stop.ps1','-Port',str(port))
    assert not service.listening(port)
