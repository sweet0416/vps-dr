#!/usr/bin/env python3
"""Fetch the current management entry through fixed HTTPS GitHub URLs."""
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

BASE = 'https://raw.githubusercontent.com/sweet0416/vps-dr/main/'


def fetch(name, limit):
    with urllib.request.urlopen(BASE + name, timeout=20) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError('下载内容超出预期大小')
    return data.decode('utf-8')


def management():
    if os.geteuid() != 0:
        raise ValueError('请使用 root 用户升级')
    latest = fetch('VERSION', 100).strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', latest):
        raise ValueError('远程版本格式异常，未执行升级')
    current = (Path(__file__).resolve().parents[1] / 'VERSION').read_text().strip()
    print(f'管理脚本当前版本 {current}，远程版本 {latest}。', flush=True)
    print('正在获取 main 的完整最新代码并检查本机兼容性……', flush=True)
    script = fetch('bootstrap.sh', 65536)
    if not script.startswith('#!/usr/bin/env bash\n'):
        raise ValueError('远程入口格式异常，未执行升级')
    environment = dict(os.environ, VPS_DR_REF='main')
    result = subprocess.run(['bash', '-s', '--', '--upgrade-management'], input=script,
                            text=True, env=environment, check=False)
    return result.returncode


def main():
    if sys.argv[1:] != ['management']:
        raise ValueError('用法：latest.py management')
    return management()


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, UnicodeError) as exc:
        sys.exit(f'MANAGEMENT_UPDATE_FAILED: 无法安全获取管理更新，请检查网络及上方提示（{type(exc).__name__}）。')
