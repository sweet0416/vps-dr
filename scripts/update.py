#!/usr/bin/env python3
"""Official Xray updates with a private trust record and recoverable transaction."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import stat
import subprocess
import sys
sys.dont_write_bytecode = True
import tempfile
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STATE = Path('/etc/vps-dr/state.json')
TRUST = Path('/etc/vps-dr/core.json')
TRANSACTION = Path('/etc/vps-dr/core-update')
XRAY = Path('/usr/local/bin/xray')
CONFIG = Path('/usr/local/etc/xray/config.json')
UNIT_FILE = Path('/etc/systemd/system/xray.service')
UNIT = 'xray.service'
LOCK = Path('/run/lock/vps-dr.lock')
RELEASE_API = 'https://api.github.com/repos/XTLS/Xray-core/releases/latest'
DOWNLOAD = 'https://github.com/XTLS/Xray-core/releases/download/'
VERSION_RE = re.compile(r'v\d+\.\d+\.\d+')
SHA_RE = re.compile(r'[a-f0-9]{64}')
MAX_BINARY = 150 * 1024 * 1024


def command(*args):
    try:
        return subprocess.run(args, text=True, capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f'CORE_COMMAND_FAILED: 无法执行 {args[0]}，请检查系统命令和权限。') from exc


def checked(*args):
    result = command(*args)
    if result.returncode:
        raise RuntimeError(f'CORE_COMMAND_FAILED: {args[0]} 执行失败，请检查本机服务状态。')
    return result.stdout.strip()


def safe(path, private=False, required=True):
    path = Path(path)
    for parent in path.parents:
        if parent.is_symlink():
            raise RuntimeError(f'CORE_PATH_UNSAFE: 路径包含符号链接：{parent}')
    if path.is_symlink():
        raise RuntimeError(f'CORE_PATH_UNSAFE: 已保留符号链接：{path}')
    parent_meta = path.parent.stat()
    if parent_meta.st_uid != 0 or parent_meta.st_mode & 0o022:
        raise RuntimeError(f'CORE_PATH_UNSAFE: 文件所在目录权限不安全：{path.parent}')
    if not path.exists():
        if required:
            raise RuntimeError(f'CORE_FILE_MISSING: 缺少受管文件：{path}')
        return False
    meta = path.stat()
    mask = 0o077 if private else 0o022
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & mask:
        raise RuntimeError(f'CORE_PATH_UNSAFE: 文件来源或权限不安全：{path}')
    return True


def secure_dir(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f'CORE_PATH_UNSAFE: 目录是符号链接：{path}')
    for parent in path.parents:
        if parent.is_symlink():
            raise RuntimeError(f'CORE_PATH_UNSAFE: 目录包含符号链接：{parent}')
    if not path.exists():
        path.mkdir(mode=0o700)
        # Persist the new name in its parent before any journal can guard a core switch.
        sync_dir(path.parent)
    meta = path.stat()
    if not path.is_dir() or meta.st_uid != 0 or meta.st_mode & 0o077:
        raise RuntimeError(f'CORE_PATH_UNSAFE: 目录必须由 root 专用：{path}')


def sync_dir(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic(path, content, mode=0o600):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError(f'CORE_PATH_UNSAFE: 已保留符号链接：{path}')
    fd, name = tempfile.mkstemp(prefix='.vps-dr-core-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
        sync_dir(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def encode(data):
    return (json.dumps(data, sort_keys=True) + '\n').encode()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def machine():
    value = platform.machine()
    if value not in ('x86_64', 'aarch64'):
        raise RuntimeError('UNSUPPORTED_ARCH: 核心升级仅支持 x86_64 与 aarch64。')
    return value


def defaults():
    return dict(line.split('=', 1) for line in (ROOT / 'config/defaults.env').read_text().splitlines()
                if '=' in line and line.startswith('XRAY_'))


def validate_trust(data):
    if (not isinstance(data, dict) or set(data) != {'schema', 'version', 'machine', 'archive_sha256', 'binary_sha256'}
            or data['schema'] != 1 or not isinstance(data['version'], str)
            or not VERSION_RE.fullmatch(data['version']) or data['machine'] != machine()
            or not isinstance(data['archive_sha256'], str) or not SHA_RE.fullmatch(data['archive_sha256'])
            or not isinstance(data['binary_sha256'], str) or not SHA_RE.fullmatch(data['binary_sha256'])):
        raise RuntimeError('CORE_TRUST_INVALID: 核心校验记录损坏，已停止覆盖。')
    return data


def read_json(path, limit=65536):
    if Path(path).stat().st_size > limit:
        raise RuntimeError('CORE_DATA_INVALID: 核心状态文件过大。')
    try:
        return json.loads(Path(path).read_text())
    except (ValueError, UnicodeError) as exc:
        raise RuntimeError('CORE_DATA_INVALID: 核心状态文件无法读取。') from exc


def verify_unit():
    for directory in (Path('/etc/systemd/system/xray.service.d'), Path('/run/systemd/system/xray.service.d'),
                      Path('/usr/local/lib/systemd/system/xray.service.d'),
                      Path('/usr/lib/systemd/system/xray.service.d')):
        if directory.exists() or directory.is_symlink():
            raise RuntimeError('EXISTING_XRAY_CONFLICT: 检测到额外服务覆盖目录，已保留。')
    safe(UNIT_FILE)
    if UNIT_FILE.read_bytes() != (ROOT / 'config/xray.service').read_bytes():
        raise RuntimeError('EXISTING_XRAY_CONFLICT: 服务文件不属于本项目，已保留。')
    fragment = checked('systemctl', 'show', '--property=FragmentPath', '--value', UNIT)
    drops = checked('systemctl', 'show', '--property=DropInPaths', '--value', UNIT)
    if fragment != str(UNIT_FILE) or drops:
        raise RuntimeError('EXISTING_XRAY_CONFLICT: 实际服务路径或覆盖项不属于本项目，已保留。')


def verify_installed(state_path=STATE, check_unit=True, check_config=True):
    import dr
    state_path = Path(state_path)
    safe(state_path, private=True)
    try:
        state = dr.read_state(state_path)
    except SystemExit as exc:
        raise RuntimeError('CONFIG_CONFLICT: 节点状态无效，已停止核心升级。') from exc
    safe(XRAY)
    if check_config:
        safe(CONFIG, private=True)
    if check_config and not dr.config_matches(CONFIG, state['uuid']):
        raise RuntimeError('CONFIG_CONFLICT: UUID 与节点配置不一致，已保留。')
    if safe(TRUST, private=True, required=False):
        trusted = validate_trust(read_json(TRUST))
    else:
        known = defaults()
        suffix = 'AMD64' if machine() == 'x86_64' else 'ARM64'
        trusted = {'schema': 1, 'version': known['XRAY_VERSION'], 'machine': machine(),
                   'archive_sha256': known['XRAY_SHA256_' + suffix],
                   'binary_sha256': known['XRAY_BINARY_SHA256_' + suffix]}
    if state['xray_version'] != trusted['version'] or sha(XRAY) != trusted['binary_sha256']:
        raise RuntimeError('EXISTING_XRAY_CONFLICT: 现有核心版本或摘要不是受信版本，已保留。')
    result = command(str(XRAY), 'version')
    if result.returncode or not re.search(r'^Xray ' + re.escape(trusted['version'][1:]) + r'\b', result.stdout):
        raise RuntimeError('EXISTING_XRAY_CONFLICT: 现有核心版本输出与校验记录不一致。')
    if check_unit:
        verify_unit()
    return trusted


def latest():
    try:
        request = urllib.request.Request(RELEASE_API, headers={'Accept': 'application/vnd.github+json',
                                                              'User-Agent': 'vps-dr'})
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError('oversized')
        data = json.loads(raw)
        version = data['tag_name']
        if data.get('draft') is not False or data.get('prerelease') is not False or not VERSION_RE.fullmatch(version):
            raise ValueError('not a stable release')
        asset = 'Xray-linux-64.zip' if machine() == 'x86_64' else 'Xray-linux-arm64-v8a.zip'
        found = {a['name']: a for a in data['assets'] if a.get('name') in (asset, asset + '.dgst')}
        for name in (asset, asset + '.dgst'):
            item = found[name]
            if item['browser_download_url'] != DOWNLOAD + version + '/' + name:
                raise ValueError('unexpected asset origin')
            digest = item.get('digest')
            if digest is not None and not re.fullmatch(r'sha256:[a-f0-9]{64}', digest):
                raise ValueError('unexpected asset digest')
        return version, found[asset], found[asset + '.dgst']
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RuntimeError('CORE_UPDATE_CHECK_FAILED: 无法读取官方稳定版，请检查 GitHub 网络或稍后重试。') from exc


def version_tuple(value):
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise RuntimeError('CORE_VERSION_INVALID: 核心版本格式无效。')
    return tuple(int(part) for part in value[1:].split('.'))


def download(url, target):
    try:
        result = subprocess.run(['curl', '-q', '--proto', '=https', '--proto-redir', '=https',
                                 '--tlsv1.2', '-fsSL', '--connect-timeout', '15', '--max-time', '120',
                                 '--max-filesize', str(MAX_BINARY), '--retry', '2', '--retry-max-time', '150',
                                 url, '-o', str(target)], capture_output=True, timeout=170)
        if result.returncode:
            raise RuntimeError('download failed')
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        raise RuntimeError('CORE_DOWNLOAD_FAILED: 官方文件下载失败，现有核心和配置已保留。') from exc


def package(version, archive, digest, directory):
    zipped, summary = directory / 'xray.zip', directory / 'xray.zip.dgst'
    download(archive['browser_download_url'], zipped)
    download(digest['browser_download_url'], summary)
    if zipped.stat().st_size > MAX_BINARY or summary.stat().st_size > 4096:
        raise RuntimeError('CORE_PACKAGE_INVALID: 官方包大小超出范围。')
    actual = sha(zipped)
    values = re.findall(r'^SHA2-256=\s*([a-fA-F0-9]{64})\s*$', summary.read_text(), re.M)
    if len(values) != 1 or actual != values[0].lower():
        raise RuntimeError('CHECKSUM_MISMATCH: 官方包与 SHA-256 摘要不一致。')
    for item, path in ((archive, zipped), (digest, summary)):
        if item.get('digest') is not None and item['digest'] != 'sha256:' + sha(path):
            raise RuntimeError('CHECKSUM_MISMATCH: 官方文件与发布 API 摘要不一致。')
    binary = directory / 'xray'
    try:
        with zipfile.ZipFile(zipped) as source:
            matches = [item for item in source.infolist() if item.filename == 'xray']
            if len(matches) != 1 or not 0 < matches[0].file_size <= MAX_BINARY or matches[0].is_dir():
                raise ValueError('invalid binary member')
            with source.open(matches[0]) as stream, binary.open('xb') as target:
                shutil.copyfileobj(stream, target)
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as exc:
        raise RuntimeError('CORE_PACKAGE_INVALID: 官方压缩包无法安全提取核心。') from exc
    binary.chmod(0o755)
    result = command(str(binary), 'version')
    if result.returncode or not re.search(r'^Xray ' + re.escape(version[1:]) + r'\b', result.stdout):
        raise RuntimeError('CORE_PACKAGE_INVALID: 官方核心架构或版本不匹配。')
    if command(str(binary), 'run', '-test', '-config', str(CONFIG)).returncode:
        raise RuntimeError('XRAY_CONFIG_TEST_FAIL: 新核心无法通过现有配置校验，未切换服务。')
    return binary, {'schema': 1, 'version': version, 'machine': machine(),
                    'archive_sha256': actual, 'binary_sha256': sha(binary)}


def local_health():
    if command(str(XRAY), 'run', '-test', '-config', str(CONFIG)).returncode:
        raise RuntimeError('HEALTH_CHECK_FAIL: 核心配置校验失败。')
    for _ in range(20):
        if command('systemctl', 'is-active', '--quiet', UNIT).returncode == 0:
            pid = checked('systemctl', 'show', '--property=MainPID', '--value', UNIT)
            listeners = checked('ss', '-ltnpH', '( sport = :443 )').splitlines()
            if (pid.isdecimal() and int(pid) > 0 and len(listeners) == 1
                    and re.search(r'pid=' + re.escape(pid) + r'(?=[,)])', listeners[0])
                    and os.path.realpath('/proc/' + pid + '/exe') == str(XRAY)):
                try:
                    with socket.create_connection(('127.0.0.1', 443), timeout=3):
                        return
                except OSError:
                    pass
        time.sleep(0.5)
    raise RuntimeError('HEALTH_CHECK_FAIL: 新核心未通过本机服务、进程及 TCP 443 检查。')


def service_active():
    state = checked('systemctl', 'show', '--property=ActiveState', '--value', UNIT)
    if state not in ('active', 'inactive'):
        raise RuntimeError('CORE_SERVICE_UNHEALTHY: 服务处于故障或过渡状态，请先检查 Xray 运行状态。')
    return state == 'active'


@contextmanager
def locked():
    if os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED: 请使用 root 用户。')
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        meta = os.fstat(fd)
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise RuntimeError('CORE_LOCK_UNSAFE: 安装锁权限不安全。')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('INSTALL_IN_PROGRESS: 其他安装、备份或升级正在运行，请稍后重试。') from exc
        yield
    finally:
        os.close(fd)


def completed_path():
    return TRANSACTION.with_name(TRANSACTION.name + '.done')


def cleanup_completed():
    completed = completed_path()
    if not completed.exists() and not completed.is_symlink():
        return False
    secure_dir(completed)
    for item in completed.iterdir():
        if item.name not in {'journal.json', 'xray.before'} and not item.name.startswith('.vps-dr-core-'):
            raise RuntimeError('CORE_RECOVERY_BLOCKED: 完成记录目录存在未知文件，已保留。')
        safe(item, private=True)
    # A kill here leaves only completed cleanup; it can never trigger rollback.
    shutil.rmtree(completed)
    sync_dir(completed.parent)
    return True


def finish_transaction():
    cleanup_completed()
    # The rename is the durable commit boundary. Never delete the journal from
    # an active transaction: a kill during rmtree could otherwise lose its phase.
    os.replace(TRANSACTION, completed_path())
    sync_dir(TRANSACTION.parent)
    cleanup_completed()


def recover():
    cleaned = cleanup_completed()
    if not TRANSACTION.exists() and not TRANSACTION.is_symlink():
        return cleaned
    secure_dir(TRANSACTION)
    journal_path = TRANSACTION / 'journal.json'
    if not journal_path.exists() and not journal_path.is_symlink():
        # A kill during preparation happened before any managed file changed.
        # Verify the live installation before removing our incomplete scratch.
        allowed = {'xray.before'}
        for item in TRANSACTION.iterdir():
            if item.name not in allowed and not item.name.startswith('.vps-dr-core-'):
                raise RuntimeError('CORE_RECOVERY_BLOCKED: 准备目录存在未知文件，已保留。')
            safe(item, private=True)
        verify_installed(STATE)
        finish_transaction()
        return True
    safe(journal_path, private=True)
    journal = read_json(journal_path)
    if (not isinstance(journal, dict) or set(journal) != {'schema', 'old', 'new', 'active', 'state', 'trust'}
            or journal['schema'] != 1 or type(journal['active']) is not bool):
        raise RuntimeError('CORE_RECOVERY_BLOCKED: 核心恢复记录损坏，请保留现场。')
    old = validate_trust(journal['old'])
    new = validate_trust(journal['new'])
    if not isinstance(journal['state'], dict) or journal['state'].get('xray_version') != old['version']:
        raise RuntimeError('CORE_RECOVERY_BLOCKED: 原状态记录与核心不一致。')
    before = TRANSACTION / 'xray.before'
    safe(before, private=True)
    if sha(before) != old['binary_sha256']:
        raise RuntimeError('CORE_RECOVERY_BLOCKED: 原核心备份摘要不一致，请保留现场。')
    safe(XRAY)
    if sha(XRAY) not in (old['binary_sha256'], new['binary_sha256']):
        raise RuntimeError('CORE_RECOVERY_BLOCKED: 当前核心存在未知修改，已停止回滚。')
    safe(STATE, private=True)
    current_state = read_json(STATE)
    allowed_state = dict(journal['state'], xray_version=new['version'])
    if current_state not in (journal['state'], allowed_state):
        raise RuntimeError('CORE_RECOVERY_BLOCKED: 当前节点状态存在未知修改，已保留。')
    if safe(TRUST, private=True, required=False):
        current_trust = read_json(TRUST)
        if current_trust not in (old, new):
            raise RuntimeError('CORE_RECOVERY_BLOCKED: 当前核心校验记录存在未知修改，已保留。')
    verify_unit()
    import dr
    safe(CONFIG, private=True)
    if not dr.config_matches(CONFIG, journal['state'].get('uuid', '')):
        raise RuntimeError('CORE_RECOVERY_BLOCKED: 节点配置存在未知修改，已保留。')
    if journal['trust'] is not None:
        validate_trust(journal['trust'])
        if journal['trust'] != old:
            raise RuntimeError('CORE_RECOVERY_BLOCKED: 原核心信任记录不一致。')
    atomic(XRAY, before.read_bytes(), 0o755)
    atomic(STATE, encode(journal['state']))
    if journal['trust'] is None:
        TRUST.unlink(missing_ok=True)
        sync_dir(TRUST.parent)
    else:
        atomic(TRUST, encode(journal['trust']))
    if journal['active']:
        checked('systemctl', 'restart', UNIT)
        local_health()
    else:
        checked('systemctl', 'stop', UNIT)
    finish_transaction()
    print('CORE_ROLLED_BACK: 已恢复升级前的核心、版本记录及服务运行状态；UUID 与节点配置已保留。')
    return True


def upgrade():
    import dr
    with locked():
        recover()
        import backup
        if (backup.BACKUPS / backup.JOURNAL_NAME).exists():
            backup.recover()
        old = verify_installed(STATE)
        state = dr.read_state(STATE)
        version, archive, digest = latest()
        if version_tuple(version) <= version_tuple(old['version']):
            print(f'CORE_CURRENT: 当前核心 {old["version"]}，官方稳定版 {version}，无需升级。')
            return
        active = service_active()
        if active:
            local_health()
        else:
            listeners = checked('ss', '-ltnpH', '( sport = :443 )')
            if listeners:
                raise RuntimeError('PORT_443_CONFLICT: 服务已停止但 TCP 443 被占用，已停止升级。')
        secure_dir(STATE.parent)
        with tempfile.TemporaryDirectory(prefix='vps-dr-core-') as temporary:
            print(f'正在下载并校验官方稳定版 {version}；现有服务继续运行。', flush=True)
            binary, trusted = package(version, archive, digest, Path(temporary))
            if verify_installed(STATE) != old or dr.read_state(STATE) != state:
                raise RuntimeError('CORE_CHANGED_DURING_DOWNLOAD: 下载期间受管状态发生变化，已停止升级。')
            # Commit only after all external downloads and config tests have passed.
            # The fixed private journal contains no attacker-selected recovery path.
            secure_dir(TRANSACTION)
            atomic(TRANSACTION / 'xray.before', XRAY.read_bytes())
            previous_trust = read_json(TRUST) if TRUST.exists() else None
            journal = {'schema': 1, 'old': old, 'new': trusted, 'active': active,
                       'state': state, 'trust': previous_trust}
            # An operator or systemd can change service state outside the project lock.
            try:
                if service_active() != active:
                    raise RuntimeError('CORE_CHANGED_DURING_DOWNLOAD: 服务运行状态已变化，已停止升级。')
            except RuntimeError:
                finish_transaction()  # Discard preparation without touching the live service.
                raise
            atomic(TRANSACTION / 'journal.json', encode(journal))
            try:
                atomic(XRAY, binary.read_bytes(), 0o755)
                atomic(TRUST, encode(trusted))
                updated = dict(state, xray_version=version)
                atomic(STATE, encode(updated))
                if active:
                    checked('systemctl', 'restart', UNIT)
                    local_health()
                verify_installed(STATE)
            except BaseException as exc:
                try:
                    recover()
                except BaseException as recovery_error:
                    raise RuntimeError('CORE_ROLLBACK_FAILED: 自动回滚未完成，恢复记录已保留；请重试恢复后再操作。') from recovery_error
                raise RuntimeError('CORE_UPDATE_FAILED: 升级失败，已回滚到原核心和原运行状态。') from exc
            finish_transaction()
        print(f'CORE_UPDATED: Xray 已升级到 {version}；UUID、配置和原服务运行状态已保留。')
        print('本机校验已通过；客户端连通性请在客户端实际测试。')


def check():
    import dr
    state = dr.read_state(STATE)
    version, _, _ = latest()
    action = '可升级' if version_tuple(version) > version_tuple(state['xray_version']) else '已是当前稳定版或更高版本'
    print(f'Xray 当前版本：{state["xray_version"]}\n官方最新稳定版：{version}\n{action}')


def main():
    args = sys.argv[1:]
    if args == ['check']:
        check()
    elif args and args[0] in ('installed-check', 'installed-binary-check') and len(args) == 2:
        binary_only = args[0] == 'installed-binary-check'
        verify_installed(Path(args[1]), check_unit=not binary_only, check_config=not binary_only)
    elif args == ['upgrade', '--confirm']:
        upgrade()
    elif args == ['recover']:
        if (TRANSACTION.exists() or TRANSACTION.is_symlink()
                or completed_path().exists() or completed_path().is_symlink()):
            with locked():
                recover()
    elif args == ['recover', '--lock-held'] and os.geteuid() == 0:
        recover()
    else:
        raise RuntimeError('用法：update.py check | installed-check 状态路径 | upgrade --confirm | recover')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        sys.exit(str(exc))
