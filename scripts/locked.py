#!/usr/bin/env python3
"""Hold the project lock across exec without following or truncating lock files."""
import fcntl
import os
from pathlib import Path
import stat
import sys

LOCK = Path('/run/lock/vps-dr.lock')
DESCRIPTOR = 9


def require_root():
    if os.geteuid() != 0:
        raise RuntimeError('ROOT_REQUIRED: 请使用 root 用户执行节点操作。')


def validate(metadata):
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0
            or metadata.st_mode & 0o022 or metadata.st_nlink != 1):
        raise RuntimeError('INSTALL_LOCK_UNSAFE: 安装锁必须是 root 所有且禁止其他用户写入的普通文件。')


def exclusive(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise RuntimeError('INSTALL_IN_PROGRESS: 另一个安装、备份或升级正在运行，请稍后重试。') from exc


def check():
    require_root()
    metadata = os.fstat(DESCRIPTOR)
    validate(metadata)
    # lstat also rejects a path replaced by a symlink after it was opened.
    current = LOCK.lstat()
    validate(current)
    if (current.st_dev, current.st_ino) != (metadata.st_dev, metadata.st_ino):
        raise RuntimeError('INSTALL_LOCK_UNSAFE: 继承的安装锁与项目锁路径不同。')
    exclusive(DESCRIPTOR)


def run(command):
    require_root()
    if not command:
        raise RuntimeError('INSTALL_LOCK_USAGE: run 后需要提供待执行命令。')
    fd = os.open(LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        validate(os.fstat(fd))
        exclusive(fd)
        # A root-owned lock in a sticky system directory cannot be replaced by
        # other users. Verify the name too before passing this descriptor on.
        current = LOCK.lstat()
        validate(current)
        opened = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
            raise RuntimeError('INSTALL_LOCK_UNSAFE: 加锁期间安装锁路径发生变化。')
        if fd != DESCRIPTOR:
            os.dup2(fd, DESCRIPTOR)
            os.close(fd)
            fd = DESCRIPTOR
        os.set_inheritable(DESCRIPTOR, True)
        environment = dict(os.environ, VPS_DR_LOCKED='1')
        os.execvpe(command[0], command, environment)
    finally:
        os.close(fd)


def main():
    args = sys.argv[1:]
    if args == ['check']:
        check()
    elif len(args) > 1 and args[0] == 'run':
        run(args[1:])
    else:
        raise RuntimeError('INSTALL_LOCK_USAGE: 使用 locked.py check 或 locked.py run 命令 [参数]。')


if __name__ == '__main__':
    try:
        main()
    except (OSError, RuntimeError) as exc:
        print(f'INSTALL_LOCK_FAILED: {exc}', file=sys.stderr)
        sys.exit(1)
