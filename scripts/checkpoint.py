"""Version source independently of uncommitted Git; never archive secrets or user data."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ('app', 'web', 'scripts', 'tests', 'docs', 'examples', 'sql', 'licenses', '.github')
ROOT_FILES = ('README.md', 'HANDOFF.md', 'AGENTS.md', 'requirements.txt', 'requirements.lock.txt',
              '.env.example', '.gitignore', '.gitattributes', 'LICENSE', 'CHANGELOG.md', 'SECURITY.md', 'CONTRIBUTING.md', '启动数策.cmd', '停止数策.cmd', '数据维护.cmd', '离线演示.cmd')


def checkpoint(label):
    if not label or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-_' for c in label):
        raise ValueError('Checkpoint label must use lowercase letters, numbers, hyphens or underscores')
    target = ROOT / 'work' / 'checkpoints' / (label + '.zip')
    target.parent.mkdir(parents=True, exist_ok=True)
    files = [p for folder in FOLDERS for p in (ROOT / folder).rglob('*')
             if p.is_file() and not p.is_symlink() and '__pycache__' not in p.parts and p.suffix not in {'.pyc', '.zip'}]
    files += [ROOT / name for name in ROOT_FILES if (ROOT / name).is_file()]
    manifest = {}
    with zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            data = path.read_bytes()
            name = path.relative_to(ROOT).as_posix()
            archive.writestr(name, data)
            manifest[name] = hashlib.sha256(data).hexdigest()
    result = {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'files': manifest}
    target.with_suffix('.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'path': str(target), 'files': len(manifest)}, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('label')
    checkpoint(parser.parse_args().label)
