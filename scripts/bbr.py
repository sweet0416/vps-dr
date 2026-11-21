#!/usr/bin/env python3
"""Inspect, enable and restore BBR under the shared installer lock."""
import json
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
ORIGINAL = Path('/var/lib/vps-dr/bbr-original.json')
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


def safe_path(path):
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
        return
    meta = path.stat()
    if not stat.S_ISREG(meta.st_mode):
        raise RuntimeError(f'BBR_CONFIG_CONFLICT: {path} 不是普通文件。')


def snapshot(path, expected):
    safe_path(path)
    if not path.exists():
        return None
    meta = path.stat()
    data = path.read_bytes()
    if data != expected.encode() or meta.st_uid != 0 or meta.st_mode & 0o022:
        raise RuntimeError(f'BBR_CONFIG_CONFLICT: {path} 包含未知内容或不安全权限，已保留。')
    return data


def atomic_write(path, data, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.vps-dr-bbr-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def managed_files():
    result = {}
    for path, expected in ((MODULES, LOAD_MODULE), (CONFIG, SETTINGS)):
        data = snapshot(path, expected)
        result[path] = (None if data is None else
                        {'content': data.decode(), 'mode': stat.S_IMODE(path.stat().st_mode)})
    return result


def load_original():
    safe_path(ORIGINAL)
    if not ORIGINAL.exists():
        return None
    meta = ORIGINAL.stat()
    if meta.st_uid != 0 or stat.S_IMODE(meta.st_mode) != 0o600 or meta.st_nlink != 1:
        raise RuntimeError('BBR_HISTORY_UNSAFE: BBR 原设置记录须为 root 所有的独立 0600 普通文件，已保留。')
    try:
        if meta.st_size > 16384:
            raise ValueError('oversized')
        state = json.loads(ORIGINAL.read_text(encoding='utf-8'))
        if (not isinstance(state, dict) or set(state) != {'schema', 'algorithm', 'config', 'modules'}
                or type(state['schema']) is not int or state['schema'] != 1):
            raise ValueError('schema')
        current = state['algorithm']
        if current is not None and (not isinstance(current, str) or
                not re.fullmatch(r'[a-zA-Z0-9_-]{1,24}', current)):
            raise ValueError('algorithm')
        for name, expected in (('config', SETTINGS), ('modules', LOAD_MODULE)):
            item = state[name]
            if item is None:
                continue
            if (not isinstance(item, dict) or set(item) != {'content', 'mode'}
                    or item['content'] != expected or type(item['mode']) is not int
                    or not 0 <= item['mode'] <= 0o777 or item['mode'] & 0o022):
                raise ValueError('snapshot')
        return state
    except (ValueError, TypeError, UnicodeError) as exc:
        raise RuntimeError('BBR_HISTORY_INVALID: BBR 原设置记录损坏或来源无法验证，已保留，请人工核对。') from exc


def save_original(state):
    safe_path(ORIGINAL)
    ORIGINAL.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    safe_path(ORIGINAL)
    atomic_write(ORIGINAL, (json.dumps(state, ensure_ascii=False, sort_keys=True) + '\n').encode(), 0o600)


def clear_original():
    load_original()  # Validate again before removing a root-only recovery record.
    ORIGINAL.unlink(missing_ok=True)
    sync_directory(ORIGINAL.parent)


def inspect():
    print('当前 TCP 算法：' + algorithm())
    available = run('sysctl', '-n', AVAILABLE).split()
    if not available or any(not re.fullmatch(r'[a-zA-Z0-9_-]{1,24}', item) for item in available):
        raise RuntimeError('BBR_STATUS_FAILED: 无法识别内核可用的 TCP 算法。')
    print('内核可用算法：' + '、'.join(available))
    files = managed_files()
    print('本项目 BBR 开机设置：' + ('已保存' if files[CONFIG] is not None else '未设置'))
    print('本项目 BBR 模块加载：' + ('已保存' if files[MODULES] is not None else '未设置'))
    original = load_original()
    if original is not None and original['algorithm'] is not None:
        print('原设置记录：可恢复（原算法 ' + original['algorithm'] + '）')
    else:
        print('原设置记录：无可用历史记录；不能推断开启 BBR 前使用的算法。')
    print('算法变更作用于后续新建 TCP 连接。')


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


def put_file(path, item):
    # Only this project's exact generated files may be replaced or removed.
    snapshot(path, LOAD_MODULE if path == MODULES else SETTINGS)
    if item is None:
        if path.exists():
            path.unlink()
            sync_directory(path.parent)
    else:
        atomic_write(path, item['content'].encode(), item['mode'])


def change(target, desired, before, previous, clear_history=False):
    """Journal exists before entry; interruptions leave it available for retry."""
    changed_files = []
    runtime_attempted = False
    history = load_original() if clear_history else None
    try:
        if before != target:
            runtime_attempted = True
            run('sysctl', '-w', KEY + '=' + target)
        if algorithm() != target:
            raise RuntimeError('BBR_VERIFY_FAILED: 内核未报告目标算法 ' + target + ' 已生效。')
        for path in (MODULES, CONFIG):
            if previous[path] != desired[path]:
                # Record before writing: a failed fsync may follow a completed replace.
                changed_files.append(path)
                put_file(path, desired[path])
        if clear_history and history is not None:
            clear_original()
    except BaseException:
        failures = []
        for path in reversed(changed_files):
            try:
                put_file(path, previous[path])
            except (OSError, RuntimeError):
                failures.append(str(path))
        if runtime_attempted:
            try:
                run('sysctl', '-w', KEY + '=' + before)
                if algorithm() != before:
                    failures.append('TCP 拥塞控制')
            except RuntimeError:
                failures.append('TCP 拥塞控制')
        if history is not None and not ORIGINAL.exists():
            try:
                save_original(history)
            except (OSError, RuntimeError):
                failures.append('BBR 原设置记录')
        if failures:
            raise RuntimeError('BBR_ROLLBACK_FAILED: 操作失败且回滚未完成，请人工核对 ' + '、'.join(failures))
        raise


def enable():
    before = algorithm()
    previous = managed_files()
    check_overrides()
    original = load_original()
    created = original is None
    if created:
        # A legacy BBR installation cannot tell us the pre-BBR algorithm.
        original = {'schema': 1, 'algorithm': before if before != 'bbr' else None,
                    'config': previous[CONFIG], 'modules': previous[MODULES]}
        save_original(original)
    try:
        if 'bbr' not in run('sysctl', '-n', AVAILABLE).split():
            try:
                run('modprobe', 'tcp_bbr')
            except RuntimeError as exc:
                raise RuntimeError('BBR_UNSUPPORTED: 当前内核无法加载 tcp_bbr，请检查内核模块或 VPS 虚拟化限制。') from exc
            if 'bbr' not in run('sysctl', '-n', AVAILABLE).split():
                raise RuntimeError('BBR_UNSUPPORTED: 当前内核未提供 BBR，无法开启。')
        desired = {MODULES: {'content': LOAD_MODULE, 'mode': 0o644},
                   CONFIG: {'content': SETTINGS, 'mode': 0o644}}
        # Keep already managed file modes; repeated calls only repair missing files.
        desired.update({p: value for p, value in previous.items() if value is not None})
        change('bbr', desired, before, previous)
    except BaseException as exc:
        # Retain history if rollback was incomplete. SIGKILL also leaves the journal.
        if created and not (isinstance(exc, RuntimeError) and 'BBR_ROLLBACK_FAILED:' in str(exc)):
            try:
                clear_original()
            except (OSError, RuntimeError) as cleanup:
                raise RuntimeError('BBR_ROLLBACK_FAILED: 原运行状态已回滚，但原设置记录清理失败，请保留记录后重试。') from cleanup
        raise
    print('BBR_ENABLED: BBR 已开启，开机加载配置已保存。')
    print('设置用于后续新建 TCP 连接；已有连接使用的算法由内核和应用决定。')


def restore():
    original = load_original()
    if original is None or original['algorithm'] is None:
        raise RuntimeError('BBR_NO_HISTORY: 没有开启 BBR 前的原设置记录，无法恢复原设置；如需关闭 BBR，请明确选择切换 cubic。')
    before = algorithm()
    previous = managed_files()
    target = original['algorithm']
    if target not in run('sysctl', '-n', AVAILABLE).split():
        raise RuntimeError('BBR_RESTORE_UNAVAILABLE: 内核当前未提供原算法 ' + target + '，已保留全部设置和历史记录。')
    desired = {MODULES: original['modules'], CONFIG: original['config']}
    change(target, desired, before, previous, clear_history=True)
    print('BBR_RESTORED: 已恢复原算法 ' + target + ' 和本项目原有开机配置，原设置记录已清除。')
    print('算法变更作用于后续新建 TCP 连接；其它系统配置保持原样。')


def cubic():
    before = algorithm()
    previous = managed_files()
    original = load_original()
    if 'cubic' not in run('sysctl', '-n', AVAILABLE).split():
        raise RuntimeError('BBR_CUBIC_UNAVAILABLE: 当前内核未提供 cubic，全部设置已保留。')
    change('cubic', {MODULES: None, CONFIG: None}, before, previous,
           clear_history=original is not None and original['algorithm'] is None)
    print('BBR_CUBIC: 已明确切换 cubic，并移除本项目 BBR 开机配置。')
    if original is not None and original['algorithm'] is not None:
        print('原设置记录已保留，仍可选择恢复原设置。')
    print('算法变更作用于后续新建 TCP 连接；重启后的算法由其它系统配置及内核默认值决定。')


def main():
    if sys.argv[1:] == ['status']:
        print(status())
        return
    if sys.argv[1:] == ['inspect']:
        inspect()
        return
    actions = {'enable': enable, 'restore': restore, 'cubic': cubic}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise RuntimeError('BBR_USAGE: 请使用 status 查看状态、inspect 查看详情、enable 开启、restore 恢复原设置，或 cubic 明确切换算法。')
    if os.name != 'posix' or os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED: 修改 TCP 拥塞控制设置需要 Linux root 权限。')
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
        actions[sys.argv[1]]()


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, UnicodeError) as exc:
        print(f'BBR_FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
