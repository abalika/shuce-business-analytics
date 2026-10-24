import asyncio
import pytest
from app import ai
from app.engine import AnalysisError
from tests.test_reviews import client


def test_offline_wins_over_existing_credentials(client, monkeypatch):
    monkeypatch.setenv('SHUCE_OFFLINE', '1')
    monkeypatch.setenv('OPENAI_API_KEY', 'should-never-be-used')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://provider.invalid/v1')
    monkeypatch.setenv('OPENAI_MODEL', 'configured-model')
    calls = []
    def network_forbidden(*a, **kw):
        calls.append(True)
        raise AssertionError('Offline mode attempted provider access')
    monkeypatch.setattr(ai.httpx, 'AsyncClient', network_forbidden)
    assert client.get('/api/status').json()['ai']['offline'] is True
    d = client.post('/api/demo/orders').json()
    r = client.post('/api/analyze', json={'dataset_id':d['id'], 'question':'净交易额按月趋势如何？'}).json()
    assert r['trace']['provider'] == 'rule'
    review = client.post('/api/ops/demo').json()
    brief = client.post('/api/ops/reviews/' + review['id'] + '/brief').json()
    assert brief['brief']['status'] == 'not_configured'
    assert review['totals']['actual_cents'] == brief['totals']['actual_cents'] == 286600
    with pytest.raises(AnalysisError, match='未配置'):
        asyncio.run(ai.complete_json('system','user'))
    assert calls == []


def test_leaving_offline_restores_configuration(monkeypatch):
    monkeypatch.delenv('SHUCE_OFFLINE', raising=False)
    monkeypatch.setenv('OPENAI_API_KEY', 'test-only')
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://provider.invalid/v1')
    monkeypatch.setenv('OPENAI_MODEL', 'test-model')
    assert ai.status()['configured'] is True
