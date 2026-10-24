import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading

from dotenv import dotenv_values
from fastapi import HTTPException
import httpx
import pytest

from app import ai, model_settings as settings
from tests.test_reviews import client

SECRET='settings-fixture-'+('9'*24)
BASE='https://provider.invalid/v1'


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch,tmp_path):
    monkeypatch.setenv('SHUCE_OFFLINE','0')
    for name in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL'):
        monkeypatch.delenv(name,raising=False)
    monkeypatch.setattr(settings,'ENV_FILE',tmp_path/'.env')
    monkeypatch.setattr(settings,'_override',None)
    monkeypatch.setattr(settings,'_source','none')
    monkeypatch.setattr(settings,'_revision',0)
    monkeypatch.setattr(settings,'_test_lock',threading.Lock())


def body(**changes):
    return {'base_url':BASE,'model':'test-model','api_key':SECRET,'remember':False,'revision':0,**changes}


def test_session_save_immediate_no_disk_no_key_in_response(client,caplog):
    response=client.post('/api/model-settings',json=body())
    assert response.status_code==200 and response.json()['source']=='session'
    assert response.json()['has_key'] and response.json()['revision']==1
    assert SECRET not in response.text+caplog.text
    assert not settings.ENV_FILE.exists()
    assert ai.config()=={'base':BASE,'model':'test-model','key':SECRET}
    read=client.get('/api/model-settings')
    assert read.headers['cache-control']=='no-store' and SECRET not in read.text
    assert 'api_key' not in read.json()


def test_remember_is_atomic_preserves_unrelated_settings_and_survives_restart(client,monkeypatch):
    settings.ENV_FILE.write_text('UNRELATED=keep-me\n',encoding='utf-8')
    result=client.post('/api/model-settings',json=body(remember=True))
    assert result.status_code==200
    saved=dotenv_values(settings.ENV_FILE)
    assert saved['UNRELATED']=='keep-me' and saved['OPENAI_API_KEY']==SECRET
    monkeypatch.setattr(settings,'_override',None)
    monkeypatch.setattr(settings,'_source','none')
    assert ai.config()['key']==SECRET
    assert client.get('/api/model-settings').json()['source']=='local_file'
    assert not list(settings.ENV_FILE.parent.glob('.env-model-*'))


def test_blank_key_reuses_current_only_when_endpoint_unchanged(client):
    assert client.post('/api/model-settings',json=body()).status_code==200
    response=client.post('/api/model-settings',json=body(api_key=None,revision=1,model='another-model'))
    assert response.status_code==200 and ai.config()['key']==SECRET
    response=client.post('/api/model-settings',json=body(api_key=None,revision=2,base_url='https://other.invalid/v1'))
    assert response.status_code==422 and '重新填写' in response.json()['detail']
    assert ai.config()['base']==BASE


def test_clear_prevents_inherited_key_reappearing_after_restart(client,monkeypatch):
    assert client.post('/api/model-settings',json=body(remember=True)).status_code==200
    cleared=client.request('DELETE','/api/model-settings',json={'revision':1})
    assert cleared.status_code==200 and not cleared.json()['has_key']
    assert SECRET not in settings.ENV_FILE.read_text(encoding='utf-8')
    monkeypatch.setenv('OPENAI_API_KEY',SECRET)
    monkeypatch.setenv('OPENAI_BASE_URL',BASE)
    monkeypatch.setenv('OPENAI_MODEL','test-model')
    monkeypatch.setattr(settings,'_override',None)
    assert not ai.status()['configured']


@pytest.mark.parametrize('base', ['http://provider.invalid/v1','file:///etc/passwd','data:text/plain,example',
    'https://user:pass@provider.invalid/v1','https://provider.invalid/v1?api_key=example',
    'https://provider.invalid/v1#secret','https://provider.invalid:wrong/v1','https://provider.invalid/\npath',
    'https://provider.invalid/\\path','https:///v1'])
def test_invalid_addresses_never_saved_or_echoed(client,base):
    response=client.post('/api/model-settings',json=body(base_url=base))
    assert response.status_code==422 and SECRET not in response.text
    assert not settings.ENV_FILE.exists() and not ai.status()['configured']


@pytest.mark.parametrize('base',['http://127.0.0.1:17863/v1','http://localhost:17863/v1','http://[::1]:17863/v1','https://provider.invalid/v1/chat/completions'])
def test_supported_local_and_full_endpoint_addresses(client,base):
    response=client.post('/api/model-settings',json=body(base_url=base))
    assert response.status_code==200 and ai.config()['base']==base


@pytest.mark.parametrize('key',['line\nbreak','line\rbreak','has space','非ASCII'])
def test_invalid_key_characters_do_not_leak(client,key):
    response=client.post('/api/model-settings',json=body(api_key=key))
    assert response.status_code==422 and 'traceback' not in response.text.lower()
    assert not settings.ENV_FILE.exists()


def test_wrong_field_and_oversized_secret_validation_never_echoes(client):
    response=client.post('/api/model-settings',json=body(model=SECRET))
    assert response.status_code==422 and SECRET not in response.text
    response=client.post('/api/model-settings',json=body(api_key=SECRET*200))
    assert response.status_code==422 and SECRET not in response.text


def test_revision_and_concurrent_saves_have_one_winner(client):
    def save(i):
        try:
            return settings.save_settings(settings.SettingsBody(**body(model='model-'+str(i)))).status_code
        except HTTPException as exc:return exc.status_code
    with ThreadPoolExecutor(max_workers=4) as pool:
        statuses=list(pool.map(save,range(4)))
    assert statuses.count(200)==1 and statuses.count(409)==3
    response=client.request('DELETE','/api/model-settings',json={'revision':0})
    assert response.status_code==409 and ai.status()['configured']


def test_disk_failure_keeps_effective_config_and_cleans_temporary_file(client,monkeypatch):
    assert client.post('/api/model-settings',json=body()).status_code==200
    def fail(*args):raise PermissionError(SECRET)
    monkeypatch.setattr(settings.os,'replace',fail)
    response=client.post('/api/model-settings',json=body(revision=1,remember=True,model='replacement'))
    assert response.status_code==503 and SECRET not in response.text
    assert ai.config()['model']=='test-model' and settings._revision==1
    assert not list(settings.ENV_FILE.parent.glob('.env-model-*'))


@pytest.mark.parametrize('operation',['save','test','clear'])
def test_offline_never_loads_or_contacts_provider(client,monkeypatch,operation):
    monkeypatch.setenv('SHUCE_OFFLINE','1')
    def forbidden(*args,**kwargs):raise AssertionError('Offline must not use credentials or provider')
    monkeypatch.setattr(settings,'dotenv_values',forbidden)
    monkeypatch.setattr(settings.httpx,'AsyncClient',forbidden)
    assert client.get('/api/model-settings').json()['offline']
    if operation=='clear':response=client.request('DELETE','/api/model-settings',json={'revision':0})
    else:response=client.post('/api/model-settings'+('/test' if operation=='test' else ''),json=body())
    assert response.status_code==409 and not settings.ENV_FILE.exists()


def install_provider(monkeypatch,status=200,content=None,error=None):
    observed=[]
    class Provider:
        def __init__(self,**options):
            assert options['follow_redirects'] is False and options['trust_env'] is False
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            observed.append({'url':url,**kwargs})
            if error:raise error
            return httpx.Response(status,json=content if content is not None else {'choices':[{'message':{'content':'{"ok":true}'}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(settings.httpx,'AsyncClient',Provider)
    return observed


def test_connection_success_is_real_protocol_check_no_auto_save_no_business_data(client,monkeypatch):
    calls=install_provider(monkeypatch)
    response=client.post('/api/model-settings/test',json=body())
    assert response.status_code==200 and response.json()['ok'] and SECRET not in response.text
    assert len(calls)==1 and calls[0]['url']==BASE+'/chat/completions'
    assert calls[0]['headers']['Authorization']=='Bearer '+SECRET
    assert calls[0]['json']['max_tokens']==32
    assert '2866' not in json.dumps(calls[0]['json'])
    assert not settings.ENV_FILE.exists() and not ai.status()['configured']


@pytest.mark.parametrize('status',[401,402,403,404,429,500,302])
def test_provider_http_failures_redacted_without_retries_or_mutation(client,monkeypatch,status):
    calls=install_provider(monkeypatch,status=status,content={'error':SECRET})
    response=client.post('/api/model-settings/test',json=body())
    assert response.status_code==422 and SECRET not in response.text
    assert len(calls)==1 and not ai.status()['configured'] and not settings.ENV_FILE.exists()


@pytest.mark.parametrize('error',[httpx.ReadTimeout(SECRET),httpx.ConnectError(SECRET)])
def test_network_error_is_safe_and_test_lock_released(client,monkeypatch,error):
    install_provider(monkeypatch,error=error)
    response=client.post('/api/model-settings/test',json=body())
    assert response.status_code==422 and SECRET not in response.text
    assert settings._test_lock.acquire(blocking=False)
    settings._test_lock.release()


@pytest.mark.parametrize('content',[{}, {'choices':[]}, {'choices':[{'message':{'content':SECRET}}]},
    {'choices':[{'message':{'content':'{"ok":false}'}}]}])
def test_malformed_provider_payload_is_not_reported_success(client,monkeypatch,content):
    install_provider(monkeypatch,content=content)
    response=client.post('/api/model-settings/test',json=body())
    assert response.status_code==422 and SECRET not in response.text
    assert not ai.status()['configured']


def test_test_gate_limits_concurrent_gateway_requests(client,monkeypatch):
    install_provider(monkeypatch)
    settings._test_lock.acquire()
    try:
        response=client.post('/api/model-settings/test',json=body())
        assert response.status_code==429
    finally:settings._test_lock.release()


def test_write_protection_cannot_be_bypassed(client):
    response=client.post('/api/model-settings',json=body(),headers={'Origin':'https://other.invalid'})
    assert response.status_code==403 and not ai.status()['configured']


def test_legacy_misconfigured_fields_cannot_echo_the_secret(client,monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY',SECRET)
    monkeypatch.setenv('OPENAI_MODEL',SECRET)
    monkeypatch.setenv('OPENAI_BASE_URL','https://user:'+SECRET+'@provider.invalid/v1?token='+SECRET)
    response=client.get('/api/model-settings')
    assert SECRET not in response.text and response.json()['model']=='' and response.json()['base_url']==''
    assert SECRET not in client.get('/api/status').text
