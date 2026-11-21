#!/usr/bin/env python3
"""Root-private node backups and journaled restoration of the fixed project profile."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import secrets
import socket
import stat
import subprocess
import sys
import tempfile
import time

sys.dont_write_bytecode = True
import dr

HERE = Path(__file__).resolve().parents[1]
STATE = Path('/etc/vps-dr/state.json')
CONFIG = Path('/usr/local/etc/xray/config.json')
BINARY = Path('/usr/local/bin/xray')
UNIT_PATH = Path('/etc/systemd/system/xray.service')
UNIT = 'xray.service'
BACKUPS = Path('/var/lib/vps-dr/backups')
LOCK_PATH = Path('/run/lock/vps-dr.lock')
JOURNAL_NAME = '.restore-journal.json'
LIMIT = 1024 * 1024
SCHEMA = 'vps-dr-backup-v1'


def fail(code, message):
    raise RuntimeError(f'{code}: {message}')


def run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, check=False, timeout=25)
    except (OSError, subprocess.SubprocessError) as exc:
        fail('BACKUP_COMMAND_FAILED', '无法执行本机检查或服务操作，请检查 systemd 和 Xray。')


def safe_parent(path):
    if not path.is_absolute() or '..' in path.parts:
        fail('BACKUP_PATH_UNSAFE', '请使用不含上级跳转的绝对路径。')
    for parent in (path.parent, *path.parent.parents):
        if parent.is_symlink():
            fail('BACKUP_PATH_UNSAFE', '路径包含符号链接，已停止操作。')
    meta = path.parent.stat()
    if not stat.S_ISDIR(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
        fail('BACKUP_PATH_UNSAFE', '文件所在目录必须由 root 所有且禁止其他用户写入。')


def read_private(path, private=True):
    safe_parent(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        meta = os.fstat(stream.fileno())
        mask = 0o077 if private else 0o022
        if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & mask
                or meta.st_nlink != 1 or meta.st_size > LIMIT):
            fail('BACKUP_FILE_UNSAFE', '文件类型、所有者、权限或大小不安全，已停止操作。')
        content = stream.read(LIMIT + 1)
        if len(content) > LIMIT:
            fail('BACKUP_FILE_UNSAFE', '文件超过大小限制，已停止操作。')
        return content


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write(path, data):
    safe_parent(path)
    if path.is_symlink():
        fail('BACKUP_PATH_UNSAFE', '目标为符号链接，已停止操作。')
    fd, name = tempfile.mkstemp(prefix='.vps-dr-private-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        sync_directory(path.parent)
    finally:
        Path(name).unlink(missing_ok=True)


def prepare_directory():
    # Existing permissions are checked, rather than silently making a public directory private.
    for directory in (BACKUPS.parent, BACKUPS):
        if not directory.exists() and not directory.is_symlink():
            safe_parent(directory)
            directory.mkdir(mode=0o700)
            sync_directory(directory.parent)
        safe_parent(directory / 'probe')
        if directory.stat().st_mode & 0o077:
            fail('BACKUP_PATH_UNSAFE', '备份目录必须为 root 专用目录，权限应为 700。')


def defaults():
    values = {}
    for line in (HERE / 'config/defaults.env').read_text(encoding='utf-8').splitlines():
        key, _, value = line.partition('=')
        if re.fullmatch(r'XRAY_(?:VERSION|BINARY_SHA256_(?:AMD64|ARM64))', key):
            values[key] = value
    return values


def validate_pair(files):
    if not isinstance(files, dict) or set(files) != {'state.json', 'config.json'}:
        fail('BACKUP_INVALID', '备份只能包含节点状态和配置。')
    try:
        if not all(isinstance(value, str) and len(value.encode('utf-8')) <= 64 * 1024 for value in files.values()):
            raise ValueError
        state = json.loads(files['state.json'])
        config = json.loads(files['config.json'])
        if (not isinstance(state, dict) or set(state) != {'architecture', 'uuid', 'xray_version', 'deployment_version'}
                or state['architecture'] != 'direct-xray-v1'
                or not isinstance(state['xray_version'], str)
                or not re.fullmatch(r'v\d+\.\d+\.\d+', state['xray_version'])
                or not isinstance(state['deployment_version'], str)
                or not re.fullmatch(r'\d+\.\d+\.\d+(?:-dev)?', state['deployment_version'])
                or config != dr.profile(state['uuid'])):
            raise ValueError
    except (ValueError, KeyError, TypeError, SystemExit):
        fail('BACKUP_INVALID', '备份状态、UUID、版本或 Xray 配置不匹配，无法恢复。')
    return state


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')


def envelope(files):
    validate_pair(files)
    value = {'schema': SCHEMA, 'created_at': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
             'machine': platform.machine(), 'files': files}
    return {**value, 'sha256': hashlib.sha256(canonical(value)).hexdigest()}


def validate_envelope(value):
    if not isinstance(value, dict) or set(value) != {'schema', 'created_at', 'machine', 'files', 'sha256'}:
        fail('BACKUP_INVALID', '备份格式不受支持或包含额外内容。')
    body = {key: content for key, content in value.items() if key != 'sha256'}
    if (value['schema'] != SCHEMA or not isinstance(value['created_at'], str)
            or not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', value['created_at'])
            or value['machine'] != platform.machine() or value['machine'] not in {'x86_64', 'aarch64'}
            or value['sha256'] != hashlib.sha256(canonical(body)).hexdigest()):
        fail('BACKUP_INVALID', '备份校验失败、架构不同或格式不兼容。')
    validate_pair(value['files'])
    return value


def load_envelope(path):
    try:
        return validate_envelope(json.loads(read_private(path).decode('utf-8')))
    except (ValueError, UnicodeError):
        fail('BACKUP_INVALID', '备份不是有效的 UTF-8 JSON 文件。')


def snapshot():
    files = {'state.json': read_private(STATE).decode('utf-8'),
             'config.json': read_private(CONFIG).decode('utf-8')}
    return envelope(files)


def service_active():
    result = run('systemctl', 'show', '--property=ActiveState', '--value', UNIT)
    state = result.stdout.strip()
    if result.returncode or state not in {'active', 'inactive'}:
        fail('BACKUP_SERVICE_UNHEALTHY', '服务处于失败或过渡状态，请先检查运行状态和故障日志。')
    return state == 'active'


def trusted_program(check_config=True):
    # The updater verifies either the pinned official binary or the private manifest
    # created after an official archive checksum check. It never trusts a version string alone.
    import update
    update.verify_installed(STATE, check_unit=True, check_config=check_config)
    result = run('systemctl', 'show', '--property=FragmentPath', '--value', UNIT)
    dropins = run('systemctl', 'show', '--property=DropInPaths', '--value', UNIT)
    if (result.returncode or result.stdout.strip() != str(UNIT_PATH)
            or dropins.returncode or dropins.stdout.strip()):
        fail('BACKUP_UNIT_CONFLICT', 'systemd 生效服务路径不同或存在额外覆盖配置，已停止操作。')


def config_test():
    if run(str(BINARY), 'run', '-test', '-config', str(CONFIG)).returncode:
        fail('BACKUP_CONFIG_INVALID', 'Xray 配置校验未通过，已停止操作。')


def local_health():
    config_test()
    pid = run('systemctl', 'show', '--property=MainPID', '--value', UNIT)
    for attempt in range(20):
        listeners = run('ss', '-ltnpH', '( sport = :443 )')
        lines = [line for line in listeners.stdout.splitlines() if re.search(r'(?<!\d):443\s', line)]
        if listeners.returncode or lines or attempt == 19:
            break
        time.sleep(0.5)
    number = pid.stdout.strip()
    if (pid.returncode or not number.isdigit() or int(number) == 0 or listeners.returncode or len(lines) != 1
            or not re.search(r'pid=' + re.escape(number) + r'(?:,|\))', lines[0])
            or os.path.realpath(f'/proc/{number}/exe') != str(BINARY)):
        fail('BACKUP_SERVICE_UNHEALTHY', 'TCP 443 未由受管 Xray 服务独占，请先检查运行状态。')
    try:
        with socket.create_connection(('127.0.0.1', 443), timeout=3):
            pass
    except OSError:
        fail('BACKUP_SERVICE_UNHEALTHY', '本机 TCP 443 无法连接，请先检查运行状态。')


def current_node():
    trusted_program()
    try:
        dr.check_state(STATE, defaults()['XRAY_VERSION'], (HERE / 'VERSION').read_text().strip())
    except SystemExit:
        fail('BACKUP_STATE_CONFLICT', '请先安装或升级受管节点，现有状态不兼容，已保留。')
    value = snapshot()
    active = service_active()
    if active:
        local_health()
    else:
        config_test()
        listeners = run('ss', '-ltnH', '( sport = :443 )')
        if listeners.returncode or listeners.stdout.strip():
            fail('BACKUP_SERVICE_UNHEALTHY', '服务已停止，但 443 端口仍被占用，已停止操作。')
    return value, active


def save(value, prefix='vps-dr'):
    name = prefix + '-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + secrets.token_hex(4) + '.json'
    path = BACKUPS / name
    # O_EXCL ensures an unexpected name collision cannot overwrite an existing backup.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(canonical(value) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        sync_directory(BACKUPS)
    except BaseException:
        path.unlink(missing_ok=True)
        raise
    return path


def set_runtime(active):
    if run('systemctl', 'start' if active else 'stop', UNIT).returncode:
        fail('BACKUP_SERVICE_FAILED', '无法恢复服务运行状态。')
    if service_active() != active:
        fail('BACKUP_SERVICE_FAILED', '服务运行状态与预期不符。')
    if active:
        local_health()


def apply_files(value):
    atomic_write(CONFIG, value['files']['config.json'].encode('utf-8'))
    atomic_write(STATE, value['files']['state.json'].encode('utf-8'))
    config_test()


def remove_journal():
    (BACKUPS / JOURNAL_NAME).unlink()
    sync_directory(BACKUPS)


def recover():
    path = BACKUPS / JOURNAL_NAME
    if not path.exists() and not path.is_symlink():
        return False
    try:
        journal = json.loads(read_private(path).decode('utf-8'))
        if (not isinstance(journal, dict) or set(journal) != {'schema', 'before', 'target', 'active'}
                or journal['schema'] != 'vps-dr-restore-v1' or type(journal['active']) is not bool):
            raise ValueError
        before = validate_envelope(journal['before'])
        target = validate_envelope(journal['target'])
    except (ValueError, UnicodeError):
        fail('BACKUP_RECOVERY_INVALID', '恢复日志损坏，请保留备份目录并人工检查。')
    trusted_program(check_config=False)
    # A third-party edit made after the interruption is never overwritten automatically.
    for path, name in ((STATE, 'state.json'), (CONFIG, 'config.json')):
        if read_private(path).decode('utf-8') not in {before['files'][name], target['files'][name]}:
            fail('BACKUP_RECOVERY_CONFLICT', '中断后配置出现其他修改，已保留恢复日志，请人工核对。')
    try:
        if run('systemctl', 'stop', UNIT).returncode:
            fail('BACKUP_SERVICE_FAILED', '无法停止服务以回滚配置。')
        apply_files(before)
        set_runtime(journal['active'])
        remove_journal()
    except (RuntimeError, OSError, UnicodeError) as exc:
        fail('BACKUP_ROLLBACK_FAILED', '回滚尚未完成，恢复日志和原配置备份已保留，请检查磁盘和服务后重试。')
    print('BACKUP_RECOVERED: 已回滚上次中断的恢复，并还原原服务运行状态。')
    return True


def create_backup():
    value, _ = current_node()
    path = save(value)
    print(f'BACKUP_SAVED: 私密节点备份已保存到 {path}')
    print('备份包含节点凭据，请仅保存在你控制的位置，不要公开上传。')
    return path


def list_backups():
    count = 0
    for path in sorted(BACKUPS.glob('*.json')):
        if not re.fullmatch(r'(?:vps-dr|before-restore)-\d{8}T\d{6}Z-[0-9a-f]{8}\.json', path.name):
            continue
        try:
            value = load_envelope(path)
        except (RuntimeError, OSError, UnicodeError):
            print(f'BACKUP_INVALID: {path.name} 无法校验，请人工检查。')
            continue
        print(f"{value['created_at']}  {path}")
        count += 1
    if not count:
        print('暂无可用备份。请先选择创建备份。')


def restore(path, confirmed=False):
    if not confirmed:
        fail('BACKUP_CONFIRM_REQUIRED', '恢复会替换 UUID 和配置，请明确确认后使用 restore /备份绝对路径 --confirm。')
    target = load_envelope(path)
    before, active = current_node()
    # Backups migrate node credentials, not installed code. Keep the verified local
    # core and current management state versions when restoring an older archive.
    state = validate_pair(target['files'])
    installed = validate_pair(before['files'])
    state['xray_version'] = installed['xray_version']
    state['deployment_version'] = installed['deployment_version']
    target = envelope({'state.json': json.dumps(state, ensure_ascii=False) + '\n',
                       'config.json': target['files']['config.json']})
    preserved = save(before, prefix='before-restore')
    journal = {'schema': 'vps-dr-restore-v1', 'before': before, 'target': target, 'active': active}
    atomic_write(BACKUPS / JOURNAL_NAME, canonical(journal) + b'\n')
    try:
        if run('systemctl', 'stop', UNIT).returncode:
            fail('BACKUP_SERVICE_FAILED', '无法停止服务以恢复配置。')
        apply_files(target)
        set_runtime(active)
        remove_journal()
    except BaseException:
        recover()
        raise
    print(f'BACKUP_RESTORED: 已恢复所选节点配置，恢复前备份保存在 {preserved}')
    print('原先运行的服务已通过本机检查，原先停止的服务保持停止。客户端请重新导入节点链接。')


@contextlib.contextmanager
def operation_lock():
    fd = os.open(LOCK_PATH, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as lock:
        meta = os.fstat(lock.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            fail('BACKUP_LOCK_UNSAFE', '共用锁文件的类型、所有者或权限不安全。')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fail('INSTALL_IN_PROGRESS', '正在安装、升级或操作节点，请稍后重试。')
        yield


def main():
    args = sys.argv[1:]
    recovering = args in (['recover'], ['recover', '--lock-held'])
    valid = recovering or args in (['backup'], ['list']) or (len(args) == 3 and args[0] == 'restore' and args[2] == '--confirm')
    if not valid:
        fail('BACKUP_USAGE', '使用 backup 创建备份、list 查看备份，或 restore /备份绝对路径 --confirm 恢复。')
    if os.name != 'posix' or os.geteuid() != 0:
        fail('ROOT_REQUIRED', '备份和恢复节点需要 Linux root 权限。')
    # The menu/installer may call this before all other operations. A normal
    # read-only menu display need not contend with an unrelated installation.
    if recovering and not (BACKUPS / JOURNAL_NAME).exists() and not (BACKUPS / JOURNAL_NAME).is_symlink():
        return
    lock = contextlib.nullcontext() if args == ['recover', '--lock-held'] else operation_lock()
    with lock:
        prepare_directory()
        recover()
        if args == ['backup']:
            create_backup()
        elif args == ['list']:
            list_backups()
        elif recovering:
            return
        else:
            restore(Path(args[1]), confirmed=True)


if __name__ == '__main__':
    try:
        main()
    except RuntimeError as exc:
        print(f'BACKUP_FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
    except (OSError, UnicodeError):
        print('BACKUP_IO_FAILED: 文件读写失败，请检查 root 权限、磁盘空间、私密文件权限及路径。', file=sys.stderr)
        sys.exit(1)
