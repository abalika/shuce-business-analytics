import asyncio
import sqlite3
import threading
import httpx
import pandas as pd
import pytest
from app import ai, main, reviews
from app.engine import AnalysisError
from app.review_demo import demo_blobs
from tests.test_reviews import client


@pytest.mark.parametrize('error,code', [
    (OSError('disk full: secret-example'), 'storage_unavailable'),
    (PermissionError('private-file secret-example'), 'storage_unavailable'),
    (sqlite3.OperationalError('locked secret-example'), 'database_unavailable'),
    (sqlite3.DatabaseError('damaged secret-example'), 'database_unavailable'),
])
def test_storage_failures_safe_and_app_recovers(client, monkeypatch, error, code):
    with monkeypatch.context() as m:
        m.setattr(main, 'get_dataset', lambda _: (_ for _ in ()).throw(error))
        response = client.get('/api/datasets/example')
    assert response.status_code == 503 and response.json()['code'] == code
    assert 'secret-example' not in response.text and 'Traceback' not in response.text
    assert client.get('/api/status').status_code == 200
    assert client.get('/api/datasets/example').status_code == 404


def test_failed_import_does_not_register_success(client, monkeypatch):
    with monkeypatch.context() as m:
        def fail(*a, **kw): raise OSError('disk full')
        m.setattr(pd.DataFrame, 'to_csv', fail)
        response = client.post('/api/demo/orders')
    assert response.status_code == 503
    assert client.get('/api/datasets').json() == []
    assert client.post('/api/demo/orders').status_code == 200


@pytest.mark.parametrize('review_source', [False, True])
@pytest.mark.parametrize('failure', ['write', 'registration'])
def test_partial_import_rolls_back_artifacts_and_can_retry(client, monkeypatch, review_source, failure):
    def submit():
        if review_source:
            return client.post('/api/ops/sources', files={'file': ('sales.csv', demo_blobs()['sales'])})
        return client.post('/api/demo/orders')

    before = set(main.DATA.iterdir())
    with monkeypatch.context() as patch:
        if failure == 'write':
            write = pd.DataFrame.to_csv

            def fail_diagnostics(frame, path, *args, **kwargs):
                if str(path).endswith('-diagnostics.csv.gz'):
                    path.write_bytes(b'incomplete')
                    raise OSError('disk full')
                return write(frame, path, *args, **kwargs)

            patch.setattr(pd.DataFrame, 'to_csv', fail_diagnostics)
        else:
            table = 'review_sources' if review_source else 'datasets'
            reviews.initialize()
            with main.connect() as con:
                con.execute(f"CREATE TRIGGER fail_import BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'injected'); END")
        assert submit().status_code == 503

    assert set(main.DATA.iterdir()) == before
    assert client.get('/api/ops/sources' if review_source else '/api/datasets').json() == []
    if failure == 'registration':
        with main.connect() as con:
            con.execute('DROP TRIGGER fail_import')
    assert submit().status_code == 200


def test_non_loopback_host_cannot_read_local_data(client):
    assert client.get('/api/ops/sources', headers={'Host': 'external.invalid:8765'}).status_code == 400
    assert client.get('/api/model-settings', headers={'Host': 'external.invalid'}).status_code == 400
    assert client.get('/api/status', headers={'Host': 'localhost:8765'}).status_code == 200


@pytest.mark.parametrize('module,endpoint', [(main, '/api/preview'), (reviews, '/api/ops/preview')])
def test_preview_parsing_runs_off_event_loop(client, monkeypatch, module, endpoint):
    parsing = module.read_frame
    worker_ids = []
    loop_id = client.portal.call(threading.get_ident)

    def record_thread(*args):
        worker_ids.append(threading.get_ident())
        return parsing(*args)

    monkeypatch.setattr(module, 'read_frame', record_thread)
    response = client.post(endpoint, files={'file': ('sales.csv', demo_blobs()['sales'])})
    assert response.status_code == 200
    assert len(worker_ids) == 1 and worker_ids[0] != loop_id


def test_validation_errors_do_not_echo_input(client):
    response = client.post('/api/analyze', json={'dataset_id': {'secret-example': 'sensitive-value'}})
    assert response.status_code == 422 and response.json()['code'] == 'invalid_request'
    assert 'secret-example' not in response.text and 'sensitive-value' not in response.text


@pytest.mark.parametrize('codes', [(401,), (402,), (429, 200), (503, 200), (503, 503), ('timeout','timeout'), ('json','json')])
def test_provider_failures_retries_and_redaction(monkeypatch, codes):
    monkeypatch.setenv('SHUCE_OFFLINE', '0')
    monkeypatch.setattr(ai, 'config', lambda: {'key':'secret-example','base':'http://provider.test/v1','model':'test'})
    async def no_sleep(*a): pass
    monkeypatch.setattr(ai.asyncio, 'sleep', no_sleep)
    calls = []
    class Client:
        def __init__(self, **kw): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): pass
        async def post(self, url, **kw):
            code = codes[len(calls)]; calls.append(code)
            if code == 'timeout': raise httpx.ReadTimeout('secret-example')
            if code == 'json': return httpx.Response(200, content=b'secret-example', request=httpx.Request('POST',url))
            return httpx.Response(code,json={'choices':[{'message':{'content':'{"ok":true}'}}]},request=httpx.Request('POST',url))
    monkeypatch.setattr(ai.httpx, 'AsyncClient', Client)
    if codes[-1] == 200:
        _, trace = asyncio.run(ai.complete_json('system','user'))
    else:
        with pytest.raises(AnalysisError) as err:
            asyncio.run(ai.complete_json('system','user'))
        assert 'secret-example' not in str(err.value)
        trace = err.value.trace
    assert len(calls) == len(codes) == len(trace['attempts'])
    assert 'secret-example' not in str(trace)
