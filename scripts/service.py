"""Local service lifecycle. Own-process identity is verified before stopping anything."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def port_number(value):
    value = int(value)
    if not 1024 <= value <= 65535 or value == 7863:
        raise argparse.ArgumentTypeError('Use 1024..65535, excluding the shared gateway port 7863')
    return value


def state_path(port):
    return ROOT / 'work' / f'service-{port}.json'


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


@contextmanager
def lifecycle_lock(port):
    folder = ROOT / 'work'
    folder.mkdir(exist_ok=True)
    with (folder / f'service-{port}.lock').open('a+b') as handle:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            handle.write(b'0'); handle.flush(); handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def probe(port):
    # Ignore machine proxy settings for local process identity checks.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/status', timeout=1) as response:
            return json.load(response)
    except Exception:
        return None


def listening(port):
    with socket.socket() as connection:
        connection.settimeout(.5)
        return connection.connect_ex(('127.0.0.1', port)) == 0


def identity(data, offline):
    return hashlib.sha256((str(ROOT.resolve()) + '|' + str(data.resolve()) + '|' + str(offline)).encode()).hexdigest()[:24]


def powershell(code):
    encoded = base64.b64encode(code.encode('utf-16le')).decode()
    result = subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-EncodedCommand',encoded],
                            capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise ValueError('Windows service operation failed; no other service was stopped.')
    return result.stdout.strip()


def start(port, data, offline):
    data = data.resolve()
    expected = identity(data, offline)
    current = probe(port)
    if current and current.get('instance_id') == expected:
        return {'ok':True,'reused':True,'port':port,'pid':current['pid'],'offline':offline}
    if listening(port):
        raise ValueError('Port occupied by another instance or mode. Stop its original instance or choose another port.')
    if (data / 'index.sqlite3').exists():
        from app.maintenance import make_backup
        make_backup(data, ROOT / 'backups' / f'pre-start-{port}-{uuid.uuid4().hex}.zip')
    nonce = uuid.uuid4().hex
    config_path = ROOT / 'work' / f'service-config-{port}-{nonce}.json'
    config = {'port':port,'data':str(data),'offline':offline,'instance_id':expected,'nonce':nonce}
    write_json(config_path, config)
    executable = Path(sys.executable)
    if os.name == 'nt':
        executable = executable.with_name('pythonw.exe')
        if not executable.is_file():
            raise ValueError('Windows deployment requires the virtual environment pythonw.exe')
    command = [str(executable), str(Path(__file__).resolve()), 'run', '--config',str(config_path)]
    if os.name == 'nt':
        # WMI creates an independent hidden process, surviving terminal/job cleanup.
        command64 = base64.b64encode(subprocess.list2cmdline(command).encode('utf-8')).decode()
        root64 = base64.b64encode(str(ROOT).encode('utf-8')).decode()
        powershell("$ErrorActionPreference='Stop'; $taskCommand=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+command64+"')); $taskDirectory=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+root64+"')); $taskResult=Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{CommandLine=$taskCommand;CurrentDirectory=$taskDirectory}; if ($taskResult.ReturnValue -ne 0) {exit 1}")
    else:
        with (ROOT/'work'/f'server-{port}.log').open('ab') as log:
            subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    for _ in range(100):
        current = probe(port)
        if current and current.get('instance_id') == expected:
            return {'ok':True,'reused':False,'port':port,'pid':current['pid'],'offline':offline}
        time.sleep(.2)
    raise ValueError(f'Startup not confirmed. Inspect work/server-{port}.log and service status before retrying.')


def stop(port):
    path = state_path(port)
    if not path.exists():
        if listening(port):
            raise ValueError('No owned process record; refusing to stop the port listener.')
        return {'ok':True,'already_stopped':True,'port':port}
    record = json.loads(path.read_text(encoding='utf-8'))
    pid = int(record['pid'])
    config = Path(record['config']).resolve()
    if config.parent != (ROOT/'work').resolve() or record['nonce'] not in config.name or pid <= 0:
        raise ValueError('Invalid process ownership record; refusing to stop.')
    if os.name == 'nt':
        # Both the script and unpredictable configuration filename must match.
        script64 = base64.b64encode(str(Path(__file__).resolve()).encode()).decode()
        config64 = base64.b64encode(str(config).encode()).decode()
        result = powershell("$ErrorActionPreference='Stop'; $taskProcess=Get-CimInstance Win32_Process -Filter 'ProcessId="+str(pid)+"'; if (-not $taskProcess) {Write-Output 'gone'; exit 0}; $taskScript=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+script64+"')); $taskConfig=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+config64+"')); if (-not $taskProcess.CommandLine.Contains($taskScript) -or -not $taskProcess.CommandLine.Contains($taskConfig)) {exit 2}; Stop-Process -Id "+str(pid)+"; Write-Output 'stopped'")
    else:
        # Linux support remains explicit; do not kill a PID without checking ownership.
        proc = Path(f'/proc/{pid}/cmdline')
        if proc.exists():
            args = proc.read_bytes().split(b'\0')
            if str(config).encode() not in args or str(Path(__file__).resolve()).encode() not in args:
                raise ValueError('Process ownership mismatch; refusing to stop.')
            import signal
            os.kill(pid, signal.SIGTERM)
        result = 'stopped'
    for _ in range(50):
        if not listening(port):
            return {'ok':True,'port':port,'result':result,'shared_gateway_untouched':True}
        time.sleep(.1)
    raise ValueError('Stop not confirmed; inspect status. No unrelated process was stopped.')


def serve(config_path):
    config = json.loads(config_path.read_text(encoding='utf-8'))
    os.chdir(ROOT)
    os.environ['SHUCE_DATA_DIR'] = config['data']
    os.environ['SHUCE_OFFLINE'] = '1' if config['offline'] else '0'
    os.environ['SHUCE_PORT'] = str(config['port'])
    os.environ['SHUCE_INSTANCE_ID'] = config['instance_id']
    log = (ROOT/'work'/f"server-{config['port']}.log").open('a',encoding='utf-8',buffering=1)
    sys.stdout = sys.stderr = log
    from app.main import app, import_scenario
    from app.scenarios import SCENARIOS
    for key in SCENARIOS:
        import_scenario(key)
    write_json(state_path(config['port']), {**config,'config':str(config_path.resolve()),'pid':os.getpid()})
    import uvicorn
    uvicorn.run(app, host='127.0.0.1', port=config['port'], access_log=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start','stop','status','run'])
    parser.add_argument('--port',type=port_number,default=8765)
    parser.add_argument('--data',type=Path)
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--config',type=Path)
    args = parser.parse_args()
    if args.action == 'run':
        if not args.config: parser.error('run requires --config')
        serve(args.config)
        return 0
    try:
        with lifecycle_lock(args.port):
            if args.action == 'start':
                result = start(args.port, args.data or ROOT/('demo-data' if args.offline else 'data'), args.offline)
            elif args.action == 'stop':
                result = stop(args.port)
            else:
                current = probe(args.port)
                result = {'ok':bool(current), 'port':args.port,'status':current,'listening':listening(args.port)}
        print(json.dumps(result,ensure_ascii=False))
        return 0 if result['ok'] else 1
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else 'Service operation failed. Inspect local logs, disk permissions and startup lock; do not delete data.'
        print(json.dumps({'ok':False,'detail':message},ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
