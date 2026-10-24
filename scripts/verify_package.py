"""Verify a clean delivery archive without extracting or importing the application."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import re
import zipfile

FOLDERS = {'app','web','tests','sql','examples','scripts','docs','design-system','licenses','.github'}
ROOT_FILES = {'README.md','AGENTS.md','HANDOFF.md','requirements.txt','requirements.lock.txt',
              '.gitignore','.gitattributes','.env.example','启动数策.cmd','停止数策.cmd','离线演示.cmd','数据维护.cmd',
              'LICENSE','CHANGELOG.md','SECURITY.md','CONTRIBUTING.md'}
EVIDENCE = {'outputs/最终公开审查-v2.6.md','outputs/manual-verification-v2.6.json',
            'outputs/privacy-scan-v2.6.json','outputs/real-model-settings-v2.6.json'}
REQUIRED = {'README.md','app/main.py','app/version.py','web/index.html','web/ops.js',
            'scripts/launch.ps1','scripts/stop.ps1','scripts/setup.ps1','scripts/service.py',
            'scripts/evaluate.py','scripts/offline_eval.py','scripts/verify_package.py','scripts/package_project.py',
            'scripts/package_smoke.py','scripts/checkpoint.py','scripts/maintain.py',
            'docs/用户手册.md','docs/部署与评测.md','docs/回退说明.md','离线演示.cmd',
            'outputs/offline-demo-v2.6.0/index.html','outputs/offline-demo-v2.6.0/evaluation.json',
            'outputs/最终公开审查-v2.6.md','outputs/manual-verification-v2.6.json',
            'outputs/closeout-acceptance-v2.6.0/evaluation.json','outputs/closeout-acceptance-v2.6.0/pytest.xml'}
REQUIRED |= ROOT_FILES
REQUIRED |= {'app/model_settings.py','web/model-settings.js','scripts/model_settings_smoke.py',
             'tests/test_model_settings.py','tests/model-settings.test.cjs'}


def allowed(name):
    path = PurePosixPath(name)
    parts=name.split('/')
    if '\\' in name or ':' in name or path.is_absolute() or any(p in {'','..','.'} for p in parts): return False
    if any(p.endswith((' ','.')) or re.match(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)',p,re.I) for p in parts): return False
    if any(ord(c)<32 for c in name): return False
    if any(part in {'__pycache__','.git','.venv','.env','.local-model-env','data','demo-data','backups','work'} for part in path.parts): return False
    if path.suffix in {'.pyc','.zip','.raw','.gz','.sqlite3'}: return False
    if path.suffix=='.log':
        return name in {'outputs/closeout-acceptance-v2.6.0/'+n for n in ('pytest.log','javascript-tests.log','javascript-syntax-app.log','javascript-syntax-ops.log','javascript-syntax-model-settings.log','offline.log','benchmark-first.log','benchmark-repeat.log')}
    if name in ROOT_FILES or name in EVIDENCE: return True
    if path.parts[0] in FOLDERS and len(path.parts)>1: return True
    if name.startswith('outputs/offline-demo-v2.6.0/'): return len(path.parts)==3
    if name.startswith('outputs/closeout-acceptance-v2.6.0/'):
        return path.suffix in {'.json','.xml','.html','.csv','.xlsx'}
    return name.startswith('outputs/pytest-closeout-') and path.suffix=='.xml' and len(path.parts)==2


def verify(path):
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist(); names=[i.filename for i in infos]
        if len(names)!=len(set(n.casefold() for n in names)): raise ValueError('Duplicate archive entries')
        if sum(i.file_size for i in infos)>200*1024*1024: raise ValueError('Archive decompressed size limit')
        if 'PACKAGE-MANIFEST.json' not in names: raise ValueError('Missing manifest')
        for entry in infos:
            if stat.S_ISLNK(entry.external_attr>>16) or entry.is_dir(): raise ValueError('Unexpected link/directory entry')
            if entry.filename!='PACKAGE-MANIFEST.json' and not allowed(entry.filename): raise ValueError('Forbidden entry: '+entry.filename)
        if archive.testzip(): raise ValueError('ZIP CRC mismatch')
        manifest=json.loads(archive.read('PACKAGE-MANIFEST.json'))
        files=manifest['files']
        if manifest.get('version')!='2.6.0' or manifest.get('contains_user_data') is not False: raise ValueError('Invalid package contract')
        if set(files)!=set(names)-{'PACKAGE-MANIFEST.json'}: raise ValueError('Manifest entry mismatch')
        if not REQUIRED<=set(files): raise ValueError('Required deliverable missing')
        for name, expected in files.items():
            data=archive.read(name)
            if len(data)!=expected['bytes'] or hashlib.sha256(data).hexdigest()!=expected['sha256']: raise ValueError('Content hash mismatch: '+name)
        evidence=json.loads(archive.read('outputs/offline-demo-v2.6.0/evaluation.json'))
        if not evidence['ok'] or evidence['version']!=manifest['version'] or evidence['provider_network_attempts']!=0: raise ValueError('Offline evidence invalid')
        for name,expected in evidence['files'].items():
            if '/' in name or '\\' in name or name in {'.','..'}: raise ValueError('Invalid offline evidence path')
            if hashlib.sha256(archive.read('outputs/offline-demo-v2.6.0/'+name)).hexdigest()!=expected: raise ValueError('Offline evidence hash mismatch')
        acceptance=json.loads(archive.read('outputs/closeout-acceptance-v2.6.0/evaluation.json'))
        if not acceptance.get('ok') or acceptance.get('version')!=manifest['version'] or not acceptance.get('checks') or not all(c['passed'] for c in acceptance['checks']): raise ValueError('Acceptance evidence invalid')
        return {'ok':True,'version':manifest['version'],'entries':len(names),'zip_crc_passed':True,'file_hashes_passed':True,'contains_user_data':False,'bytes':Path(path).stat().st_size,'sha256':hashlib.sha256(Path(path).read_bytes()).hexdigest()}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('archive',type=Path)
    args=parser.parse_args()
    try: print(json.dumps(verify(args.archive),ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok':False,'detail':'Package verification failed. Do not extract or run this archive.'}))
        raise SystemExit(1)
