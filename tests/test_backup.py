"""Private backups and restore transactions use isolated files and simulated systemd."""
import contextlib
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
import unittest
import uuid
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import backup
import dr


class BackupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = self.root / 'state' / 'state.json'
        self.config = self.root / 'xray' / 'config.json'
        self.directory = self.root / 'private' / 'backups'
        self.unit = self.root / 'systemd' / 'xray.service'
        self.binary = self.root / 'bin' / 'xray'
        for parent in (self.state.parent, self.config.parent, self.unit.parent, self.binary.parent):
            parent.mkdir(mode=0o700)
        self.deployment = (backup.HERE / 'VERSION').read_text().strip()
        self.uid = str(uuid.uuid4())
        self.version = backup.defaults()['XRAY_VERSION']
        self.write_node(self.uid)
        self.original = (self.state.read_bytes(), self.config.read_bytes())
        for name, value in {'STATE': self.state, 'CONFIG': self.config, 'BACKUPS': self.directory,
                            'UNIT_PATH': self.unit, 'BINARY': self.binary,
                            'LOCK_PATH': self.root / 'lock'}.items():
            guard = patch.object(backup, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.trusted = patch.object(backup, 'trusted_program')
        self.trusted.start()
        self.addCleanup(self.trusted.stop)
        self.health = patch.object(backup, 'local_health')
        self.health_mock = self.health.start()
        self.addCleanup(self.health.stop)
        self.active = True
        self.fail_start = False
        self.calls = []
        guard = patch.object(backup, 'run', side_effect=self.system)
        guard.start()
        self.addCleanup(guard.stop)
        self.output = io.StringIO()
        guard = contextlib.redirect_stdout(self.output)
        guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)
        backup.prepare_directory()

    def write_node(self, uid, version=None):
        value = {'architecture': 'direct-xray-v1', 'uuid': uid,
                 'xray_version': version or self.version, 'deployment_version': self.deployment}
        self.state.write_text(json.dumps(value) + '\n')
        self.config.write_text(json.dumps(dr.profile(uid), indent=2) + '\n')
        self.state.chmod(0o600)
        self.config.chmod(0o600)

    def system(self, *args):
        self.calls.append(args)
        code, output = 0, ''
        if args == ('systemctl', 'show', '--property=ActiveState', '--value', backup.UNIT):
            output = 'active' if self.active else 'inactive'
        elif args == ('systemctl', 'show', '--property=FragmentPath', '--value', backup.UNIT):
            output = str(self.unit)
        elif args == ('systemctl', 'show', '--property=DropInPaths', '--value', backup.UNIT):
            pass
        elif args == ('systemctl', 'stop', backup.UNIT):
            self.active = False
        elif args == ('systemctl', 'start', backup.UNIT):
            if self.fail_start:
                self.fail_start = False
                code = 1
            else:
                self.active = True
        elif args == (str(self.binary), 'run', '-test', '-config', str(self.config)):
            pass
        elif args == ('ss', '-ltnH', '( sport = :443 )'):
            output = ':443 occupied' if self.active else ''
        else:
            raise AssertionError(args)
        return subprocess.CompletedProcess(args, code, output, '')

    def archive(self):
        uid = str(uuid.uuid4())
        state = json.loads(self.state.read_text())
        state['uuid'] = uid
        value = backup.envelope({'state.json': json.dumps(state) + '\n',
                                 'config.json': json.dumps(dr.profile(uid)) + '\n'})
        return backup.save(value), uid

    def run_cli(self, *args):
        with patch.object(sys, 'argv', ['backup.py', *args]):
            backup.main()

    def assert_original(self):
        self.assertEqual((self.state.read_bytes(), self.config.read_bytes()), self.original)

    def test_backup_is_private_roundtrip_and_output_has_no_credentials(self):
        self.run_cli('backup')
        files = list(self.directory.glob('vps-dr-*.json'))
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.directory.parent.stat().st_mode & 0o777, 0o700)
        value = backup.load_envelope(files[0])
        self.assertEqual(value['files']['state.json'].encode(), self.original[0])
        self.assertEqual(value['files']['config.json'].encode(), self.original[1])
        self.run_cli('list')
        self.assertNotIn(self.uid, self.output.getvalue())
        self.assertNotIn('vmess://', self.output.getvalue())

    def test_explicit_confirmation_and_absolute_safe_path_required(self):
        path, _ = self.archive()
        with self.assertRaisesRegex(RuntimeError, 'BACKUP_CONFIRM_REQUIRED'):
            backup.restore(path)
        for candidate in (Path('relative.json'), path.parent / '..' / 'backups' / path.name):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(RuntimeError, 'BACKUP_PATH_UNSAFE'):
                backup.restore(candidate, confirmed=True)
        self.assert_original()

    def test_restore_keeps_runtime_and_current_installed_versions(self):
        path, uid = self.archive()
        value = backup.load_envelope(path)
        state = json.loads(value['files']['state.json'])
        state['xray_version'] = 'v1.2.3'
        state['deployment_version'] = '0.1.0-dev'
        value = backup.envelope({**value['files'], 'state.json': json.dumps(state)})
        backup.atomic_write(path, backup.canonical(value))
        self.run_cli('restore', str(path), '--confirm')
        restored = json.loads(self.state.read_text())
        self.assertEqual(restored['uuid'], uid)
        self.assertEqual(restored['xray_version'], self.version)
        self.assertEqual(restored['deployment_version'], self.deployment)
        self.assertEqual(json.loads(self.config.read_text()), dr.profile(uid))
        self.assertTrue(self.active)
        self.assertTrue(self.health_mock.called)
        preserved = list(self.directory.glob('before-restore-*.json'))
        self.assertEqual(len(preserved), 1)
        self.assertEqual(backup.load_envelope(preserved[0])['files']['state.json'].encode(), self.original[0])
        self.assertFalse((self.directory / backup.JOURNAL_NAME).exists())
        self.assertNotIn(uid, self.output.getvalue())

    def test_stopped_node_stays_stopped(self):
        path, uid = self.archive()
        self.active = False
        backup.restore(path, confirmed=True)
        self.assertFalse(self.active)
        self.assertEqual(json.loads(self.state.read_text())['uuid'], uid)
        self.health_mock.assert_not_called()
        self.assertNotIn(('systemctl', 'start', backup.UNIT), self.calls)

    def test_checksum_mismatch_and_extra_paths_are_rejected_without_mutation(self):
        path, _ = self.archive()
        value = backup.load_envelope(path)
        value['sha256'] = '0' * 64
        backup.atomic_write(path, backup.canonical(value))
        with self.assertRaisesRegex(RuntimeError, 'BACKUP_INVALID'):
            backup.restore(path, confirmed=True)
        value['files']['../../root/other'] = 'unknown'
        body = {key: content for key, content in value.items() if key != 'sha256'}
        value['sha256'] = hashlib.sha256(backup.canonical(body)).hexdigest()
        backup.atomic_write(path, backup.canonical(value))
        with self.assertRaisesRegex(RuntimeError, 'BACKUP_INVALID'):
            backup.restore(path, confirmed=True)
        self.assert_original()
        self.assertEqual(self.calls, [])

    def test_resigned_mismatched_uuid_and_wrong_architecture_rejected(self):
        path, _ = self.archive()
        original = backup.load_envelope(path)
        for kind in ('uuid', 'machine'):
            value = json.loads(json.dumps(original))
            if kind == 'uuid':
                value['files']['config.json'] = json.dumps(dr.profile(str(uuid.uuid4())))
            else:
                value['machine'] = 'unsupported'
            body = {key: content for key, content in value.items() if key != 'sha256'}
            value['sha256'] = hashlib.sha256(backup.canonical(body)).hexdigest()
            backup.atomic_write(path, backup.canonical(value))
            with self.subTest(kind=kind), self.assertRaisesRegex(RuntimeError, 'BACKUP_INVALID'):
                backup.restore(path, confirmed=True)
        self.assert_original()

    def test_unsafe_permissions_symlink_and_oversize_rejected(self):
        path, _ = self.archive()
        path.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'BACKUP_FILE_UNSAFE'):
            backup.load_envelope(path)
        path.chmod(0o600)
        link = self.directory / 'link.json'
        link.symlink_to(path)
        with self.assertRaises(OSError):
            backup.load_envelope(link)
        backup.atomic_write(path, b'x' * (backup.LIMIT + 1))
        with self.assertRaisesRegex(RuntimeError, 'BACKUP_FILE_UNSAFE'):
            backup.load_envelope(path)
        self.assert_original()

    def test_write_failure_rolls_back_exact_bytes_and_original_runtime(self):
        path, _ = self.archive()
        writer = backup.atomic_write
        failed = False
        def failing_writer(target, data):
            nonlocal failed
            if target == self.state and not failed:
                failed = True
                raise OSError('simulated disk failure')
            return writer(target, data)
        with patch.object(backup, 'atomic_write', side_effect=failing_writer):
            with self.assertRaisesRegex(OSError, 'simulated disk failure'):
                backup.restore(path, confirmed=True)
        self.assert_original()
        self.assertTrue(self.active)
        self.assertFalse((self.directory / backup.JOURNAL_NAME).exists())
        self.assertTrue(list(self.directory.glob('before-restore-*.json')))

    def test_failed_start_rolls_back(self):
        path, _ = self.archive()
        self.fail_start = True
        with self.assertRaisesRegex(RuntimeError, 'BACKUP_SERVICE_FAILED'):
            backup.restore(path, confirmed=True)
        self.assert_original()
        self.assertTrue(self.active)
        self.assertFalse((self.directory / backup.JOURNAL_NAME).exists())

    def interrupted(self):
        path, _ = self.archive()
        before, _ = backup.current_node()
        target = backup.load_envelope(path)
        journal = {'schema': 'vps-dr-restore-v1', 'before': before, 'target': target, 'active': True}
        backup.atomic_write(self.directory / backup.JOURNAL_NAME, backup.canonical(journal))
        self.active = False
        backup.atomic_write(self.config, target['files']['config.json'].encode())

    def test_next_entry_rolls_back_interrupted_mixed_pair(self):
        self.interrupted()
        self.trusted.stop()
        self.addCleanup(self.trusted.start)
        checked = []
        def verify(state_path, check_unit=True, check_config=True):
            checked.append(check_config)
            state = dr.read_state(state_path)
            if check_config and not dr.config_matches(self.config, state['uuid']):
                raise RuntimeError('CONFIG_CONFLICT')
            return {'version': state['xray_version']}
        verifier = types.SimpleNamespace(verify_installed=verify)
        with patch.dict(sys.modules, {'update': verifier}):
            self.run_cli('recover')
        self.assertEqual(checked, [False])
        self.assert_original()
        self.assertTrue(self.active)
        self.assertFalse((self.directory / backup.JOURNAL_NAME).exists())

    def test_post_interruption_unknown_edit_is_preserved_with_journal(self):
        self.interrupted()
        self.config.write_text('manually edited')
        with self.assertRaisesRegex(RuntimeError, 'BACKUP_RECOVERY_CONFLICT'):
            self.run_cli('recover')
        self.assertEqual(self.config.read_text(), 'manually edited')
        self.assertTrue((self.directory / backup.JOURNAL_NAME).exists())
        self.assertFalse(self.active)

    def test_rollback_failure_retains_journal_for_retry(self):
        self.interrupted()
        with patch.object(backup, 'apply_files', side_effect=OSError('full')):
            with self.assertRaisesRegex(RuntimeError, 'BACKUP_ROLLBACK_FAILED'):
                backup.recover()
        self.assertTrue((self.directory / backup.JOURNAL_NAME).exists())
        self.run_cli('recover')
        self.assert_original()
        self.assertTrue(self.active)

    def test_shared_lock_blocks_operations_and_no_pending_recover_skips_lock(self):
        with open(backup.LOCK_PATH, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_cli('recover')
            with self.assertRaisesRegex(RuntimeError, 'INSTALL_IN_PROGRESS'):
                self.run_cli('backup')
        self.assert_original()

    def test_unknown_program_or_unit_blocks_restore(self):
        path, _ = self.archive()
        with patch.object(backup, 'trusted_program', side_effect=RuntimeError('BACKUP_UNIT_CONFLICT')):
            with self.assertRaisesRegex(RuntimeError, 'BACKUP_UNIT_CONFLICT'):
                backup.restore(path, confirmed=True)
        self.assert_original()
        self.assertTrue(self.active)

    def test_program_verifier_uses_trusted_update_manifest_and_effective_unit(self):
        self.trusted.stop()
        self.addCleanup(self.trusted.start)
        verifier = types.SimpleNamespace(verify_installed=lambda *a, **k: {'version': self.version})
        with patch.dict(sys.modules, {'update': verifier}):
            backup.trusted_program()
            system = self.system
            def override(*args):
                if '--property=DropInPaths' in args:
                    return subprocess.CompletedProcess(args, 0, '/etc/systemd/system/xray.service.d/override.conf', '')
                return system(*args)
            with patch.object(backup, 'run', side_effect=override):
                with self.assertRaisesRegex(RuntimeError, 'BACKUP_UNIT_CONFLICT'):
                    backup.trusted_program()


if __name__ == '__main__':
    unittest.main()
