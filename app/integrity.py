"""Local artifact integrity; detects accidental changes, not a signed trust boundary."""
from contextlib import contextmanager
import hashlib
import logging
import re
from pathlib import Path
from .engine import AnalysisError


class IntegrityError(AnalysisError):
    pass


def artifact_paths(data, meta, review=False):
    identifier = meta['id']
    if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
        raise IntegrityError('数据标识损坏，请从经过校验的备份恢复。')
    stem = identifier + ('.review' if review else '')
    return {'original': Path(data) / (stem + '.raw'),
            'normalized': Path(data) / (stem + '.csv.gz'),
            'diagnostics': Path(data) / (stem + '-diagnostics.csv.gz')}


@contextmanager
def write_import_artifacts(data, meta, frame, diagnostics, blob, *, review=False):
    """Remove artifacts from a failed import, including when registration fails."""
    paths = artifact_paths(data, meta, review)
    try:
        frame.to_csv(paths['normalized'], index=False, compression='gzip')
        diagnostics.to_csv(paths['diagnostics'], index=False, compression='gzip')
        paths['original'].write_bytes(blob)
        meta['artifact_sha256'] = fingerprints(data, meta, review)
        yield
    except Exception:
        for path in paths.values():
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logging.getLogger('shuce').warning('import_artifact_cleanup_failed')
        raise


def file_hash(path):
    if path.is_symlink() or not path.is_file():
        raise IntegrityError('数据文件缺失或不是普通文件；请恢复备份或以新批次重导，旧报告仍保留。')
    try:
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest()
    except OSError:
        raise IntegrityError('数据文件无法读取；请检查本地文件权限或恢复备份。') from None


def fingerprints(data, meta, review=False):
    return {role: file_hash(path) for role, path in artifact_paths(data, meta, review).items()}


def verify_artifacts(data, meta, review=False):
    expected = meta.get('artifact_sha256')
    if not isinstance(expected, dict) or set(expected) != {'original', 'normalized', 'diagnostics'}:
        raise IntegrityError('历史数据尚未完成原件重放核查；请停止服务并运行 数据维护.cmd seal，再重新打开。')
    original_hash = meta['file_sha256'] if review else meta['quality']['file_sha256']
    observed = fingerprints(data, meta, review)
    if expected.get('original') != original_hash or observed != expected:
        raise IntegrityError('数据完整性校验失败，已停止使用可能被改动的文件；请恢复已校验备份或以新批次重导。旧快照未改写。')
    return observed
