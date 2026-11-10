"""Build a clean source and simulated evidence package; never ship the working database."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from app.version import VERSION
from verify_package import allowed,verify,ROOT_FILES,FOLDERS


def package(destination):
    destination=destination.resolve()
    if destination.exists(): raise ValueError('Destination already exists; use a new filename')
    if VERSION!='2.7.0': raise ValueError('Update the package contract for the new version')
    from dotenv import dotenv_values
    credentials=[]
    env_files=[ROOT/'.env']
    pointer=ROOT/'.local-model-env'
    if pointer.is_file(): env_files.append(Path(pointer.read_text(encoding='utf-8-sig').strip()))
    for env_file in env_files:
        if env_file.is_file():
            for interpolate in (False,True):
                credentials += [v.encode() for k,v in dotenv_values(env_file,interpolate=interpolate).items() if v and ('KEY' in k or 'TOKEN' in k or 'SECRET' in k) and len(v)>=12]
    if len(os.getenv('OPENAI_API_KEY',''))>=12: credentials.append(os.environ['OPENAI_API_KEY'].encode())
    candidates=[ROOT/name for name in ROOT_FILES if (ROOT/name).is_file()]
    for folder in (*sorted(FOLDERS),'outputs'):
        candidates.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and allowed(p.relative_to(ROOT).as_posix()))
    files=sorted(set(candidates))
    if any(p.is_symlink() or not p.resolve().is_relative_to(ROOT.resolve()) for p in files): raise ValueError('Unexpected source symlink')
    destination.parent.mkdir(parents=True,exist_ok=True)
    handle,temporary=tempfile.mkstemp(prefix='shuce-package-',suffix='.tmp',dir=destination.parent)
    os.close(handle)
    temporary=Path(temporary)
    manifest={'version':VERSION,'created_at':datetime.now().isoformat(),'contains_user_data':False,'files':{}}
    try:
        with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            for path in files:
                name=path.relative_to(ROOT).as_posix(); data=path.read_bytes()
                if any(key in data for key in credentials): raise ValueError('Credential detected in allowlisted source or evidence')
                manifest['files'][name]={'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
                archive.writestr(name,data)
            archive.writestr('PACKAGE-MANIFEST.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        report=verify(temporary)
        # Exclusive hard link avoids a concurrent run overwriting a finished archive.
        os.link(temporary,destination)
        report['path']=str(destination)
        return report
    finally:
        temporary.unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest',type=Path,default=ROOT/'outputs'/('数策-v'+VERSION+'-交付包.zip'))
    args=parser.parse_args()
    try: print(json.dumps(package(args.dest),ensure_ascii=False))
    except Exception:
        print(json.dumps({'ok':False,'detail':'Packaging failed; inspect required evidence, credentials and destination. Previous archives were not overwritten.'}))
        raise SystemExit(1)
