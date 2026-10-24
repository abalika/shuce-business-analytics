import io
import json
import sqlite3
import zipfile
from pathlib import Path
import pandas as pd
import pytest
from app import main
from app.integrity import IntegrityError, artifact_paths
from app.maintenance import audit, seal, make_backup, verify_backup, restore_backup
from tests.test_reviews import client


def legacy(client):
    return client.post('/api/demo/orders').json()


@pytest.mark.parametrize('review', [False, True])
@pytest.mark.parametrize('role', ['original', 'normalized', 'diagnostics'])
@pytest.mark.parametrize('damage', ['changed', 'missing'])
def test_source_damage_rejected_and_snapshot_preserved(client, review, role, damage):
    if review:
        snapshot = client.post('/api/ops/demo').json()
        meta = snapshot['sources']['sales']
        detail = f"/api/ops/reviews/{snapshot['id']}/evidence/E1"
        original = '/api/ops/sources/' + meta['id'] + '/original'
        diagnostic = '/api/ops/sources/' + meta['id'] + '/diagnostics'
        snapshot_url = '/api/ops/reviews/' + snapshot['id']
    else:
        meta = legacy(client)
        snapshot = client.post('/api/analyze', json={'dataset_id': meta['id'], 'plan': {'operation': 'overview', 'metric': 'amount'}}).json()
        detail = '/api/datasets/' + meta['id']  # warm-cache path must still verify
        original = detail + '/original'
        diagnostic = detail + '/diagnostics'
        snapshot_url = '/api/analyses/' + snapshot['id'] + '/export'
    assert client.get(detail).status_code == 200
    path = artifact_paths(main.DATA, meta, review)[role]
    if damage == 'missing':
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b'tampered')
    for url in [detail, original, diagnostic]:
        response = client.get(url)
        assert response.status_code == 409, response.text
        assert response.json()['code'] == 'data_integrity_failed'
        assert 'Traceback' not in response.text
    assert client.get(snapshot_url).json() == snapshot
    with pytest.raises(IntegrityError):
        audit(main.DATA)


def test_backup_restore_reopens_reports_evidence_and_export(client, tmp_path, monkeypatch):
    r = client.post('/api/ops/demo').json()
    saved = client.post(f"/api/ops/reviews/{r['id']}/report", json={'facts_note': '授权试用前仅模拟核查', 'next_check': '核对原始记录'}).json()
    d = legacy(client)
    a = client.post('/api/analyze', json={'dataset_id': d['id'], 'plan': {'operation': 'overview', 'metric': 'amount'}}).json()
    old_report = client.post('/api/reports', json={'analysis_id': a['id'], 'title': '旧模式报告'}).json()
    before_html = client.get(old_report['url']).text
    (main.DATA / '.env').write_text('SHOULD_NOT_BE_BACKED_UP')
    (main.DATA / 'orphan.raw').write_text('not referenced')
    backup = tmp_path.parent / (tmp_path.name + '-backup.zip')
    result = make_backup(main.DATA, backup)
    assert result['counts']['reviews'] == 2 and verify_backup(backup)['source_batches'] == 4
    with zipfile.ZipFile(backup) as z:
        assert not any('env' in n or 'orphan' in n for n in z.namelist())
        assert json.loads(z.read('manifest.json'))['app_version'] == main.app.version
    restored = tmp_path.parent / (tmp_path.name + '-restored')
    restore_backup(backup, restored)
    monkeypatch.setattr(main, 'DATA', restored)
    monkeypatch.setattr(main, 'DB', restored / 'index.sqlite3')
    main.load_frame.cache_clear()
    assert client.get('/api/ops/reviews/' + saved['id']).json() == saved
    assert client.get(f"/api/ops/reviews/{saved['id']}/evidence/E1").json()['total'] == 149
    assert client.get(f"/api/ops/reviews/{saved['id']}/xlsx").status_code == 200
    assert client.get(old_report['url']).text == before_html
    assert client.get('/api/analyses/' + a['id'] + '/export').json() == a
    assert client.get('/api/datasets/' + d['id']).status_code == 200
    with pytest.raises(IntegrityError):
        restore_backup(backup, restored)
    with pytest.raises(IntegrityError):
        make_backup(restored, backup)


def test_backup_concurrent_import_after_db_snapshot_is_excluded(client, tmp_path, monkeypatch):
    import app.maintenance as maintenance
    legacy(client)
    real_inventory = maintenance.inventory
    inserted = []
    def hook(data):
        if Path(data) != main.DATA and not inserted:
            inserted.append(client.post('/api/demo/content').json()['id'])
        return real_inventory(data)
    monkeypatch.setattr(maintenance, 'inventory', hook)
    backup = tmp_path.parent / (tmp_path.name + '-concurrent.zip')
    result = make_backup(main.DATA, backup)
    assert result['counts']['datasets'] == 1 and audit(main.DATA)['counts']['datasets'] == 2
    with zipfile.ZipFile(backup) as z:
        assert all(inserted[0] not in n for n in z.namelist())


def test_seal_replays_original_and_never_changes_snapshots(client):
    r = client.post('/api/ops/demo').json()
    d = legacy(client)
    with main.connect() as con:
        for table in ['datasets', 'review_sources']:
            for identifier, blob in con.execute('SELECT id,metadata FROM ' + table).fetchall():
                meta = json.loads(blob); meta.pop('artifact_sha256')
                con.execute('UPDATE ' + table + ' SET metadata=? WHERE id=?', (json.dumps(meta), identifier))
    assert client.get('/api/datasets/' + d['id']).status_code == 409
    result = seal(main.DATA)
    assert result['sealed_batches'] == 4
    assert client.get('/api/ops/reviews/' + r['id']).json() == r
    assert seal(main.DATA)['sealed_batches'] == 0
    assert client.get('/api/datasets/' + d['id']).status_code == 200


def test_seal_cannot_bless_modified_normalized_file(client):
    d = legacy(client)
    meta = dict(d); meta.pop('artifact_sha256')
    with main.connect() as con:
        con.execute('UPDATE datasets SET metadata=? WHERE id=?', (json.dumps(meta), d['id']))
    path = artifact_paths(main.DATA, meta)['normalized']
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    df.loc[0, 'amount'] = '999999'
    df.to_csv(path, index=False, compression='gzip')
    with pytest.raises(IntegrityError, match='原件重放'):
        seal(main.DATA)
    assert 'artifact_sha256' not in main.get_dataset(d['id'])


@pytest.mark.parametrize('mode', ['bytes', 'manifest_rewritten', 'path', 'duplicate', 'missing',
                                 'manifest_structure', 'manifest_oversized', 'manifest_file_size'])
def test_bad_backup_never_creates_restore_destination(client, tmp_path, mode):
    r = client.post('/api/ops/demo').json()
    backup = tmp_path.parent / (tmp_path.name + '-valid.zip')
    make_backup(main.DATA, backup)
    with zipfile.ZipFile(backup) as z:
        entries = {n: z.read(n) for n in z.namelist()}
    key = next(n for n in entries if n.endswith('.review.raw'))
    if mode in {'bytes', 'manifest_rewritten'}:
        entries[key] += b'bad'
        if mode == 'manifest_rewritten':
            import hashlib
            m = json.loads(entries['manifest.json'])
            m['files'][key] = {'sha256': hashlib.sha256(entries[key]).hexdigest(), 'bytes': len(entries[key])}
            entries['manifest.json'] = json.dumps(m).encode()
    elif mode == 'path':
        entries['data/../../outside.txt'] = b'bad'
    elif mode == 'missing':
        del entries[key]
    elif mode == 'manifest_structure':
        entries['manifest.json'] = b'[]'
    elif mode == 'manifest_oversized':
        entries['manifest.json'] = b' ' * (16 * 1024**2 + 1)
    elif mode == 'manifest_file_size':
        m = json.loads(entries['manifest.json'])
        m['files'][key]['bytes'] = 'invalid'
        entries['manifest.json'] = json.dumps(m).encode()
    bad = tmp_path.parent / (tmp_path.name + '-bad.zip')
    with zipfile.ZipFile(bad, 'w') as z:
        for name, blob in entries.items():
            z.writestr(name, blob)
        if mode == 'duplicate':
            with pytest.warns(UserWarning):
                z.writestr(key, b'duplicate')
    destination = tmp_path.parent / (tmp_path.name + '-must-not-exist')
    with pytest.raises(IntegrityError):
        restore_backup(bad, destination)
    assert not destination.exists()
    assert client.get('/api/ops/reviews/' + r['id']).json() == r


def test_dangling_source_reference_blocks_backup(client, tmp_path):
    r = client.post('/api/ops/demo').json()
    with main.connect() as con:
        con.execute('DELETE FROM review_sources WHERE id=?', (r['sources']['sales']['id'],))
    destination = tmp_path.parent / (tmp_path.name + '-must-not-save.zip')
    with pytest.raises(IntegrityError, match='引用'):
        make_backup(main.DATA, destination)
    assert not destination.exists()
