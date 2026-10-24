"""Backup/restore without importing main, loading model configuration or starting services."""
import io
import json
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from .engine import normalize, read_frame
from .integrity import IntegrityError, artifact_paths, file_hash, fingerprints, verify_artifacts
from .review_engine import normalize_source
from .version import VERSION

TABLES = {'datasets', 'analyses', 'reports', 'review_sources', 'reviews'}
FILE_NAME = re.compile(r'[0-9a-f]{32}(?:\.raw|\.csv\.gz|-diagnostics\.csv\.gz|\.review\.raw|\.review\.csv\.gz|\.review-diagnostics\.csv\.gz)')


def readonly(db):
    if not db.is_file() or db.is_symlink():
        raise IntegrityError('应用数据库缺失或不是普通文件。')
    return sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)


def inventory(data):
    """All DB references must resolve; orphan files are deliberately not backed up."""
    with closing(readonly(Path(data) / 'index.sqlite3')) as con:
        if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise IntegrityError('数据库完整性检查失败，请恢复已校验备份。')
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not names <= TABLES or not {'datasets', 'analyses', 'reports'} <= names:
            raise IntegrityError('数据库结构不在当前支持范围。')
        datasets = dict(con.execute('SELECT id,metadata FROM datasets'))
        sources = dict(con.execute('SELECT id,metadata FROM review_sources')) if 'review_sources' in names else {}
        entries = []
        for review, records in [(False, datasets), (True, sources)]:
            for key, value in records.items():
                meta = json.loads(value)
                if meta.get('id') != key:
                    raise IntegrityError('来源标识与数据库不一致。')
                entries.append((review, meta))
        analyses = {r[0] for r in con.execute('SELECT id FROM analyses')}
        for (dataset,) in con.execute('SELECT dataset_id FROM analyses'):
            if dataset not in datasets:
                raise IntegrityError('分析引用的数据集缺失。')
        for (analysis,) in con.execute('SELECT analysis_id FROM reports'):
            if analysis not in analyses:
                raise IntegrityError('报告引用的分析快照缺失。')
        review_rows = dict(con.execute('SELECT id,result FROM reviews')) if 'reviews' in names else {}
        for identifier, value in review_rows.items():
            result = json.loads(value)
            if result.get('id') != identifier or set(result.get('sources', {})) != {'sales', 'targets', 'operations'}:
                raise IntegrityError('复盘快照结构损坏。')
            for kind, embedded in result['sources'].items():
                actual = json.loads(sources[embedded['id']]) if embedded.get('id') in sources else None
                if not actual or actual['kind'] != kind or actual['file_sha256'] != embedded['file_sha256'] or actual['mapping'] != embedded['mapping']:
                    raise IntegrityError('复盘引用的数据源缺失或与快照不一致。')
            for link in [result.get('parent_review_id'), result.get('followup', {}).get('previous_review_id')]:
                if link and link not in review_rows:
                    raise IntegrityError('复盘关联的历史快照缺失。')
        counts = {name: con.execute('SELECT COUNT(*) FROM ' + name).fetchone()[0] for name in sorted(names)}
    return entries, counts


def replay(data, meta, review):
    """For pre-v2.2 data, prove artifacts match original normalization before sealing."""
    paths = artifact_paths(data, meta, review)
    expected = meta['file_sha256'] if review else meta['quality']['file_sha256']
    if file_hash(paths['original']) != expected:
        raise IntegrityError('原件指纹不匹配，拒绝为历史数据登记基线。')
    raw = read_frame(paths['original'].read_bytes(), meta['filename'])
    if review:
        fresh, quality, diagnostics = normalize_source(raw, meta['kind'], meta['mapping'])
        columns = ['source_row', 'kind', 'reason', 'original_values']
        quality_keys = ['input_rows', 'valid_rows', 'rejected_rows', 'duplicate_participants', 'blocking', 'key_fields']
    else:
        if meta.get('schema_version', 1) < 2:
            raise IntegrityError('旧单表数据先执行v1.2证据字段升级，不可直接登记指纹。')
        diagnostics = []
        fresh, quality = normalize(raw, meta['mode'], meta['mapping'], cancellation_prefix=meta.get('cancellation_prefix'), diagnostics=diagnostics)
        columns = ['source_row', 'kind', 'reason', 'date_raw', 'dimension_raw', 'entity_raw', 'original_values']
        quality_keys = ['input_rows', 'valid_rows', 'rejected_rows', 'duplicate_rows']
    for role, frame in [('normalized', fresh), ('diagnostics', pd.DataFrame(diagnostics, columns=columns))]:
        file_hash(paths[role])
        # Compare every field including IDs/quantities as text, without numeric coercion.
        observed = pd.read_csv(paths[role], dtype=str, keep_default_na=False)
        expected_frame = pd.read_csv(io.StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=False)
        try:
            pd.testing.assert_frame_equal(observed, expected_frame)
        except AssertionError:
            raise IntegrityError('历史' + role + '与原件重放结果不同，拒绝登记基线。') from None
    if any(quality[k] != meta['quality'][k] for k in quality_keys):
        raise IntegrityError('历史质量计数与原件重放不同。')
    return fingerprints(data, meta, review)


def audit(data):
    entries, counts = inventory(data)
    for review, meta in entries:
        verify_artifacts(data, meta, review)
    return {'ok': True, 'source_batches': len(entries), 'files': 1 + len(entries) * 3, 'counts': counts}


def seal(data):
    """Offline migration, no raw/file/snapshot rewrites; all metadata updates commit together."""
    data = Path(data)
    entries, counts = inventory(data)
    changes = []
    for review, meta in entries:
        if meta.get('artifact_sha256'):
            verify_artifacts(data, meta, review)
        else:
            changes.append((review, meta, replay(data, meta, review)))
    if changes:
        before = data.parent / 'work' / ('integrity-before-' + uuid.uuid4().hex + '.sqlite3')
        before.parent.mkdir(parents=True, exist_ok=True)
        with closing(readonly(data / 'index.sqlite3')) as src, closing(sqlite3.connect(before)) as dst:
            src.backup(dst)
        with closing(sqlite3.connect(data / 'index.sqlite3')) as con, con:
            for review, meta, hashes in changes:
                table = 'review_sources' if review else 'datasets'
                old = con.execute('SELECT metadata FROM ' + table + ' WHERE id=?', (meta['id'],)).fetchone()
                if not old or json.loads(old[0]) != meta:
                    raise IntegrityError('登记期间来源元数据变化，请停止服务后重试。')
                if hashes != fingerprints(data, meta, review):
                    raise IntegrityError('登记期间文件变化，已回滚登记。')
                con.execute('UPDATE ' + table + ' SET metadata=? WHERE id=?',
                            (json.dumps({**meta, 'artifact_sha256': hashes}, ensure_ascii=False), meta['id']))
    return {**audit(data), 'sealed_batches': len(changes), 'snapshot_results_rewritten': False}


def make_backup(data, destination):
    data, destination = Path(data).resolve(), Path(destination).resolve()
    if destination.exists() or destination.is_relative_to(data):
        raise IntegrityError('备份必须使用数据目录以外的新文件名，不覆盖旧备份。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    # DB first: later committed imports are excluded; referenced artifacts are immutable.
    with tempfile.TemporaryDirectory(prefix='shuce-backup-', dir=destination.parent) as work:
        stage = Path(work)
        with closing(readonly(data / 'index.sqlite3')) as src, closing(sqlite3.connect(stage / 'index.sqlite3')) as dst:
            src.backup(dst)
        entries, counts = inventory(stage)
        names = {'index.sqlite3': stage / 'index.sqlite3'}
        for review, meta in entries:
            verify_artifacts(data, meta, review)
            names.update({p.name: p for p in artifact_paths(data, meta, review).values()})
        archive_path = stage / 'backup.zip'
        manifest = {'format': 'shuce-data-backup-1', 'created_at': datetime.now(timezone.utc).isoformat(),
                    'app_version': VERSION, 'counts': counts, 'files': {},
                    'excludes': ['model_config', 'browser_drafts', 'dependencies', 'DSH']}
        with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in sorted(names.items()):
                archive.write(path, 'data/' + name)
                manifest['files']['data/' + name] = {'sha256': file_hash(path), 'bytes': path.stat().st_size}
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
        # Validate copied bytes + DB-to-file dependencies, not just source files or ZIP CRC.
        verify_backup(archive_path)
        os.link(archive_path, destination)  # atomic create; FileExistsError never overwrites
    return {'ok': True, 'path': str(destination), 'bytes': destination.stat().st_size,
            'sha256': file_hash(destination), 'counts': counts}


def _unpack(archive_path, stage):
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        if len(names) > 50000 or len(set(names)) != len(names) or sum(i.file_size for i in infos) > 4 * 1024**3:
            raise IntegrityError('备份过大或存在重复条目。')
        if any(n != 'manifest.json' and n != 'data/index.sqlite3' and not (n.startswith('data/') and FILE_NAME.fullmatch(n[5:])) for n in names):
            raise IntegrityError('备份含不允许的路径或配置，拒绝解包。')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size > 16 * 1024**2:
            raise IntegrityError('备份缺少校验清单或清单过大。')
        try:
            manifest = json.loads(archive.read('manifest.json'))
        except (ValueError, UnicodeError):
            raise IntegrityError('备份校验清单无法解析。') from None
        if not isinstance(manifest, dict):
            raise IntegrityError('备份校验清单结构无效。')
        files = manifest.get('files', {})
        if not isinstance(files, dict) or manifest.get('format') != 'shuce-data-backup-1' or set(files) != set(names) - {'manifest.json'} or 'data/index.sqlite3' not in files:
            raise IntegrityError('备份格式或文件清单不完整。')
        for info in infos:
            if info.filename == 'manifest.json':
                continue
            expected = files[info.filename]
            if (not isinstance(expected, dict) or type(expected.get('bytes')) is not int
                    or expected['bytes'] != info.file_size or not isinstance(expected.get('sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', expected['sha256'])):
                raise IntegrityError('备份文件大小或指纹清单无效。')
            path = stage / info.filename
            path.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, path.open('xb') as target:
                shutil.copyfileobj(source, target)
            if path.stat().st_size != expected['bytes'] or file_hash(path) != expected['sha256']:
                raise IntegrityError('备份内容与校验清单不一致，拒绝恢复。')
    report = audit(stage / 'data')
    entries, _ = inventory(stage / 'data')
    expected_names = {'data/index.sqlite3'} | {'data/' + p.name for review, meta in entries for p in artifact_paths(stage / 'data', meta, review).values()}
    if expected_names != set(files) or report['counts'] != manifest['counts']:
        raise IntegrityError('备份文件或记录数与数据库引用范围不一致。')
    return report


def verify_backup(archive_path):
    with tempfile.TemporaryDirectory(prefix='shuce-verify-') as work:
        return _unpack(Path(archive_path), Path(work))


def restore_backup(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise IntegrityError('恢复目标已经存在；只能恢复到全新目录，不覆盖现有数据。')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='shuce-restore-', dir=destination.parent) as work:
        stage = Path(work)
        report = _unpack(Path(archive_path), stage)
        os.rename(stage / 'data', destination)
    return {**report, 'restored_data_dir': str(destination), 'replaced_existing_data': False}
