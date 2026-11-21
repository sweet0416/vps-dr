#!/usr/bin/env python3
"""Sample host default-interface counters into private, cumulative UTC totals."""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

STATE_DIR = Path('/var/lib/vps-dr')
STATE = STATE_DIR / 'traffic.json'
LOCK = STATE_DIR / '.traffic.lock'
ROUTES = Path('/proc/net/route')
ROUTES6 = Path('/proc/net/ipv6_route')
NET = Path('/sys/class/net')
BOOT = Path('/proc/sys/kernel/random/boot_id')
UNIT_DIR = Path('/etc/systemd/system')
OTHER_UNIT_DIRS = tuple(map(Path, ('/run/systemd/system', '/run/systemd/transient',
                                 '/run/systemd/generator', '/run/systemd/generator.early',
                                 '/run/systemd/generator.late', '/usr/local/lib/systemd/system',
                                 '/usr/lib/systemd/system', '/lib/systemd/system')))
SOURCE = Path(__file__).resolve().parents[1] / 'config'
UNITS = ('vps-dr-traffic.service', 'vps-dr-traffic.timer')


def now():
    return datetime.now(timezone.utc)


def require_root():
    if os.geteuid() != 0:
        raise ValueError('ROOT_REQUIRED: 请使用 root 查看或采集流量。')


def secure_parent(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError(f'TRAFFIC_PATH_UNSAFE: 目录包含符号链接：{parent}')
    if path.exists():
        meta = path.stat()
        if not path.is_dir() or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise ValueError(f'TRAFFIC_PATH_UNSAFE: 目录所有者或权限不安全：{path}')


@contextmanager
def locked():
    secure_parent(LOCK.parent)
    fd = os.open(LOCK, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a') as stream:
        meta = os.fstat(stream.fileno())
        if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o022:
            raise ValueError('TRAFFIC_LOCK_UNSAFE: 流量采集锁的所有者或权限不安全。')
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def interfaces():
    # Keep the preferred default routes from the main table in each family.
    # Equal-metric routes may both carry traffic; count each interface once.
    preferred = {}
    def add(family, metric, name):
        current = preferred.get(family)
        if current is None or metric < current[0]:
            preferred[family] = (metric, {name})
        elif metric == current[0]:
            current[1].add(name)
    for row in ROUTES.read_text().splitlines()[1:]:
        fields = row.split()
        if len(fields) >= 8 and fields[1] == '00000000' and fields[7] == '00000000' and int(fields[3], 16) & 1:
            add(4, int(fields[6]), fields[0])
    if ROUTES6.exists():
        for row in ROUTES6.read_text().splitlines():
            fields = row.split()
            if len(fields) == 10 and fields[0] == '0' * 32 and fields[1] == '00':
                flags = int(fields[8], 16)
                if flags & 1 and not flags & 0x200:
                    add(6, int(fields[5], 16), fields[9])
    found = set().union(*(entry[1] for entry in preferred.values()))
    found.discard('lo')
    if not found or any(not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', name) for name in found):
        raise ValueError('TRAFFIC_INTERFACE_UNAVAILABLE: 未发现可统计的默认出口网卡。')
    return sorted(found)


def counters():
    result = {}
    for name in interfaces():
        stats = NET / name / 'statistics'
        result[name] = [int((stats / field).read_text()) for field in ('rx_bytes', 'tx_bytes')]
        if min(result[name]) < 0:
            raise ValueError('TRAFFIC_COUNTER_INVALID: 网卡计数器无效。')
    return result


def load():
    if STATE.is_symlink():
        raise ValueError('TRAFFIC_STATE_UNSAFE: 流量状态文件是符号链接。')
    if not STATE.exists():
        return None
    meta = STATE.stat()
    if not stat.S_ISREG(meta.st_mode) or meta.st_uid != 0 or meta.st_mode & 0o077:
        raise ValueError('TRAFFIC_STATE_UNSAFE: 流量状态必须由 root 所有且禁止其他用户读取。')
    state = json.loads(STATE.read_text())
    if not isinstance(state, dict) or state.get('schema') != 1:
        raise ValueError('TRAFFIC_STATE_INVALID: 流量状态格式不兼容。')
    for key in ('started_at', 'last_at', 'boot'):
        if not isinstance(state.get(key), str):
            raise ValueError('TRAFFIC_STATE_INVALID: 流量状态字段损坏。')
    for key in ('total',):
        if not valid_pair(state.get(key)):
            raise ValueError('TRAFFIC_STATE_INVALID: 流量累计值损坏。')
    for key in ('monthly', 'last'):
        values = state.get(key)
        if not isinstance(values, dict) or any(not isinstance(k, str) or not valid_pair(v) for k, v in values.items()):
            raise ValueError('TRAFFIC_STATE_INVALID: 流量采样值损坏。')
    if type(state.get('gaps')) is not int or state['gaps'] < 0:
        raise ValueError('TRAFFIC_STATE_INVALID: 流量中断计数损坏。')
    for key in ('started_at', 'last_at'):
        if datetime.fromisoformat(state[key]).utcoffset() is None:
            raise ValueError('TRAFFIC_STATE_INVALID: 采集时间缺少时区。')
    if not state['boot'] or any(not re.fullmatch(r'\d{4}-(?:0[1-9]|1[0-2])', key) for key in state['monthly']):
        raise ValueError('TRAFFIC_STATE_INVALID: 流量月份或启动标识损坏。')
    if not state['last'] or any(not re.fullmatch(r'[A-Za-z0-9_.:-]{1,64}', key) for key in state['last']):
        raise ValueError('TRAFFIC_STATE_INVALID: 网卡标识损坏。')
    return state


def valid_pair(value):
    return isinstance(value, list) and len(value) == 2 and all(type(n) is int and n >= 0 for n in value)


def write_atomic(path, content, mode):
    fd, name = tempfile.mkstemp(prefix='.vps-dr-traffic-', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def sample():
    require_root()
    secure_parent(STATE_DIR)
    STATE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    with locked():
        current = counters()
        boot = BOOT.read_text().strip()
        if not boot:
            raise ValueError('TRAFFIC_BOOT_UNAVAILABLE: 无法读取本机启动标识。')
        stamp = now().isoformat(timespec='seconds')
        state = load()
        if state is None:
            state = dict(schema=1, started_at=stamp, last_at=stamp, boot=boot,
                         total=[0, 0], monthly={}, last=current, gaps=0)
        else:
            delta = [0, 0]
            changed_boot = state['boot'] != boot
            gap = changed_boot or set(current) != set(state['last'])
            for name, values in current.items():
                previous = state['last'].get(name)
                if previous is None:
                    continue  # Start a baseline for newly observed interfaces.
                for index, value in enumerate(values):
                    reset = changed_boot or value < previous[index]
                    delta[index] += value if reset else value - previous[index]
                    gap = gap or reset
            month = stamp[:7]
            month_total = state['monthly'].setdefault(month, [0, 0])
            for index in (0, 1):
                state['total'][index] += delta[index]
                month_total[index] += delta[index]
            state['gaps'] += int(gap)
            state.update(last_at=stamp, boot=boot, last=current)
        write_atomic(STATE, (json.dumps(state, ensure_ascii=False) + '\n').encode(), 0o600)
        return state


def readable(value):
    amount = float(value)
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB'):
        if amount < 1024 or unit == 'PiB':
            return f'{amount:.2f} {unit}'
        amount /= 1024


def show():
    state = sample()
    month = now().strftime('%Y-%m')
    received, sent = state['monthly'].get(month, [0, 0])
    print('整机默认出口网卡流量统计（UTC 月份，采集起点后累计）')
    print('当前出口网卡  ' + '、'.join(state['last']))
    print('首次采集时间  ' + state['started_at'])
    print('最新采集时间  ' + state['last_at'])
    print(f'本月 {month}  下载 {readable(received)}  上传 {readable(sent)}')
    print(f'累计          下载 {readable(state["total"][0])}  上传 {readable(state["total"][1])}')
    print('按主路由表选择出口，范围包含这些网卡上其他服务的流量。计费口径请以服务商为准。')
    print('每分钟采集；采集间隔跨月时计入本次月份。重启、网卡切换或归零期间可能漏计。')
    if state['gaps']:
        print(f'已记录 {state["gaps"]} 次网卡变化或计数重置，累计值为估算。')


def systemctl(*args):
    subprocess.run(('systemctl', *args), check=True, capture_output=True, text=True, timeout=20)


def setup():
    """Installer holds vps-dr.lock. Validate both units before writing either."""
    require_root()
    secure_parent(UNIT_DIR)
    contents = {}
    for name in UNITS:
        expected = (SOURCE / name).read_bytes()
        for directory in (UNIT_DIR, *OTHER_UNIT_DIRS):
            path = directory / name
            dropin = directory / (name + '.d')
            if dropin.is_symlink() or (dropin.exists() and (not dropin.is_dir() or any(dropin.iterdir()))):
                raise ValueError(f'TRAFFIC_UNIT_CONFLICT: 存在未知流量服务覆盖配置：{dropin}')
            if path.is_symlink() or (path.exists() and (not path.is_file() or path.read_bytes() != expected)):
                raise ValueError(f'TRAFFIC_UNIT_CONFLICT: 已有未知来源的流量服务文件：{path}')
            if path.exists():
                meta = path.stat()
                if meta.st_uid != 0 or meta.st_mode & 0o022:
                    raise ValueError(f'TRAFFIC_UNIT_CONFLICT: 流量服务文件权限不安全：{path}')
        contents[UNIT_DIR / name] = expected
    UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for path, expected in contents.items():
        if not path.exists():
            write_atomic(path, expected, 0o644)
    systemctl('daemon-reload')
    systemctl('enable', '--now', 'vps-dr-traffic.timer')
    # Seed immediately; failure preserves the installed timer for a later retry.
    sample()
    print('TRAFFIC_READY: 每分钟流量采集已启用，统计从首次成功采集开始。')


def main():
    actions = {'sample': sample, 'show': show, 'setup': setup}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise ValueError('TRAFFIC_USAGE: 请使用 sample、show 或 setup。')
    actions[sys.argv[1]]()


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f'TRAFFIC_FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
