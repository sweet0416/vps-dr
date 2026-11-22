"""BBR regressions use a simulated kernel and temporary persistence paths."""
import contextlib
import io
import json
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
        self.original = self.root / 'private' / 'bbr-original.json'
        self.config.parent.mkdir()
        self.modules.parent.mkdir()
        for name, value in dict(CONFIG=self.config, MODULES=self.modules,
                                SYSCTL_DIRS=(self.config.parent,),
                                SYSCTL_CONF=self.root / 'sysctl.conf',
                                LOCK_PATH=self.root / 'lock', ORIGINAL=self.original).items():
            guard = patch.object(bbr, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.current = 'cubic'
        self.available = 'reno cubic bbr'
        self.calls = []
        guard = patch.object(bbr, 'run', side_effect=self.kernel)
        guard.start()
        self.addCleanup(guard.stop)
        self.output = io.StringIO()
        guard = contextlib.redirect_stdout(self.output)
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
        original = self.original.read_bytes()
        self.assertEqual(self.original.stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(original)['algorithm'], 'cubic')
        before = self.config.stat().st_mtime_ns
        bbr.enable()
        self.assertEqual(self.config.stat().st_mtime_ns, before)
        self.assertEqual(self.original.read_bytes(), original)
        self.config.unlink()
        bbr.enable()
        self.assertEqual(self.config.read_text(), bbr.SETTINGS)
        self.assertEqual(self.original.read_bytes(), original)

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
        def fail_sysctl(path, data, mode=0o644):
            if path == self.config:
                raise OSError('disk full')
            writer(path, data, mode)
        with patch.object(bbr, 'atomic_write', side_effect=fail_sysctl):
            with self.assertRaisesRegex(OSError, 'disk full'):
                bbr.enable()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())
        self.assertFalse(self.original.exists())

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
        writer = bbr.atomic_write
        def fail_rollback(*args):
            if args == ('sysctl', '-w', bbr.KEY + '=cubic'):
                raise RuntimeError('denied')
            return kernel(*args)
        def fail_config(path, data, mode=0o644):
            if path == self.config:
                raise OSError('disk full')
            return writer(path, data, mode)
        with patch.object(bbr, 'run', side_effect=fail_rollback), \
                patch.object(bbr, 'atomic_write', side_effect=fail_config):
            with self.assertRaisesRegex(RuntimeError, 'BBR_ROLLBACK_FAILED'):
                bbr.enable()
        self.assertTrue(self.original.exists())

    def test_managed_symlink_is_rejected(self):
        target = self.root / 'other'
        target.write_text('retained')
        self.config.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'BBR_CONFIG_CONFLICT'):
            bbr.enable()
        self.assertEqual(target.read_text(), 'retained')

    def test_shared_lock_blocks_all_mutating_actions(self):
        import fcntl
        with open(bbr.LOCK_PATH, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for action in ('enable', 'restore', 'cubic'):
                with self.subTest(action=action), patch.object(sys, 'argv', ['bbr.py', action]):
                    with self.assertRaisesRegex(RuntimeError, 'INSTALL_IN_PROGRESS'):
                        bbr.main()
        self.assertEqual(self.current, 'cubic')

    def test_inspect_reports_current_available_files_and_history_without_writes(self):
        bbr.inspect()
        self.assertIn('当前 TCP 算法：cubic', self.output.getvalue())
        self.assertIn('内核可用算法：reno、cubic、bbr', self.output.getvalue())
        self.assertIn('无可用历史记录', self.output.getvalue())
        self.assertFalse(self.original.exists())
        self.assertTrue(all(c[:2] == ('sysctl', '-n') for c in self.calls))
        bbr.enable()
        bbr.inspect()
        self.assertIn('可恢复（原算法 cubic）', self.output.getvalue())
        self.assertIn('本项目 BBR 开机设置：已保存', self.output.getvalue())

    def test_restore_original_runtime_and_missing_project_files(self):
        bbr.enable()
        bbr.restore()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())
        self.assertFalse(self.original.exists())
        with self.assertRaisesRegex(RuntimeError, 'BBR_NO_HISTORY'):
            bbr.restore()

    def test_restore_existing_managed_files_and_their_modes(self):
        self.config.write_text(bbr.SETTINGS)
        self.config.chmod(0o600)
        self.modules.write_text(bbr.LOAD_MODULE)
        self.modules.chmod(0o400)
        bbr.enable()
        self.config.unlink()
        self.modules.chmod(0o644)
        bbr.restore()
        self.assertEqual(self.current, 'cubic')
        self.assertEqual(self.config.read_text(), bbr.SETTINGS)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.modules.stat().st_mode & 0o777, 0o400)
        self.assertFalse(self.original.exists())

    def test_restore_preexisting_system_bbr(self):
        self.current = 'bbr'
        other = self.config.parent / '10-image.conf'
        other.write_text(bbr.KEY + '=bbr\n')
        bbr.enable()
        self.assertEqual(json.loads(self.original.read_bytes())['algorithm'], 'bbr')
        bbr.restore()
        self.assertEqual(self.current, 'bbr')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())
        self.assertFalse(self.original.exists())
        self.assertEqual(other.read_text(), bbr.KEY + '=bbr\n')

    def test_restore_native_bbr_from_previous_release_record(self):
        self.current = 'bbr'
        bbr.save_original({'schema': 1, 'algorithm': None, 'config': None, 'modules': None})
        self.config.write_text(bbr.SETTINGS)
        self.modules.write_text(bbr.LOAD_MODULE)
        bbr.restore()
        self.assertEqual(self.current, 'bbr')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())
        self.assertFalse(self.original.exists())

    def test_old_bbr_has_no_invented_original_algorithm(self):
        self.current = 'bbr'
        self.config.write_text(bbr.SETTINGS)
        with self.assertRaisesRegex(RuntimeError, 'BBR_NO_HISTORY'):
            bbr.restore()
        bbr.enable()
        self.assertIsNone(json.loads(self.original.read_bytes())['algorithm'])
        with self.assertRaisesRegex(RuntimeError, 'BBR_NO_HISTORY'):
            bbr.restore()
        self.assertEqual(self.current, 'bbr')
        self.assertTrue(self.config.exists())
        bbr.cubic()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())
        self.assertFalse(self.original.exists())
        bbr.enable()
        self.assertEqual(json.loads(self.original.read_bytes())['algorithm'], 'cubic')

    def test_cubic_keeps_known_original_and_other_system_configuration(self):
        bbr.enable()
        other = self.config.parent / 'zz-local.conf'
        other.write_text(bbr.KEY + '=bbr\n')
        bbr.cubic()
        self.assertEqual(self.current, 'cubic')
        self.assertEqual(other.read_text(), bbr.KEY + '=bbr\n')
        self.assertTrue(self.original.exists())
        self.assertIn('重启后的算法由其它系统配置', self.output.getvalue())
        bbr.restore()
        self.assertFalse(self.original.exists())

    def test_restore_resumes_after_interrupted_removal(self):
        bbr.enable()
        # A killed restore may have switched the kernel and removed just one file.
        self.current = 'cubic'
        self.modules.unlink()
        bbr.restore()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.original.exists())

    def test_unknown_files_block_restore_and_cubic_without_kernel_changes(self):
        bbr.enable()
        original = self.original.read_bytes()
        self.modules.write_text('administrator module\n')
        for action in (bbr.restore, bbr.cubic):
            with self.subTest(action=action.__name__):
                with self.assertRaisesRegex(RuntimeError, 'BBR_CONFIG_CONFLICT'):
                    action()
                self.assertEqual(self.current, 'bbr')
                self.assertEqual(self.modules.read_text(), 'administrator module\n')
                self.assertEqual(self.original.read_bytes(), original)

    def test_restore_unavailable_original_algorithm_keeps_everything(self):
        bbr.enable()
        self.available = 'reno bbr'
        with self.assertRaisesRegex(RuntimeError, 'BBR_RESTORE_UNAVAILABLE'):
            bbr.restore()
        self.assertEqual(self.current, 'bbr')
        self.assertTrue(self.config.exists())
        self.assertTrue(self.original.exists())

    def test_restore_removal_failure_rolls_back_and_retains_history(self):
        bbr.enable()
        original = self.original.read_bytes()
        remover = bbr.put_file
        def fail_config(path, item):
            if path == self.config and item is None:
                raise OSError('read-only filesystem')
            return remover(path, item)
        with patch.object(bbr, 'put_file', side_effect=fail_config):
            with self.assertRaisesRegex(OSError, 'read-only filesystem'):
                bbr.restore()
        self.assertEqual(self.current, 'bbr')
        self.assertEqual(self.modules.read_text(), bbr.LOAD_MODULE)
        self.assertEqual(self.config.read_text(), bbr.SETTINGS)
        self.assertEqual(self.original.read_bytes(), original)
        bbr.restore()
        self.assertEqual(self.current, 'cubic')

    def test_restore_history_cleanup_failure_rolls_back(self):
        bbr.enable()
        cleaner = bbr.clear_original
        def fail_after_clear():
            cleaner()
            raise OSError('directory sync failed')
        with patch.object(bbr, 'clear_original', side_effect=fail_after_clear):
            with self.assertRaisesRegex(OSError, 'directory sync failed'):
                bbr.restore()
        self.assertEqual(self.current, 'bbr')
        self.assertEqual(self.config.read_text(), bbr.SETTINGS)
        self.assertEqual(self.modules.read_text(), bbr.LOAD_MODULE)
        self.assertEqual(json.loads(self.original.read_bytes())['algorithm'], 'cubic')

    def test_unsafe_or_corrupt_original_record_is_preserved(self):
        bbr.enable()
        pristine = self.original.read_bytes()
        for data in (b'not json', b'[]', b'{"schema": true}',
                     json.dumps({'schema': 1, 'algorithm': 'cubic', 'config': {
                         'content': 'unknown', 'mode': 0o600}, 'modules': None}).encode()):
            with self.subTest(data=data):
                self.original.write_bytes(data)
                with self.assertRaisesRegex(RuntimeError, 'BBR_HISTORY_INVALID'):
                    bbr.restore()
                self.assertEqual(self.original.read_bytes(), data)
                self.assertEqual(self.current, 'bbr')
        self.original.write_bytes(pristine)
        self.original.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'BBR_HISTORY_UNSAFE'):
            bbr.restore()
        self.original.chmod(0o600)
        self.original.unlink()
        target = self.root / 'history'
        target.write_bytes(pristine)
        self.original.symlink_to(target)
        with self.assertRaisesRegex(RuntimeError, 'BBR_CONFIG_CONFLICT'):
            bbr.restore()
        self.assertEqual(target.read_bytes(), pristine)

    def test_original_record_failure_prevents_runtime_or_persistence_changes(self):
        with patch.object(bbr, 'atomic_write', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                bbr.enable()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())
        self.assertFalse(self.original.exists())

    def test_interrupt_rolls_back_and_clears_new_history(self):
        writer = bbr.put_file
        def interrupt_config(path, item):
            if path == self.config and item is not None:
                raise KeyboardInterrupt()
            return writer(path, item)
        with patch.object(bbr, 'put_file', side_effect=interrupt_config):
            with self.assertRaises(KeyboardInterrupt):
                bbr.enable()
        self.assertEqual(self.current, 'cubic')
        self.assertFalse(self.config.exists())
        self.assertFalse(self.modules.exists())
        self.assertFalse(self.original.exists())
