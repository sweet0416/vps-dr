"""Exercise safe inherited project locks without touching system lock paths."""
import contextlib
import fcntl
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import locked


class LockedTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        guard = patch.object(locked, 'LOCK', self.root / 'vps-dr.lock')
        guard.start()
        self.addCleanup(guard.stop)
        self.directory = str(Path(locked.__file__).resolve().parent)

    def invoke(self, command, inherited=()):
        # Constants are supplied only to the isolated child module instance.
        script = ('import sys; from pathlib import Path; '
                  f'sys.path.insert(0,{self.directory!r}); import locked; '
                  f'locked.LOCK=Path({str(locked.LOCK)!r}); '
                  'locked.run(sys.argv[1:])')
        return subprocess.run([sys.executable, '-c', script, *command], text=True,
                              capture_output=True, timeout=5, pass_fds=inherited)

    def checker(self):
        return ('import os,sys; from pathlib import Path; '
                f'sys.path.insert(0,{self.directory!r}); import locked; '
                f'locked.LOCK=Path({str(locked.LOCK)!r}); '
                'locked.check(); assert os.environ["VPS_DR_LOCKED"]=="1"; '
                'assert os.get_inheritable(9); print("LOCK_INHERITED")')

    def test_exec_inherits_fd_and_environment_and_releases_after_exit(self):
        result = self.invoke([sys.executable, '-c', self.checker()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'LOCK_INHERITED')
        self.assertEqual(locked.LOCK.stat().st_mode & 0o777, 0o600)
        with locked.LOCK.open('r+') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_lock_content_is_not_truncated(self):
        locked.LOCK.write_text('retained')
        result = self.invoke([sys.executable, '-c', self.checker()])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(locked.LOCK.read_text(), 'retained')

    def test_symlink_fifo_hardlink_and_writable_file_are_rejected(self):
        other = self.root / 'retained'
        other.write_text('retained')
        locked.LOCK.symlink_to(other)
        result = self.invoke(['/bin/true'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(other.read_text(), 'retained')
        locked.LOCK.unlink()
        os.mkfifo(locked.LOCK)
        result = self.invoke(['/bin/true'])
        self.assertNotEqual(result.returncode, 0)
        locked.LOCK.unlink()
        os.link(other, locked.LOCK)
        result = self.invoke(['/bin/true'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(other.read_text(), 'retained')
        locked.LOCK.unlink()
        locked.LOCK.write_text('retained')
        locked.LOCK.chmod(0o666)
        result = self.invoke(['/bin/true'])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(locked.LOCK.read_text(), 'retained')

    def test_competing_process_cannot_acquire_held_lock(self):
        with locked.LOCK.open('w') as stream:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.invoke(['/bin/true'])
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('INSTALL_IN_PROGRESS', result.stderr)

    def test_check_rejects_another_file_or_replaced_lock_path(self):
        locked.LOCK.write_text('retained')
        other = self.root / 'other'
        other.write_text('other')
        with other.open('r') as stream, patch.object(locked, 'DESCRIPTOR', stream.fileno()):
            with self.assertRaisesRegex(RuntimeError, 'INSTALL_LOCK_UNSAFE'):
                locked.check()
        with locked.LOCK.open('r+') as stream, patch.object(locked, 'DESCRIPTOR', stream.fileno()):
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            locked.check()
            locked.LOCK.unlink()
            locked.LOCK.symlink_to(other)
            with self.assertRaisesRegex(RuntimeError, 'INSTALL_LOCK_UNSAFE'):
                locked.check()

    def test_already_descriptor_nine_is_kept_inheritable(self):
        # Reserve 3..8 so opening the lock itself returns descriptor 9.
        code = ('import os,sys; from pathlib import Path; '
                f'sys.path.insert(0,{self.directory!r}); import locked; '
                f'locked.LOCK=Path({str(locked.LOCK)!r}); '
                'fds=[os.open("/dev/null",os.O_RDONLY) for _ in range(6)]; '
                f'locked.run([{sys.executable!r},"-c",{self.checker()!r}])')
        result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                                text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('LOCK_INHERITED', result.stdout)

    def test_check_in_child_keeps_parent_lock_held(self):
        checker = self.checker()
        # locked.check() exits in a child; its shared OFD must leave the parent locked.
        inner = ('import subprocess,sys; '
                 f'check={checker!r}; '
                 'r=subprocess.run([sys.executable,"-c",check],pass_fds=(9,),capture_output=True,text=True); '
                 'assert r.returncode==0,r.stderr; '
                 'import fcntl; '
                 f'f=open({str(locked.LOCK)!r},"r+"); '
                 '\ntry: fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)'
                 '\nexcept BlockingIOError: print("PARENT_STILL_LOCKED")'
                 '\nelse: raise AssertionError("lock lost")')
        result = self.invoke([sys.executable, '-c', inner])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('PARENT_STILL_LOCKED', result.stdout)

    def test_nonroot_and_bad_arguments_are_rejected(self):
        with patch.object(os, 'geteuid', return_value=1000):
            for action in (locked.check, lambda: locked.run(['/bin/true'])):
                with self.assertRaisesRegex(RuntimeError, 'ROOT_REQUIRED'):
                    action()
        with self.assertRaisesRegex(RuntimeError, 'INSTALL_LOCK_USAGE'):
            locked.run([])
        self.assertFalse(locked.LOCK.exists())


if __name__ == '__main__':
    unittest.main()
