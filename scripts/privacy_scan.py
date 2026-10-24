"""Scan staged public files and nested ZIP/XLSX content without printing secrets.

Optional private reference files stay outside the repository. This is a release
check, not a guarantee that arbitrary personal information can be recognized.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
PATTERNS={
    'provider_key':rb'\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{24,}',
    'github_token':rb'\bgh[pousr]_[A-Za-z0-9]{30,}',
    'github_pat':rb'\bgithub_pat_[A-Za-z0-9_]{30,}',
    'private_key':rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',
    'user_profile_path':rb'[A-Za-z]:[\\/]Users[\\/][A-Za-z0-9_.~-]+',
    'phone_in_context':rb'(?:phone|mobile|tel)[\s:=\"\x27]{1,8}(?:\+86)?1[3-9][0-9]{9}\b',
}


def scan(paths, private_envs=(), deny_list=None):
    from dotenv import dotenv_values
    private=[]
    for env in private_envs:
        for interpolate in (False,True):
            private += [value.encode() for key,value in dotenv_values(env,interpolate=interpolate).items()
                        if value and len(value)>=12 and any(word in key.upper() for word in ('KEY','TOKEN','SECRET','PASSWORD'))]
    if deny_list:
        private += [value.encode() for value in json.loads(deny_list.read_text(encoding='utf-8-sig')) if value]
    findings=[]
    entries=0
    def inspect(name,blob,depth=0):
        nonlocal entries
        entries+=1
        for rule,pattern in PATTERNS.items():
            if re.search(pattern,blob,re.I if rule in ('phone_in_context','user_profile_path') else 0):
                findings.append({'path':name,'rule':rule})
        if any(value in blob for value in private): findings.append({'path':name,'rule':'private_reference_match'})
        if blob[:2]==b'PK' and depth<3:
            try:
                with zipfile.ZipFile(io.BytesIO(blob)) as archive:
                    if sum(info.file_size for info in archive.infolist())>200*1024*1024: raise ValueError('Archive too large for scan')
                    for info in archive.infolist():
                        if not info.is_dir(): inspect(name+'!'+info.filename,archive.read(info),depth+1)
            except zipfile.BadZipFile:
                findings.append({'path':name,'rule':'unreadable_archive'})
    hashes={}
    for path in paths:
        data=path.read_bytes()
        name=path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
        if any(part in {'.env','.local-model-env','data','demo-data','backups','.venv','work','.git','__pycache__'} for part in Path(name).parts):
            findings.append({'path':name,'rule':'private_path'})
        inspect(name,data)
        hashes[name]=hashlib.sha256(data).hexdigest()
    return {'ok':not findings,'time_utc':datetime.now(timezone.utc).isoformat(),'files_scanned':len(paths),
            'nested_entries_scanned':entries,'private_reference_checks_enabled':bool(private),
            'findings':findings,'file_sha256':hashes,
            'scope':'Explicit submitted files plus nested archive contents; common credential/path patterns and optional private references. Does not prove absence of every kind of personal information.'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--private-env',action='append',type=Path,default=[])
    parser.add_argument('--deny-list',type=Path)
    parser.add_argument('--archive',type=Path)
    args=parser.parse_args()
    if args.archive:
        paths=[args.archive.resolve()]
    else:
        result=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True,check=True)
        paths=[ROOT/name.decode('utf-8') for name in result.stdout.split(b'\0') if name]
    report=scan(paths,args.private_env,args.deny_list)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    with args.out.open('x',encoding='utf-8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
    print(json.dumps({k:report[k] for k in ('ok','files_scanned','nested_entries_scanned','findings')},ensure_ascii=False))
    raise SystemExit(0 if report['ok'] else 1)
