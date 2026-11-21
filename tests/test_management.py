"""Upgrade regressions: preserve state, reject unknown edits, recover interrupted swaps."""
import hashlib
import json
from contextlib import redirect_stdout
from io import StringIO
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import manage
import dr


class ManagementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.target = self.root / 'vps-dr'
        self.output = StringIO()
        self.capture = redirect_stdout(self.output)
        self.capture.__enter__()
        self.addCleanup(self.capture.__exit__, None, None, None)

    def seed(self):
        for name in manage.FILES:
            dest = self.target / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / name, dest)

    def test_caidan_shortcut_create_repeat_and_conflict(self):
        command = self.root / 'bin' / 'caidan'
        command.parent.mkdir()
        manage.shortcut(ROOT, command, check_only=True)
        self.assertFalse(command.exists())
        manage.shortcut(ROOT, command)
        self.assertEqual(command.read_bytes(), (ROOT/'scripts/caidan.sh').read_bytes())
        self.assertEqual(command.stat().st_mode & 0o777, 0o755)
        before = command.stat().st_mtime_ns
        manage.shortcut(ROOT, command)
        self.assertEqual(command.stat().st_mtime_ns, before)
        command.write_text('existing user command')
        with self.assertRaisesRegex(ValueError, '未知来源'):
            manage.shortcut(ROOT, command)
        self.assertEqual(command.read_text(), 'existing user command')

    def test_caidan_failed_write_keeps_existing_command(self):
        command = self.root / 'caidan'
        command.write_bytes((ROOT/'scripts/caidan.sh').read_bytes())
        command.chmod(0o644)
        before = command.read_bytes()
        with patch.object(manage.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                manage.shortcut(ROOT, command)
        self.assertEqual(command.read_bytes(), before)
        self.assertFalse(list(self.root.glob('.vps-dr-caidan-*')))

    def test_caidan_symlink_is_not_replaced(self):
        command = self.root / 'caidan'
        original = self.root / 'original'
        original.write_text('user command')
        command.symlink_to(original)
        with self.assertRaisesRegex(ValueError, '占用'):
            manage.shortcut(ROOT, command)
        self.assertEqual(original.read_text(), 'user command')

    def test_first_deploy_and_repeat(self):
        state = self.root / 'state.json'
        state.write_text('retained private state')
        manage.upgrade(ROOT, self.target)
        before = {n: (self.target/n).read_bytes() for n in manage.FILES}
        manage.upgrade(ROOT, self.target)
        self.assertEqual(before, {n:(self.target/n).read_bytes() for n in manage.FILES})
        self.assertEqual(state.read_text(), 'retained private state')
        self.assertFalse(list(self.root.glob('.vps-dr.backup-*')))

    def test_installed_recovery_entries_do_not_create_management_bytecode(self):
        self.seed()
        # Run deployed source in fresh interpreters without the launcher's env
        # override. Missing transaction fixtures guarantee no host service calls.
        environment = dict(os.environ)
        environment.pop('PYTHONDONTWRITEBYTECODE', None)
        for name, args in (('backup', ['recover']), ('update', ['recover']), ('observe', [])):
            script = self.target / 'scripts' / (name + '.py')
            code = (
                'import runpy,sys; from pathlib import Path; '
                f'sys.path.insert(0,{str(script.parent)!r}); '
                f'namespace=runpy.run_path({str(script)!r}); '
                "scope=namespace['main'].__globals__; "
                f'scope["BACKUPS"]=Path({str(self.root / 'missing-backups')!r}); '
                f'scope["TRANSACTION"]=Path({str(self.root / 'missing-core-transaction')!r}); '
                f'sys.argv=[{str(script)!r},*{args!r}]; '
                + ("namespace['redact']('public diagnostic probe')" if name == 'observe'
                   else "namespace['main']()")
            )
            result = subprocess.run([sys.executable, '-c', code], env=environment,
                                    capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, f'{name}: {result.stderr}')
            self.assertFalse(list(self.target.rglob('*.pyc')), name)
            manage.verify(ROOT, self.target)

    def test_known_legacy_domain_repair_upgrade(self):
        self.seed()
        old = (ROOT/'config/defaults.env').read_text().replace('DOMAIN=${VPS_DR_DOMAIN:-}', 'DOMAIN=custom.example.com')
        p = self.target / 'config/defaults.env'
        p.write_text(old)
        manage.upgrade(ROOT, self.target)
        self.assertEqual(p.read_bytes(), (ROOT/'config/defaults.env').read_bytes())
        backups = list(self.root.glob('.vps-dr.backup-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0]/'config/defaults.env').read_text(), old)
        manage.rollback(ROOT,backups[0],self.target)
        self.assertEqual(p.read_text(),old)

    def test_normalization_allows_only_domain_assignment(self):
        a = 'before\nDOMAIN=node.example.com\nafter\n'
        b = 'before\nDOMAIN=8.8.8.8\nafter\n'
        self.assertEqual(manage.digest('config/defaults.env',a),manage.digest('config/defaults.env',b))
        self.assertNotEqual(manage.digest('config/defaults.env',a),manage.digest('config/defaults.env',b+'evil\n'))
        self.assertNotEqual(manage.digest('config/defaults.env',a),manage.digest('config/defaults.env',a.replace('node.example.com','$(evil)')))

    def test_unknown_modification_is_not_overwritten(self):
        self.seed()
        path = self.target/'install.sh'
        path.write_text('unknown custom installer')
        with self.assertRaisesRegex(ValueError,'未知修改'):
            manage.upgrade(ROOT,self.target)
        self.assertEqual(path.read_text(),'unknown custom installer')

    def test_failed_swap_rolls_back(self):
        self.seed()
        (self.target/'menu.sh').unlink()  # Recognized partial management deployment.
        replace = os.replace
        def fail_stage(src,dst):
            if Path(src).name.startswith('.vps-dr.stage-'):
                raise OSError('simulated interruption')
            return replace(src,dst)
        with patch.object(manage.os,'replace',side_effect=fail_stage):
            with self.assertRaises(OSError):
                manage.upgrade(ROOT,self.target)
        self.assertTrue((self.target/'install.sh').is_file())
        self.assertFalse((self.target/'menu.sh').exists())

    def test_recover_after_kill_between_renames(self):
        self.seed()
        os.replace(self.target,self.root/'.vps-dr.previous')
        manage.recover(self.target)
        self.assertTrue((self.target/'install.sh').is_file())

    def test_legacy_state_compatibility_does_not_rewrite(self):
        import uuid
        for version in ('0.1.0-dev','1.0.0','1.1.0'):
            p=self.root/f'{version}.json'
            dr.write_state(p,str(uuid.uuid4()),'v26.3.27',version)
            before=p.read_bytes()
            dr.check_state(p,'v26.3.27','1.1.0')
            self.assertEqual(before,p.read_bytes())
        with self.assertRaisesRegex(SystemExit,'CONFIG_CONFLICT'):
            dr.check_state(p,'v99.0.0','1.1.0')

    def test_invalid_server_inputs(self):
        for value in ('','999.1.2.3','127.0.0.1','0.0.0.0','192.168.1.1','https://example.com','example.com:443','a b.example'):
            with self.subTest(value=value), self.assertRaisesRegex(SystemExit,'SERVER_INVALID'):
                dr.validate_server(value)
        self.assertEqual(dr.validate_server('EXAMPLE.COM'),'example.com')

    def test_public_ip_lookup_rejects_non_public_response(self):
        from unittest.mock import MagicMock
        for value in (b'127.0.0.1',b'192.168.1.1',b'garbage',b'8.8.4.4'):
            response=MagicMock()
            response.__enter__.return_value.read.return_value=value
            with patch.object(dr.urllib.request,'urlopen',return_value=response):
                self.assertEqual(dr.public_ip(),'8.8.4.4' if value==b'8.8.4.4' else 'UNKNOWN')

    def test_state_atomic_failure_leaves_no_partial_state(self):
        import uuid
        p=self.root/'state.json'
        with patch.object(dr.json,'dump',side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                dr.write_state(p,str(uuid.uuid4()),'v26.3.27','1.1.0')
        self.assertFalse(p.exists())
        self.assertFalse(list(self.root.glob('.state-*')))
