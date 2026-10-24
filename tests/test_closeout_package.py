import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile
import pytest

SCRIPTS=Path(__file__).resolve().parents[1]/'scripts'
sys.path.insert(0,str(SCRIPTS))
from verify_package import REQUIRED,verify,allowed
import package_project


def sources():
    files={name:b'x' for name in REQUIRED}
    files['outputs/offline-demo-v2.5.0/evaluation.json']=json.dumps({'ok':True,'version':'2.5.0','provider_network_attempts':0,'files':{'index.html':hashlib.sha256(b'x').hexdigest()}}).encode()
    files['outputs/closeout-acceptance-v2.5.0/evaluation.json']=json.dumps({'ok':True,'version':'2.5.0','checks':[{'passed':True}]}).encode()
    return files


def write_archive(path,files,*,extra=None,tamper=None):
    manifest={'version':'2.5.0','contains_user_data':False,'files':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in files.items()}}
    with zipfile.ZipFile(path,'w') as archive:
        for name,data in files.items():archive.writestr(name,b'tampered' if name==tamper else data)
        archive.writestr('PACKAGE-MANIFEST.json',json.dumps(manifest))
        if extra:archive.writestr(*extra)


def test_valid_archive_and_content_tamper(tmp_path):
    path=tmp_path/'delivery.zip'; files=sources();write_archive(path,files)
    assert verify(path)['ok']
    write_archive(path,files,tamper='app/main.py')
    with pytest.raises(ValueError,match='hash'):verify(path)


@pytest.mark.parametrize('name',['../escape.py','app/../../escape.py','C:/secret','app/.env','data/index.sqlite3','work/log.json','app/__pycache__/main.pyc','outputs/user.csv','.local-model-env','app/./escape.py','app//escape.py','app/CON.py','app/trailing.'])
def test_forbidden_paths(tmp_path,name):
    assert not allowed(name)
    path=tmp_path/'bad.zip';write_archive(path,sources(),extra=(name,b'private'))
    with pytest.raises(ValueError):verify(path)


def test_missing_required_and_duplicate_entries(tmp_path):
    path=tmp_path/'bad.zip';files=sources();del files['离线演示.cmd'];write_archive(path,files)
    with pytest.raises(ValueError,match='Required'):verify(path)
    with pytest.warns(UserWarning):write_archive(path,sources(),extra=('app/main.py',b'extra'))
    with pytest.raises(ValueError,match='Duplicate'):verify(path)
    write_archive(path,sources(),extra=('app/Main.py',b'case collision'))
    with pytest.raises(ValueError,match='Duplicate'):verify(path)


def test_clean_package_excludes_private_data_and_refuses_overwrite(tmp_path,monkeypatch):
    root=tmp_path/'source';root.mkdir()
    for name,content in sources().items():
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(content)
    (root/'data').mkdir();(root/'data/private.raw').write_bytes(b'private business data')
    (root/'.env').write_text('OPENAI_API_KEY=private-fixture-secret-987654321',encoding='utf-8')
    monkeypatch.setattr(package_project,'ROOT',root)
    target=tmp_path/'new.zip'; assert package_project.package(target)['ok']
    assert 'data/private.raw' not in zipfile.ZipFile(target).namelist()
    original=target.read_bytes()
    with pytest.raises(ValueError,match='exists'):package_project.package(target)
    assert target.read_bytes()==original
    (root/'app/main.py').write_bytes(b'private-fixture-secret-987654321')
    with pytest.raises(ValueError,match='Credential'):package_project.package(tmp_path/'leak.zip')
    assert not (tmp_path/'leak.zip').exists()
