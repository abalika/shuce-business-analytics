"""Local data maintenance; no model calls or gateway lifecycle operations."""
import argparse
import json
import socket
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.maintenance import audit, seal, make_backup, verify_backup, restore_backup


def run():
    parser = argparse.ArgumentParser(description='数策数据核查、备份与恢复（不会启动模型或DSH网关）')
    parser.add_argument('action', choices=['check', 'seal', 'backup', 'verify', 'restore'])
    parser.add_argument('--data', type=Path, default=ROOT / 'data', help='来源/核查数据目录')
    parser.add_argument('--archive', type=Path, help='备份输出或校验/恢复输入ZIP')
    parser.add_argument('--to', type=Path, help='恢复到尚不存在的数据目录；绝不覆盖原目录')
    args = parser.parse_args()
    try:
        if args.action == 'seal':
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1', 8765)) == 0:
                    raise ValueError('先运行 停止数策.cmd 再登记历史数据指纹。')
            result = seal(args.data)
        elif args.action == 'check':
            result = audit(args.data)
        elif args.action == 'backup':
            target = args.archive or ROOT / 'backups' / ('shuce-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.zip')
            result = make_backup(args.data, target)
        elif args.action == 'verify':
            if not args.archive:
                raise ValueError('verify需要 --archive 指定备份ZIP。')
            result = verify_backup(args.archive)
        else:
            if not args.archive or not args.to:
                raise ValueError('restore需要 --archive 和 --to（全新数据目录）。')
            result = restore_backup(args.archive, args.to)
        print(json.dumps({'action': args.action, **result}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        # Never dump traceback, credentials or source records to a terminal/log.
        from app.integrity import IntegrityError
        message = str(exc) if isinstance(exc, (IntegrityError, ValueError)) else '维护操作失败；请检查备份格式、磁盘空间和读写权限。现有数据未被替换。'
        print(json.dumps({'ok': False, 'action': args.action, 'detail': message}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(run())
