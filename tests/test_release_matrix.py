"""Release fault matrix: HTTP feedback, recovery and immutable concurrent reports."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

import httpx
import pandas as pd
import pytest

from app import ai, main, reviews
from app.review_demo import demo_blobs
from tests.test_reviews import client


@pytest.mark.parametrize('kind', ['sales', 'targets', 'operations'])
@pytest.mark.parametrize('fault', ['empty', 'missing_column', 'encoding', 'duplicate'])
def test_import_matrix_recovery(client, kind, fault):
    good = demo_blobs()[kind]
    if fault == 'empty':
        bad = b''
    elif fault == 'encoding':
        bad = b'\xff\xff\xff\xff'
    else:
        frame = pd.read_csv(io.BytesIO(good), dtype=str, keep_default_na=False)
        if fault == 'missing_column':
            frame = frame.drop(columns=frame.columns[0])
        else:
            frame = pd.concat([frame, frame.iloc[:1]], ignore_index=True)
        bad = frame.to_csv(index=False).encode('utf-8-sig')
    response = client.post('/api/ops/sources', files={'file': ('fault.csv', bad)}, data={'kind': kind})
    if fault == 'duplicate':
        assert response.status_code == 200
        q = response.json()['quality']
        assert q['blocking'] and q['duplicate_participants'] == 2
        diagnostics = client.get('/api/ops/sources/' + response.json()['id'] + '/diagnostics')
        assert diagnostics.status_code == 200 and 'duplicate_key' in diagnostics.text
    else:
        assert response.status_code == 422
        assert response.json()['detail'] and 'Traceback' not in response.text
        assert client.get('/api/ops/sources').json() == []
    repaired = client.post('/api/ops/sources', files={'file': ('repaired.csv', good)}, data={'kind': kind})
    assert repaired.status_code == 200 and not repaired.json()['quality']['blocking']


@pytest.mark.parametrize('field,limit', [('facts_note',2500), ('hypothesis',1500), ('next_check',1500)])
def test_report_text_boundary_and_recovery(client, field, limit):
    parent = client.post('/api/ops/demo').json()['id']
    url = '/api/ops/reviews/' + parent + '/report'
    before = len(client.get('/api/ops/reviews').json())
    rejected = client.post(url, json={field: 'private-input-' + '字' * limit})
    assert rejected.status_code == 422 and 'private-input' not in rejected.text
    assert len(client.get('/api/ops/reviews').json()) == before
    accepted = client.post(url, json={field: '字' * limit})
    assert accepted.status_code == 200 and accepted.json()['report'][field] == '字' * limit


@pytest.mark.parametrize('field,limit', [('title',120), ('question',1500), ('confirmation_note',600)])
def test_task_long_text_preserves_history(client, field, limit):
    demo = client.post('/api/ops/demo').json()
    body = {**demo['task'], **{kind+'_id':m['id'] for kind,m in demo['sources'].items()}, field:'字'*(limit+1)}
    before = client.get('/api/ops/reviews').json()
    rejected = client.post('/api/ops/reviews', json=body)
    assert rejected.status_code == 422
    assert client.get('/api/ops/reviews').json() == before
    body[field] = '字' * limit
    assert client.post('/api/ops/reviews', json=body).status_code == 200


def test_actual_oversized_upload_is_rejected_before_parsing(client):
    from app.engine import AnalysisError
    class Upload:
        filename = 'large.csv'
        async def read(self, n):
            return bytes(100 * 1024 * 1024 + 1)
    with pytest.raises(AnalysisError, match='100MB'):
        asyncio.run(main.upload_bytes(Upload()))
    assert client.get('/api/ops/sources').json() == []


@pytest.mark.parametrize('error', [RuntimeError, KeyError, TypeError])
def test_unexpected_errors_are_safe_and_recoverable(client, monkeypatch, caplog, error):
    with monkeypatch.context() as m:
        def broken(_):
            raise error('private-message-and-key-never-log')
        m.setattr(main, 'get_dataset', broken)
        response = client.get('/api/datasets/example')
    assert response.status_code == 503 and response.json()['code'] == 'unexpected_error'
    assert len(response.json()['incident']) == 12 and '历史' in response.json()['detail']
    assert 'private-message' not in response.text + caplog.text
    assert 'Traceback' not in response.text + caplog.text
    assert client.get('/api/datasets/example').status_code == 404
    assert client.get('/api/status').status_code == 200


@pytest.mark.parametrize('failure', ['timeout', 'connect', 401, 402, 429, 503])
def test_provider_matrix_with_structured_recovery(client, monkeypatch, failure):
    monkeypatch.setenv('SHUCE_OFFLINE', '0')
    monkeypatch.setattr(ai, 'config', lambda:{'key':'private-test-value', 'base':'https://provider.invalid/v1', 'model':'test-only'})
    async def no_sleep(*args): pass
    monkeypatch.setattr(ai.asyncio, 'sleep', no_sleep)
    calls = []
    class Provider:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kw):
            calls.append(True)
            if failure == 'timeout': raise httpx.ReadTimeout('private-test-value')
            if failure == 'connect': raise httpx.ConnectError('private-test-value')
            return httpx.Response(failure, request=httpx.Request('POST',url), text='private-test-value')
    monkeypatch.setattr(ai.httpx, 'AsyncClient', Provider)
    d = client.post('/api/demo/orders').json()
    response = client.post('/api/analyze', json={'dataset_id':d['id'], 'question':'净交易额总计多少？'})
    assert response.status_code == 422 and 'private-test-value' not in response.text
    assert len(calls) == (1 if failure in (401,402) else 2)
    assert len(response.json()['trace']['attempts']) == len(calls)
    assert client.get('/api/history/'+d['id']).json() == []
    restored = client.post('/api/analyze', json={'dataset_id':d['id'], 'plan':{'operation':'overview','metric':'amount'}})
    assert restored.status_code == 200 and restored.json()['trace']['provider'] == 'structured'


@pytest.mark.parametrize('content', [b'broken', b'', b'PK\x03\x04broken', b'{}'])
def test_corrupted_backup_cli_never_creates_restore_target(tmp_path, content):
    archive = tmp_path/'damaged.zip'
    archive.write_bytes(content)
    destination = tmp_path/'restored'
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root/'scripts/maintain.py'), 'restore', '--archive', str(archive), '--to', str(destination)],
                            capture_output=True, text=True, encoding='utf-8', env={**os.environ,'PYTHONIOENCODING':'utf-8'}, timeout=20)
    assert result.returncode == 1
    body = json.loads(result.stdout)
    assert body['ok'] is False and body['detail'] and 'Traceback' not in result.stdout
    assert not destination.exists() and archive.read_bytes() == content


@pytest.mark.parametrize('same_content', [True, False])
def test_concurrent_report_save_keeps_each_content_and_parent(client, same_content):
    parent = client.post('/api/ops/demo').json()['id']
    def save(i):
        return reviews.report(parent, reviews.ReportBody(facts_note='note-'+str(0 if same_content else i)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        saved = list(pool.map(save, range(4)))
    assert len({r['id'] for r in saved}) == (1 if same_content else 4)
    for r in saved:
        assert client.get('/api/ops/reviews/'+r['id']).json() == r
    assert 'report' not in client.get('/api/ops/reviews/'+parent).json()
    assert len(client.get('/api/ops/reviews').json()) == (2 if same_content else 5)


@pytest.mark.parametrize('inherited_credentials', [False, True])
def test_offline_main_workflow_ignores_gateway(client, monkeypatch, inherited_credentials):
    monkeypatch.setenv('SHUCE_OFFLINE','1')
    if inherited_credentials:
        for key in ('OPENAI_API_KEY','OPENAI_BASE_URL','OPENAI_MODEL'):
            monkeypatch.setenv(key,'intentionally-unused-test')
    def forbidden(*a, **kw): raise AssertionError('Model provider should not be constructed')
    monkeypatch.setattr(ai.httpx,'AsyncClient',forbidden)
    assert client.get('/api/status').json()['ai']['offline']
    review = client.post('/api/ops/demo').json()
    assert review['totals']['actual_cents'] == 286600
    url = '/api/ops/reviews/'+review['id']
    assert client.post(url+'/brief').json()['brief']['status'] == 'not_configured'
    assert client.get(url+'/xlsx').status_code == 200
    assert client.post(url+'/report',json={'facts_note':'模拟、非实时模型生成'}).status_code == 200
