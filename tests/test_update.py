"""Core updates use temporary files and stub commands, never the host service."""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import dr
import update
import backup


class CoreUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state_dir = self.root / 'state'
        self.state_dir.mkdir(mode=0o700)
        self.state = self.state_dir / 'state.json'
        self.trust = self.state_dir / 'core.json'
        self.transaction = self.state_dir / 'core-update'
        self.binary = self.root / 'xray'
        self.binary.write_bytes(b'old-official-core')
        self.binary.chmod(0o755)
        self.config = self.root / 'config.json'
        self.uid = str(uuid.uuid4())
        dr.write_state(self.state, self.uid, 'v26.3.27', '1.1.0')
        dr.render_config(self.config, self.uid)
        self.unit = self.root / 'xray.service'
        self.unit.write_bytes((update.ROOT / 'config/xray.service').read_bytes())
        self.service_state = 'active'
        self.commands = []
        self.known = {'XRAY_VERSION': 'v26.3.27', 'XRAY_SHA256_AMD64': '1' * 64,
                      'XRAY_BINARY_SHA256_AMD64': update.sha(self.binary)}
        for name, value in [('STATE', self.state), ('TRUST', self.trust),
                            ('TRANSACTION', self.transaction), ('XRAY', self.binary),
                            ('CONFIG', self.config), ('UNIT_FILE', self.unit),
                            ('LOCK', self.root / 'lock')]:
            patcher = patch.object(update, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for name, value in [('machine', lambda: 'x86_64'), ('defaults', lambda: self.known)]:
            patcher = patch.object(update, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.object(update, 'command', side_effect=self.command)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = patch.object(backup, 'BACKUPS', self.root / 'backups')
        patcher.start()
        self.addCleanup(patcher.stop)
        self.output = StringIO()
        patcher = redirect_stdout(self.output)
        patcher.__enter__()
        self.addCleanup(patcher.__exit__, None, None, None)

    def command(self, *args):
        self.commands.append(args)
        code, output = 0, ''
        if args[-1:] == ('version',):
            version = '26.3.27' if Path(args[0]).read_bytes() == b'old-official-core' else '26.11.9'
            output = 'Xray ' + version + ' (Xray, Penetrates Everything.)'
        elif '--property=FragmentPath' in args:
            output = str(self.unit)
        elif '--property=DropInPaths' in args:
            output = ''
        elif '--property=ActiveState' in args:
            output = self.service_state
        elif args[:2] == ('systemctl', 'is-active'):
            code = 0 if self.service_state == 'active' else 3
        elif args[:2] == ('systemctl', 'restart'):
            self.service_state = 'active'
        elif args[:2] == ('systemctl', 'stop'):
            self.service_state = 'inactive'
        return subprocess.CompletedProcess(args, code, output, '')

    def replacement(self):
        path = self.root / 'new'
        path.write_bytes(b'new-official-core')
        return path, {'schema': 1, 'version': 'v26.11.9', 'machine': 'x86_64',
                      'archive_sha256': '2' * 64, 'binary_sha256': update.sha(path)}

    def do_upgrade(self, health=None):
        with (patch.object(update, 'latest', return_value=('v26.11.9', {}, {})),
              patch.object(update, 'package', return_value=self.replacement()),
              patch.object(update, 'local_health', side_effect=health)):
            update.upgrade()

    def test_partial_install_binary_check_allows_missing_config_and_unit(self):
        self.config.unlink()
        self.unit.unlink()
        self.assertEqual(update.verify_installed(self.state, check_unit=False, check_config=False)['version'],
                         'v26.3.27')
        with self.assertRaisesRegex(RuntimeError, 'CORE_FILE_MISSING'):
            update.verify_installed(self.state)

    def test_unknown_state_or_trust_permissions_are_rejected(self):
        self.state.chmod(0o644)
        with self.assertRaisesRegex(RuntimeError, 'CORE_PATH_UNSAFE'):
            update.verify_installed(self.state)
        self.state.chmod(0o600)
        self.trust.symlink_to(self.state)
        with self.assertRaisesRegex(RuntimeError, 'CORE_PATH_UNSAFE'):
            update.verify_installed(self.state)

    def test_pinned_core_trusted_and_unknown_binary_rejected_before_execution(self):
        self.assertEqual(update.verify_installed(self.state)['version'], 'v26.3.27')
        self.binary.write_bytes(b'unknown-core')
        self.commands.clear()
        with self.assertRaisesRegex(RuntimeError, 'EXISTING_XRAY_CONFLICT'):
            update.verify_installed(self.state)
        self.assertFalse(self.commands)

    def test_success_preserves_uuid_config_and_existing_running_state(self):
        config = self.config.read_bytes()
        self.do_upgrade()
        current = dr.read_state(self.state)
        self.assertEqual(current['uuid'], self.uid)
        self.assertEqual(current['xray_version'], 'v26.11.9')
        self.assertEqual(self.config.read_bytes(), config)
        self.assertEqual(update.verify_installed(self.state)['binary_sha256'], update.sha(self.binary))
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.service_state, 'active')

    def test_stopped_service_stays_stopped(self):
        self.service_state = 'inactive'
        self.do_upgrade()
        self.assertEqual(self.service_state, 'inactive')
        self.assertNotIn(('systemctl', 'restart', update.UNIT), self.commands)

    def test_failed_or_transitional_service_blocks_download(self):
        with (patch.object(update, 'latest', return_value=('v26.11.9', {}, {})),
              patch.object(update, 'package') as download):
            for status in ('failed', 'activating', 'deactivating', 'reloading'):
                with self.subTest(status=status):
                    self.service_state = status
                    with self.assertRaisesRegex(RuntimeError, 'CORE_SERVICE_UNHEALTHY'):
                        update.upgrade()
                    self.assertEqual(self.service_state, status)
        download.assert_not_called()
        self.assertFalse(self.transaction.exists())

    def test_state_change_during_download_preserves_service_and_files(self):
        before = self.state.read_bytes()
        for initial, after, error in [('inactive', 'active', 'CORE_CHANGED_DURING_DOWNLOAD'),
                                      ('active', 'inactive', 'CORE_CHANGED_DURING_DOWNLOAD'),
                                      ('active', 'failed', 'CORE_SERVICE_UNHEALTHY')]:
            with self.subTest(initial=initial, after=after):
                self.service_state = initial
                self.commands.clear()

                def download(*args):
                    self.service_state = after
                    return self.replacement()

                with (patch.object(update, 'latest', return_value=('v26.11.9', {}, {})),
                      patch.object(update, 'package', side_effect=download),
                      patch.object(update, 'local_health')):
                    with self.assertRaisesRegex(RuntimeError, error):
                        update.upgrade()
                self.assertEqual(self.service_state, after)
                self.assertNotIn(('systemctl', 'restart', update.UNIT), self.commands)
                self.assertNotIn(('systemctl', 'stop', update.UNIT), self.commands)
                self.assertEqual(self.binary.read_bytes(), b'old-official-core')
                self.assertEqual(self.state.read_bytes(), before)
                self.assertFalse(self.trust.exists())
                self.assertFalse(self.transaction.exists())

    def test_transaction_parent_sync_failure_prevents_switch(self):
        sync_dir = update.sync_dir
        before = self.state.read_bytes()

        def fail_parent_sync(path):
            if path == self.transaction.parent:
                raise OSError('cannot sync transaction parent')
            sync_dir(path)

        with patch.object(update, 'sync_dir', side_effect=fail_parent_sync):
            with self.assertRaisesRegex(OSError, 'cannot sync transaction parent'):
                self.do_upgrade()
        self.assertEqual(self.binary.read_bytes(), b'old-official-core')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(self.trust.exists())
        self.assertTrue(self.transaction.is_dir())
        self.assertFalse((self.transaction / 'journal.json').exists())
        self.assertTrue(update.recover())
        self.assertFalse(self.transaction.exists())
        self.assertNotIn(('systemctl', 'restart', update.UNIT), self.commands)

    def test_health_failure_rolls_back_state_core_and_trust(self):
        before = self.state.read_bytes()
        self.do_upgrade_health_failure(before)

    def do_upgrade_health_failure(self, before):
        calls = 0
        def health():
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('new core unhealthy')
        with self.assertRaisesRegex(RuntimeError, 'CORE_UPDATE_FAILED'):
            self.do_upgrade(health)
        self.assertEqual(json.loads(self.state.read_bytes()), json.loads(before))
        self.assertEqual(self.binary.read_bytes(), b'old-official-core')
        self.assertFalse(self.trust.exists())
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.service_state, 'active')

    def test_commit_write_failure_restores_old_binary_and_records(self):
        writer = update.atomic
        failed = False
        def fail_trust(path, content, mode=0o600):
            nonlocal failed
            if path == self.trust and not failed:
                failed = True
                raise OSError('disk full')
            return writer(path, content, mode)
        with patch.object(update, 'atomic', side_effect=fail_trust):
            with self.assertRaisesRegex(RuntimeError, 'CORE_UPDATE_FAILED'):
                self.do_upgrade()
        self.assertEqual(self.binary.read_bytes(), b'old-official-core')
        self.assertEqual(dr.read_state(self.state)['xray_version'], 'v26.3.27')
        self.assertFalse(self.trust.exists())
        self.assertFalse(self.transaction.exists())

    def test_recovery_failure_keeps_durable_journal(self):
        calls = 0
        def health():
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise RuntimeError('service refuses to start')
        with self.assertRaisesRegex(RuntimeError, 'CORE_ROLLBACK_FAILED'):
            self.do_upgrade(health)
        self.assertTrue((self.transaction / 'journal.json').is_file())
        with patch.object(update, 'local_health'):
            self.assertTrue(update.recover())
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.binary.read_bytes(), b'old-official-core')

    def assert_completed_cleanup_interruption(self, removed):
        cleaner = update.shutil.rmtree
        interrupted = False
        def die_during_cleanup(path, *args, **kwargs):
            nonlocal interrupted
            if Path(path) == update.completed_path() and not interrupted:
                interrupted = True
                (Path(path) / removed).unlink()
                raise SystemExit('simulated kill during completed cleanup')
            return cleaner(path, *args, **kwargs)
        with patch.object(update.shutil, 'rmtree', side_effect=die_during_cleanup):
            with self.assertRaisesRegex(SystemExit, 'simulated kill'):
                self.do_upgrade()
        self.assertFalse(self.transaction.exists())
        self.assertTrue(update.completed_path().exists())
        self.assertEqual(self.binary.read_bytes(), b'new-official-core')
        self.assertEqual(dr.read_state(self.state)['xray_version'], 'v26.11.9')
        self.commands.clear()
        self.assertTrue(update.recover())
        self.assertEqual(update.verify_installed(self.state)['version'], 'v26.11.9')
        self.assertFalse(update.completed_path().exists())
        self.assertNotIn(('systemctl', 'restart', update.UNIT), self.commands)

    def test_completed_cleanup_kill_after_journal_delete_keeps_new_core(self):
        self.assert_completed_cleanup_interruption('journal.json')

    def test_completed_cleanup_kill_after_backup_delete_keeps_new_core(self):
        self.assert_completed_cleanup_interruption('xray.before')

    def test_download_failure_leaves_all_managed_files_unchanged(self):
        before = self.state.read_bytes()
        with (patch.object(update, 'latest', return_value=('v26.11.9', {}, {})),
              patch.object(update, 'package', side_effect=RuntimeError('download failed')),
              patch.object(update, 'local_health')):
            with self.assertRaisesRegex(RuntimeError, 'download failed'):
                update.upgrade()
        self.assertEqual(self.binary.read_bytes(), b'old-official-core')
        self.assertEqual(self.state.read_bytes(), before)
        self.assertFalse(self.trust.exists())
        self.assertFalse(self.transaction.exists())

    def pending(self):
        old = update.verify_installed(self.state)
        before = dr.read_state(self.state)
        replacement, new = self.replacement()
        self.transaction.mkdir(mode=0o700)
        update.atomic(self.transaction / 'xray.before', self.binary.read_bytes())
        record = {'schema': 1, 'old': old, 'new': new, 'active': True, 'state': before, 'trust': None}
        update.atomic(self.transaction / 'journal.json', update.encode(record))
        update.atomic(self.binary, replacement.read_bytes(), 0o755)
        update.atomic(self.trust, update.encode(new))
        update.atomic(self.state, update.encode(dict(before, xray_version=new['version'])))

    def test_interrupted_commit_recovers_on_next_operation(self):
        self.pending()
        with patch.object(update, 'local_health'):
            self.assertTrue(update.recover())
        self.assertEqual(self.binary.read_bytes(), b'old-official-core')
        self.assertEqual(dr.read_state(self.state)['xray_version'], 'v26.3.27')
        self.assertFalse(self.transaction.exists())

    def test_recovery_rejects_unknown_binary_and_preserves_journal(self):
        self.pending()
        self.binary.write_bytes(b'user-modified-core')
        with self.assertRaisesRegex(RuntimeError, 'CORE_RECOVERY_BLOCKED'):
            update.recover()
        self.assertEqual(self.binary.read_bytes(), b'user-modified-core')
        self.assertTrue((self.transaction / 'journal.json').is_file())

    def test_recovery_rejects_changed_state_without_overwriting(self):
        self.pending()
        state = dr.read_state(self.state)
        state['uuid'] = str(uuid.uuid4())
        update.atomic(self.state, update.encode(state))
        with self.assertRaisesRegex(RuntimeError, 'CORE_RECOVERY_BLOCKED'):
            update.recover()
        self.assertEqual(dr.read_state(self.state), state)

    def test_preparation_interruption_can_be_cleared_without_changes(self):
        self.transaction.mkdir(mode=0o700)
        update.atomic(self.transaction / 'xray.before', self.binary.read_bytes())
        self.assertTrue(update.recover())
        self.assertFalse(self.transaction.exists())
        self.assertEqual(self.binary.read_bytes(), b'old-official-core')

    def test_unit_dropin_prevents_upgrade(self):
        previous = self.command
        def changed(*args):
            if '--property=DropInPaths' in args:
                return subprocess.CompletedProcess(args, 0, '/etc/systemd/system/xray.service.d/custom.conf', '')
            return previous(*args)
        with patch.object(update, 'command', side_effect=changed):
            with self.assertRaisesRegex(RuntimeError, 'EXISTING_XRAY_CONFLICT'):
                update.verify_installed(self.state)

    def test_lock_blocks_parallel_upgrade(self):
        with update.locked():
            with self.assertRaisesRegex(RuntimeError, 'INSTALL_IN_PROGRESS'):
                with update.locked():
                    self.fail('concurrent lock acquired')

    def test_current_release_does_not_restart_or_download(self):
        with (patch.object(update, 'latest', return_value=('v26.3.27', {}, {})),
              patch.object(update, 'package') as download):
            update.upgrade()
        download.assert_not_called()
        self.assertNotIn(('systemctl', 'restart', update.UNIT), self.commands)

    def test_package_verifies_digest_api_and_binary_before_accepting(self):
        packaged = BytesIO()
        with zipfile.ZipFile(packaged, 'w') as source:
            source.writestr('xray', b'new-official-core')
        zipped = packaged.getvalue()
        summary = ('SHA2-256= ' + hashlib.sha256(zipped).hexdigest() + '\n').encode()
        archive = {'browser_download_url': 'official.zip', 'digest': 'sha256:' + hashlib.sha256(zipped).hexdigest()}
        digest = {'browser_download_url': 'official.dgst', 'digest': 'sha256:' + hashlib.sha256(summary).hexdigest()}
        def download(url, target):
            target.write_bytes(zipped if url == 'official.zip' else summary)
        with tempfile.TemporaryDirectory(dir=self.root) as directory, patch.object(update, 'download', side_effect=download):
            binary, trusted = update.package('v26.11.9', archive, digest, Path(directory))
            self.assertEqual(binary.read_bytes(), b'new-official-core')
            self.assertEqual(trusted['binary_sha256'], update.sha(binary))
        archive['digest'] = 'sha256:' + '0' * 64
        with tempfile.TemporaryDirectory(dir=self.root) as directory, patch.object(update, 'download', side_effect=download):
            with self.assertRaisesRegex(RuntimeError, 'CHECKSUM_MISMATCH'):
                update.package('v26.11.9', archive, digest, Path(directory))

    def test_stable_release_filter_rejects_prerelease_and_wrong_asset_origin(self):
        release = {'tag_name': 'v26.11.9', 'draft': False, 'prerelease': False,
                   'assets': [{'name': name, 'browser_download_url': update.DOWNLOAD + 'v26.11.9/' + name}
                              for name in ('Xray-linux-64.zip', 'Xray-linux-64.zip.dgst')]}
        def response():
            value = BytesIO(json.dumps(release).encode())
            return value
        with patch.object(update.urllib.request, 'urlopen', side_effect=lambda *a, **k: response()):
            self.assertEqual(update.latest()[0], 'v26.11.9')
            release['prerelease'] = True
            with self.assertRaisesRegex(RuntimeError, 'CORE_UPDATE_CHECK_FAILED'):
                update.latest()
            release['prerelease'] = False
            release['assets'][0]['browser_download_url'] = 'https://example.com/unknown.zip'
            with self.assertRaisesRegex(RuntimeError, 'CORE_UPDATE_CHECK_FAILED'):
                update.latest()


if __name__ == '__main__':
    unittest.main()
