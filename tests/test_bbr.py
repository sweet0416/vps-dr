"""BBR regressions use a simulated kernel and temporary persistence paths."""
import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import bbr


class BbrTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.config = self.root / 'sysctl.d' / '99-vps-dr-bbr.conf'
        self.modules = self.root / 'modules-load.d' / 'vps-dr-bbr.conf'
        self.config.parent.mkdir()
        self.modules.parent.mkdir()
        for name, value in dict(CONFIG=self.config, MODULES=self.modules,
                                SYSCTL_DIRS=(self.config.parent,),
                                SYSCTL_CONF=self.root / 'sysctl.conf',
                                LOCK_PATH=self.root / 'lock').items():
            guard = patch.object(bbr, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.current = 'cubic'
        self.available = 'reno cubic bbr'
        self.calls = []
        guard = patch.object(bbr, 'run', side_effect=self.kernel)
        guard.start()
        self.addCleanup(guard.stop)
        guard = contextlib.redirect_stdout(io.StringIO())
        guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)

    def kernel(self, *args):
        self.calls.append(args)
        if args == ('sysctl', '-n', bbr.KEY):
            return self.current
        if args == ('sysctl', '-n', bbr.AVAILABLE):
            return self.available
        if args == ('modprobe', 'tcp_bbr'):
            self.available += ' bbr'
            return ''
        if args[:2] == ('sysctl', '-w'):
            self.current = args[2].split('=', 1)[1]
            return ''
        raise AssertionError(args)

    def test_status_is_read_only_and_handles_missing_sysctl(self):
        self.assertEqual(bbr.status(), '未开启（cubic）')
        self.current = 'bbr'
        self.assertEqual(bbr.status(), '已开启')
        with patch.object(bbr, 'run', side_effect=RuntimeError('missing')):
            self.assertEqual(bbr.status(), '状态不可用')
        self.assertFalse(self.config.exists())
        self.assertTrue(all(c[:2] == ('sysctl', '-n') for c in self.calls))

    def test_enable_persist_repeat_and_resume_missing_file(self):
        bbr.enable()
        self.assertEqual(self.current, 'bbr')
        self.assertEqual(self.config.read_text(), bbr.SETTINGS)
        self.assertEqual(self.modules.read_text(), bbr.LOAD_MODULE)
        before = self.config.stat().st_mtime_ns
        bbr.enable()
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.config.unlink()
        bbr.enable()
        self.assertEqual(self.config.read_text(), bbr.SETTINGS)

    def test_load_module_only_when_needed(self):
        self.available = 'reno cubic'
        bbr.enable()
        self.assertIn(('modprobe', 'tcp_bbr'), self.calls)
        self.assertEqual(self.current, 'bbr')

    def test_unsupported_kernel_keeps_runtime_and_files(self):
        self.available = 'reno cubic'
        kernel = self.kernel
        def fail_module(*args):
            if args[0] == 'modprobe':
                raise RuntimeError('denied')
            return kernel(*args)
        with patch.object(bbr, 'run', side_effect=fail_module):
            with self.assertRaisesRegex(RuntimeError, 'BBR_UNSUPPORTED'):
                bbr.enable()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())

    def test_unknown_managed_file_is_preserved(self):
        self.config.write_text('user settings')
        with self.assertRaisesRegex(RuntimeError, 'BBR_CONFIG_CONFLICT'):
            bbr.enable()
        self.assertEqual(self.config.read_text(), 'user settings')
        self.assertEqual(self.current, 'cubic')

    def test_later_boot_override_is_reported_before_changes(self):
        for path in (self.root / 'sysctl.conf', self.config.parent / 'zz-local.conf'):
            with self.subTest(path=path):
                path.write_text('net/ipv4/tcp_congestion_control = cubic\n')
                with self.assertRaisesRegex(RuntimeError, 'BBR_CONFIG_CONFLICT'):
                    bbr.enable()
                self.assertEqual(self.current, 'cubic')
                self.assertFalse(self.config.exists())
                path.unlink()

    def test_write_failure_rolls_back_runtime_and_created_file(self):
        writer = bbr.atomic_write
        def fail_sysctl(path, data):
            if path == self.config:
                raise OSError('disk full')
            writer(path, data)
        with patch.object(bbr, 'atomic_write', side_effect=fail_sysctl):
            with self.assertRaisesRegex(OSError, 'disk full'):
                bbr.enable()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())

    def test_kernel_refusal_is_verified_and_rolled_back(self):
        kernel = self.kernel
        def ignore_bbr(*args):
            if args == ('sysctl', '-w', bbr.KEY + '=bbr'):
                return ''
            return kernel(*args)
        with patch.object(bbr, 'run', side_effect=ignore_bbr):
            with self.assertRaisesRegex(RuntimeError, 'BBR_VERIFY_FAILED'):
                bbr.enable()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())

    def test_rollback_failure_has_clear_error(self):
        kernel = self.kernel
        def fail_rollback(*args):
            if args == ('sysctl', '-w', bbr.KEY + '=cubic'):
                raise RuntimeError('denied')
            return kernel(*args)
        with patch.object(bbr, 'run', side_effect=fail_rollback), \
                patch.object(bbr, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(RuntimeError, 'BBR_ROLLBACK_FAILED'):
                bbr.enable()

    def test_managed_symlink_is_rejected(self):
        target = self.root / 'other'
        target.write_text('retained')
        self.config.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'BBR_CONFIG_CONFLICT'):
            bbr.enable()
        self.assertEqual(target.read_text(), 'retained')

    def test_shared_lock_blocks_concurrent_enable(self):
        import fcntl
        with open(bbr.LOCK_PATH, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(sys, 'argv', ['bbr.py', 'enable']):
                with self.assertRaisesRegex(RuntimeError, 'INSTALL_IN_PROGRESS'):
                    bbr.main()
        self.assertEqual(self.current, 'cubic')
