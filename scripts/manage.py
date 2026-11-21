#!/usr/bin/env python3
"""Transactional management-only upgrades. Caller must hold vps-dr.lock."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile

FILES = ('VERSION', 'bootstrap.sh', 'install.sh', 'preflight.sh', 'menu.sh',
         'health-check.sh', 'export-client.sh', 'uninstall.sh', 'scripts/dr.py',
         'scripts/manage.py', 'scripts/bbr.py', 'scripts/caidan.sh',
         'scripts/traffic.py', 'scripts/backup.py', 'scripts/observe.py',
         'scripts/latest.py', 'scripts/update.py', 'scripts/locked.py',
         'config/defaults.env', 'config/xray.service',
         'config/vps-dr-traffic.service', 'config/vps-dr-traffic.timer',
         'config/legacy-v1.json')


def normalize(name, text):
    # Recognize only the documented DOMAIN assignment repair, never other edits.
    if name == 'scripts/dr.py':
        text = re.sub(r'^DOMAIN = "[A-Za-z0-9.-]*"$', 'DOMAIN = ""', text, flags=re.M)
    elif name == 'config/defaults.env':
        text = re.sub(r'^DOMAIN=[A-Za-z0-9.-]*$', 'DOMAIN=', text, flags=re.M)
    return text


def digest(name, text):
    return hashlib.sha256(normalize(name, text).encode()).hexdigest()


def safe_tree(path):
    if path.is_symlink() or not path.is_dir():
        raise ValueError('管理目录不是普通目录')
    for p in [path, *path.rglob('*')]:
        meta = p.lstat()
        if p.is_symlink() or (not p.is_file() and not p.is_dir()):
            raise ValueError('管理目录包含链接或特殊文件')
        if os.name != 'nt' and (meta.st_uid != 0 or meta.st_mode & 0o022):
            raise ValueError('管理目录必须由 root 所有且禁止其他用户写入')


def verify(source, target):
    safe_tree(target)
    legacy = json.loads((source / 'config/legacy-v1.json').read_text(encoding='utf-8'))
    found = 0
    for p in target.rglob('*'):
        if p.is_dir():
            continue
        name = p.relative_to(target).as_posix()
        if name not in FILES:
            raise ValueError(f'存在未知管理文件：{name}，已保留，请先核对')
        text = p.read_text(encoding='utf-8')
        current = source / name
        if current.is_file() and text == current.read_text(encoding='utf-8'):
            found += 1
            continue
        if digest(name, text) in legacy.get(name, []):
            found += 1
            continue
        raise ValueError(f'管理文件有未知修改：{name}，已保留，请先核对')
    if not found:
        raise ValueError('管理目录为空或无法识别，未覆盖')


def shortcut(source, target, check_only=False):
    target = Path(target).absolute()
    expected = (Path(source) / 'scripts/caidan.sh').read_bytes()
    for parent in (target.parent, *target.parent.parents):
        if parent.is_symlink():
            raise ValueError(f'快捷命令目录包含符号链接：{parent}')
    if target.parent.exists():
        meta = target.parent.stat()
        if os.name != 'nt' and (meta.st_uid != 0 or meta.st_mode & 0o022):
            raise ValueError('快捷命令目录必须由 root 所有且禁止其他用户写入')
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise ValueError(f'快捷命令路径被其他文件或链接占用：{target}')
    if target.exists():
        meta = target.stat()
        if target.read_bytes() != expected or (os.name != 'nt' and
                                               (meta.st_uid != 0 or meta.st_mode & 0o022)):
            raise ValueError(f'已有未知来源的 caidan 命令，已保留：{target}')
        if meta.st_mode & 0o777 == 0o755:
            return
    if check_only:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.vps-dr-caidan-', dir=target.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(expected)
            stream.flush()
            os.fsync(stream.fileno())
        staged.chmod(0o755)
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    print('CAIDAN_READY: 已配置快捷命令，下次输入 caidan 即可打开中文菜单。')


def recover(target):
    # Fixed sibling name: no untrusted paths are read from a journal.
    pending = target.with_name('.vps-dr.previous')
    if pending.exists() or pending.is_symlink():
        safe_tree(pending)
        if not target.exists():
            os.replace(pending, target)
            print('已恢复上次中断的管理脚本升级。')
        else:
            safe_tree(target)
            backup = Path(tempfile.mkdtemp(prefix='.vps-dr.backup-', dir=target.parent))
            backup.rmdir()
            os.replace(pending, backup)
            print(f'上次升级备份：{backup}')


def upgrade(source, target):
    source, target = Path(source).resolve(), Path(target).absolute()
    recover(target)
    if target.exists() or target.is_symlink():
        verify(source, target)
        if all((target / n).is_file() and (target / n).read_bytes() == (source / n).read_bytes()
               for n in FILES):
            print('MANAGEMENT_CURRENT: 管理脚本已是当前版本。')
            return
    stage = Path(tempfile.mkdtemp(prefix='.vps-dr.stage-', dir=target.parent))
    pending = target.with_name('.vps-dr.previous')
    try:
        for name in FILES:
            dest = stage / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, dest)
            dest.chmod(0o755 if name.endswith(('.sh', '.py')) else 0o644)
        stage.chmod(0o755)
        if target.exists():
            os.replace(target, pending)
        try:
            os.replace(stage, target)
        except BaseException:
            if pending.exists() and not target.exists():
                os.replace(pending, target)
            raise
        recover(target)
        print('MANAGEMENT_UPDATED: 管理脚本升级完成；UUID、节点配置和服务均已保留。')
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def rollback(source, backup, target):
    source, backup, target = Path(source), Path(backup).absolute(), Path(target).absolute()
    if backup.parent != target.parent or not backup.name.startswith('.vps-dr.backup-'):
        raise ValueError('请选择升级输出的同目录备份路径')
    recover(target)
    verify(source, backup)
    safe_tree(target)
    pending = target.with_name('.vps-dr.previous')
    os.replace(target, pending)
    try:
        os.replace(backup, target)
    except BaseException:
        os.replace(pending, target)
        raise
    recover(target)
    print('MANAGEMENT_ROLLED_BACK: 已恢复备份管理脚本；节点配置、UUID 与服务未改动。')


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'recover':
            recover(Path(sys.argv[2]))
        elif sys.argv[1] == 'check':
            if Path(sys.argv[3]).exists():
                verify(Path(sys.argv[2]), Path(sys.argv[3]))
        elif sys.argv[1] == 'upgrade':
            upgrade(sys.argv[2], sys.argv[3])
        elif sys.argv[1] == 'rollback':
            rollback(Path(__file__).resolve().parents[1], sys.argv[2], sys.argv[3])
        elif sys.argv[1] in ('shortcut-check', 'shortcut-install'):
            shortcut(sys.argv[2], sys.argv[3], check_only=sys.argv[1] == 'shortcut-check')
        else:
            raise ValueError('未知管理操作')
    except (OSError, ValueError) as exc:
        sys.exit(f'MANAGEMENT_CONFLICT: {exc}')
