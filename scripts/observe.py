#!/usr/bin/env python3
"""Read-only, bounded node diagnostics. Never include client credentials."""
import os
from pathlib import Path
import re
import socket
import subprocess
import sys

sys.dont_write_bytecode = True
import dr

STATE = Path('/etc/vps-dr/state.json')
PROC = Path('/proc')
UNIT_PATH = Path('/etc/systemd/system/xray.service')
ROOT = Path(__file__).resolve().parents[1]


def redact(text):
    # Journals can contain copied links, config fragments or terminal escapes.
    text = re.sub(r'vmess://[^\s\"\'<>]+', '[节点链接已隐藏]', str(text), flags=re.I)
    text = re.sub(r'(?<![A-Za-z0-9])[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}(?![A-Za-z0-9])',
                  '[UUID已隐藏]', text, flags=re.I)
    text = re.sub(r'(?i)(\bauthorization\s*[:=]\s*)[^\r\n]*', r'\1[已隐藏]', text)
    text = re.sub(r'(?i)([\"\']?(?:password|passwd|token|secret)[\"\']?\s*[:=]\s*)'
                  r'(?:\"[^\"]*\"|\'[^\']*\'|\S+)', r'\1[已隐藏]', text)
    # Strip all terminal controls, including OSC hyperlinks and cursor movement.
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    return ''.join(c for c in text if c in '\n\t' or 32 <= ord(c) < 127 or ord(c) >= 160)


def command(*args, timeout=6):
    """Return a result without shell execution; callers handle unavailable tools."""
    try:
        return subprocess.run(args, text=True, capture_output=True, check=False,
                              timeout=timeout, env={**os.environ, 'LC_ALL': 'C'})
    except (OSError, subprocess.TimeoutExpired):
        return subprocess.CompletedProcess(args, 127, '', '')


def main_pid():
    result = command('systemctl', 'show', '--property=MainPID', '--value', dr.UNIT)
    value = result.stdout.strip()
    return int(value) if result.returncode == 0 and value.isdecimal() and int(value) > 0 else None


def managed_pid(pid):
    return pid is not None and os.path.realpath(PROC / str(pid) / 'exe') == str(dr.XRAY)


def config_status():
    try:
        state = dr.read_state(STATE)
        meta = dr.CONFIG.lstat()
        if (dr.CONFIG.is_symlink() or not dr.CONFIG.is_file()
                or os.name != 'nt' and (meta.st_uid != 0 or meta.st_mode & 0o077)):
            return False, '配置文件路径或权限不安全'
        if not dr.config_matches(dr.CONFIG, state['uuid']):
            return False, '配置与本机状态不一致，请先核对，不要直接重装'
        return True, '配置格式与本机 UUID 一致'
    except (OSError, SystemExit, ValueError, TypeError):
        return False, '状态文件或配置缺失、损坏或权限不安全'


def unit_status():
    try:
        meta = UNIT_PATH.lstat()
        if (UNIT_PATH.is_symlink() or not UNIT_PATH.is_file()
                or os.name != 'nt' and (meta.st_uid != 0 or meta.st_mode & 0o022)):
            return False
        if UNIT_PATH.read_bytes() != (ROOT / 'config/xray.service').read_bytes():
            return False
        for base in ('/etc/systemd/system', '/run/systemd/system'):
            dropin = Path(base) / (dr.UNIT + '.d')
            if dropin.exists() or dropin.is_symlink():
                return False
        return True
    except OSError:
        return False


def firewall():
    print('\n防火墙检查')
    ufw = command('ufw', 'status')
    if ufw.returncode == 0:
        lines = ufw.stdout.splitlines()
        print('  UFW：' + ('已启用' if any(line.strip() == 'Status: active' for line in lines)
                              else '未启用' if any(line.strip() == 'Status: inactive' for line in lines)
                              else '状态无法确认'))
        for line in [line for line in lines if re.search(r'(?<!\d)443(?:/tcp)?(?!\d)', line)][:12]:
            print('  ' + redact(line)[:240])
    else:
        nft = command('nft', 'list', 'ruleset')
        if nft.returncode == 0:
            print('  nftables：' + ('存在规则' if nft.stdout.strip() else '未读取到规则'))
            for line in [line for line in nft.stdout.splitlines()
                         if re.search(r'(?<!\d)443(?!\d)', line)][:12]:
                print('  ' + redact(line.strip())[:240])
        else:
            print('  未能读取 UFW/nftables 规则，可能未安装或权限不足。')
    print('  规则仅供核对；云安全组、上游防火墙及客户端网络仍需外部验证。')


def diagnostics():
    print('=== 节点连接诊断 ===')
    active = command('systemctl', 'is-active', '--quiet', dr.UNIT).returncode == 0
    config_ok, config_text = config_status()
    unit_ok = unit_status()
    pid = main_pid()
    listeners = command('ss', '-ltnpH', '( sport = :443 )')
    lines = [line for line in listeners.stdout.splitlines() if line.strip()]
    owner_ok = (listeners.returncode == 0 and len(lines) == 1 and managed_pid(pid)
                and re.search(r'\bpid=' + str(pid) + r'(?:,|\))', lines[0]) is not None)
    loopback_ok = False
    if active and owner_ok:
        try:
            with socket.create_connection(('127.0.0.1', 443), timeout=3):
                loopback_ok = True
        except OSError:
            pass
    print('节点服务：' + ('运行中' if active else '未运行或无法读取状态'))
    print('节点配置：' + config_text)
    print('服务定义：' + ('与本项目一致' if unit_ok else '缺失、有额外覆盖或存在未知修改，请核对'))
    print('TCP 443：' + ('由本机受管 Xray 监听' if owner_ok else
                        '无法确认由受管 Xray 独占监听，请检查端口占用及服务日志'))
    print('本机 TCP 测试：' + ('通过' if loopback_ok else '未通过或未执行'))
    addr = command('ip', '-o', '-4', 'address', 'show', 'scope', 'global')
    addresses = re.findall(r'\binet ([0-9.]+/[0-9]+)\b', addr.stdout) if addr.returncode == 0 else []
    print('本机网卡 IPv4：' + ('、'.join(addresses[:8]) if addresses else '未能读取'))
    public = dr.public_ip()
    print('公网 IPv4：' + (public if public != 'UNKNOWN' else '获取失败，不影响上述本机服务判断'))
    firewall()
    ok = active and config_ok and unit_ok and owner_ok and loopback_ok
    if ok:
        print('\nLOCAL_SERVICE: PASS — 本机服务、配置与 TCP 443 检查通过。')
    else:
        print('\nLOCAL_SERVICE: FAIL — 本机检查存在异常，请先查看上述项目与最近日志。')
    print('此诊断未进行 VMess 客户端握手；客户端实际连通和网站速度需在客户端测试。')
    return 0 if ok else 1


def human_bytes(size):
    for unit in ('B', 'KiB', 'MiB', 'GiB', 'TiB'):
        if size < 1024 or unit == 'TiB':
            return f'{size:.1f} {unit}'
        size /= 1024


def resources(pid):
    try:
        info = dict(re.findall(r'^(\w+):\s+(\d+) kB$', (PROC / 'meminfo').read_text(), flags=re.M))
        total = int(info['MemTotal']) * 1024
        available = int(info['MemAvailable']) * 1024
        print(f'主机内存：已用 {human_bytes(total - available)} / {human_bytes(total)}')
    except (OSError, ValueError, KeyError):
        print('主机内存：未能读取')
    try:
        load = (PROC / 'loadavg').read_text().split()
        if len(load) < 3 or not all(re.fullmatch(r'\d+(?:\.\d+)?', item) for item in load[:3]):
            raise ValueError('load')
        print('主机负载（1/5/15 分钟）：' + ' / '.join(load[:3]))
    except (OSError, ValueError):
        print('主机负载：未能读取')
    if not managed_pid(pid):
        print('Xray 资源：服务未运行或无法确认进程归属')
        return
    try:
        status = (PROC / str(pid) / 'status').read_text()
        rss = re.search(r'^VmRSS:\s+(\d+) kB$', status, flags=re.M)
        if rss:
            print('Xray 内存：' + human_bytes(int(rss.group(1)) * 1024))
        # comm may contain spaces or closing parentheses, so split after its last ).
        stat = (PROC / str(pid) / 'stat').read_text().rsplit(')', 1)[1].split()
        cpu_seconds = (int(stat[11]) + int(stat[12])) / os.sysconf('SC_CLK_TCK')
        print(f'Xray 累计 CPU 时间：{cpu_seconds:.1f} 秒（进程启动后累计）')
    except (OSError, ValueError, IndexError):
        print('Xray 进程资源：未能完整读取，进程可能已退出')


def connections():
    print('=== 当前连接与资源 ===')
    result = command('ss', '-Htn', 'state', 'established', '( sport = :443 )')
    if result.returncode:
        print('CONNECTIONS_UNAVAILABLE: 无法读取 TCP 连接，请确认已安装 iproute2 并使用 root 运行。')
    else:
        count = sum(bool(line.strip()) for line in result.stdout.splitlines())
        print(f'本机 TCP 443 已建立连接：{count}')
        print('这是当前 TCP 连接数，同一客户端可建立多个连接，不等于独立用户人数。')
    resources(main_pid())
    return 0 if result.returncode == 0 else 1


def logs():
    print('=== Xray 最近 50 行日志（凭据已隐藏） ===')
    result = command('journalctl', '--unit=' + dr.UNIT, '--lines=50', '--no-pager',
                     '--output=short-iso', '--quiet', timeout=8)
    if result.returncode:
        print('LOGS_UNAVAILABLE: 无法读取服务日志，请确认 systemd 正常并使用 root 运行。')
        return 1
    text = result.stdout
    if not text.strip():
        print('当前没有可读取的日志。')
        return 0
    # Bound even mocked/unexpected command output; avoid partial links at truncation.
    lines = text.splitlines()[-50:]
    for line in lines:
        cleaned = redact(line)
        print(cleaned[:2000] + ('…（本行已截断）' if len(cleaned) > 2000 else ''))
    print('日志已隐藏 UUID 和节点链接；分享前仍请检查 IP 等个人信息。')
    return 0


def main():
    actions = {'diagnostics': diagnostics, 'connections': connections, 'logs': logs}
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        print('用法：observe.py diagnostics | connections | logs', file=sys.stderr)
        return 2
    return actions[sys.argv[1]]()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError) as exc:
        sys.exit(f'OBSERVE_FAILED: 无法完成只读检查（{type(exc).__name__}），请核对权限和系统命令。')
