#!/usr/bin/env python3
"""Inspect BBR, or enable it under the shared installer lock."""
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

KEY = 'net.ipv4.tcp_congestion_control'
AVAILABLE = 'net.ipv4.tcp_available_congestion_control'
CONFIG = Path('/etc/sysctl.d/99-vps-dr-bbr.conf')
MODULES = Path('/etc/modules-load.d/vps-dr-bbr.conf')
LOCK_PATH = Path('/run/lock/vps-dr.lock')
SYSCTL_DIRS = tuple(Path(p) for p in (
    '/usr/lib/sysctl.d', '/usr/local/lib/sysctl.d', '/run/sysctl.d', '/etc/sysctl.d'))
SYSCTL_CONF = Path('/etc/sysctl.conf')
HEADER = '# Managed by vps-dr: BBR\n'
SETTINGS = HEADER + KEY + '=bbr\n'
LOAD_MODULE = HEADER + 'tcp_bbr\n'


def run(*args):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f'BBR_COMMAND_FAILED: 执行 {args[0]} 失败，请检查命令、内核支持及系统权限。') from exc


def algorithm():
    current = run('sysctl', '-n', KEY)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,24}', current):
        raise RuntimeError('BBR_STATUS_FAILED: 无法识别本机 TCP 拥塞控制状态。')
    return current


def status():
    try:
        current = algorithm()
    except RuntimeError:
        return '状态不可用'
    return '已开启' if current == 'bbr' else f'未开启（{current}）'


def snapshot(path, expected):
    for parent in [path.parent, *path.parent.parents]:
        if parent.is_symlink():
            raise RuntimeError(f'BBR_CONFIG_CONFLICT: {parent} 是符号链接，请先核对路径。')
        if parent == path.parent and parent.exists():
            meta = parent.stat()
            if meta.st_uid != 0 or meta.st_mode & 0o022:
                raise RuntimeError(f'BBR_CONFIG_CONFLICT: {parent} 的所有者或写入权限不安全。')
    if path.is_symlink():
        raise RuntimeError(f'BBR_CONFIG_CONFLICT: {path} 是符号链接，已保留。')
    if not path.exists():
        return None
    meta = path.stat()
    if not stat.S_ISREG(meta.st_mode):
        raise RuntimeError(f'BBR_CONFIG_CONFLICT: {path} 不是普通文件。')
    data = path.read_bytes()
    if data != expected.encode() or meta.st_uid != 0 or meta.st_mode & 0o022:
        raise RuntimeError(f'BBR_CONFIG_CONFLICT: {path} 包含未知内容或不安全权限，已保留。')
    return data


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.vps-dr-bbr-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def check_overrides():
    # systemd sorts effective sysctl.d basenames; procps reads sysctl.conf last.
    effective = {}
    for directory in SYSCTL_DIRS:
        effective.update({p.name: p for p in directory.glob('*.conf')})
    candidates = [p for name, p in effective.items() if name > CONFIG.name]
    if SYSCTL_CONF.exists():
        candidates.append(SYSCTL_CONF)
    for path in candidates:
        for line in path.read_text(encoding='utf-8').splitlines():
            setting = line.split('#', 1)[0].split(';', 1)[0].strip()
            if '=' not in setting:
                continue
            key, value = (part.strip() for part in setting.split('=', 1))
            if key.lstrip('-').replace('/', '.') == KEY and value != 'bbr':
                raise RuntimeError(f'BBR_CONFIG_CONFLICT: {path} 会覆盖 BBR 设置，请先核对其中的 {KEY}。')


def enable():
    before = algorithm()
    previous = {MODULES: snapshot(MODULES, LOAD_MODULE), CONFIG: snapshot(CONFIG, SETTINGS)}
    check_overrides()
    if 'bbr' not in run('sysctl', '-n', AVAILABLE).split():
        try:
            run('modprobe', 'tcp_bbr')
        except RuntimeError as exc:
            raise RuntimeError('BBR_UNSUPPORTED: 当前内核无法加载 tcp_bbr，请检查内核模块或 VPS 虚拟化限制。') from exc
        if 'bbr' not in run('sysctl', '-n', AVAILABLE).split():
            raise RuntimeError('BBR_UNSUPPORTED: 当前内核未提供 BBR，无法开启。')
    changed_files = []
    runtime_attempted = False
    try:
        if before != 'bbr':
            runtime_attempted = True
            run('sysctl', '-w', KEY + '=bbr')
        if algorithm() != 'bbr':
            raise RuntimeError('BBR_VERIFY_FAILED: 内核未报告 BBR 已生效。')
        # Module loading precedes sysctl on boot. Repeat runs repair partial saves.
        for path, content in ((MODULES, LOAD_MODULE), (CONFIG, SETTINGS)):
            if previous[path] is None:
                atomic_write(path, content.encode())
                changed_files.append(path)
    except BaseException:
        failures = []
        for path in reversed(changed_files):
            try:
                path.unlink()
            except OSError:
                failures.append(str(path))
        if runtime_attempted:
            try:
                run('sysctl', '-w', KEY + '=' + before)
                if algorithm() != before:
                    failures.append('TCP 拥塞控制')
            except RuntimeError:
                failures.append('TCP 拥塞控制')
        if failures:
            raise RuntimeError('BBR_ROLLBACK_FAILED: 恢复失败，请人工核对 ' + '、'.join(failures))
        raise
    print('BBR_ENABLED: BBR 已开启，开机加载配置已保存。')
    print('设置用于后续新建 TCP 连接；已有连接使用的算法由内核和应用决定。')


def main():
    if sys.argv[1:] == ['status']:
        print(status())
        return
    if sys.argv[1:] != ['enable']:
        raise RuntimeError('BBR_USAGE: 请使用 status 查看状态，或 enable 开启 BBR。')
    if os.name != 'posix' or os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED: 开启 BBR 需要 Linux root 权限。')
    import fcntl
    fd = os.open(LOCK_PATH, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        meta = os.fstat(lock.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise RuntimeError('BBR_LOCK_UNSAFE: 安装锁文件的类型、所有者或权限不安全。')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('INSTALL_IN_PROGRESS: 正在安装、升级或操作节点，请稍后重试。') from exc
        enable()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, UnicodeError) as exc:
        print(f'BBR_FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
