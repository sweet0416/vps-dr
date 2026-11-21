"""Real installer/preflight shell flows in a temporary root with fake system tools.

Run as Linux root (CI or ``unshare -Ur``). No host service paths or network are used.
"""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == 'posix' and hasattr(os, 'geteuid') and os.geteuid() == 0
                     and shutil.which('bash') and platform.machine() in ('x86_64', 'aarch64'),
                     'requires Linux root and Bash; use isolated CI or unshare -Ur')
class InstallFlowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='vps-dr-install-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source'
        shutil.copytree(ROOT, self.source, ignore=shutil.ignore_patterns('.git', '__pycache__'))
        paths = ('/etc/vps-dr', '/usr/local/bin', '/usr/local/etc/xray',
                 '/etc/systemd/system', '/run/systemd/system',
                 '/usr/local/lib/systemd/system', '/usr/lib/systemd/system',
                 '/opt/vps-dr', '/run/lock', '/var/lib/vps-dr', '/proc', '/etc/os-release')
        self.paths = {value: str(self.root / value.lstrip('/')) for value in paths}
        # Rewrite only the disposable project copy. All real scripts retain root checks.
        for path in self.source.rglob('*'):
            if path.is_file() and (path.suffix in ('.py', '.sh', '.env', '.service', '.timer')
                                   or path.name == 'VERSION'):
                content = path.read_text()
                for old, new in self.paths.items():
                    content = content.replace(old, new)
                path.write_text(content)
        self.state_dir = self.root / 'etc/vps-dr'
        self.state_dir.mkdir(parents=True, mode=0o700)
        self.state = self.state_dir / 'state.json'
        self.binary = self.root / 'usr/local/bin/xray'
        self.binary.parent.mkdir(parents=True)
        self.config = self.root / 'usr/local/etc/xray/config.json'
        self.config.parent.mkdir(parents=True, mode=0o700)
        self.unit = self.root / 'etc/systemd/system/xray.service'
        self.unit.parent.mkdir(parents=True)
        (self.root / 'run/systemd/system').mkdir(parents=True)
        (self.root / 'run/lock').mkdir(parents=True)
        (self.root / 'opt').mkdir()
        (self.root / 'tmp').mkdir()
        (self.root / 'proc').mkdir()
        (self.root / 'proc/meminfo').write_text('MemTotal: 2097152 kB\nMemAvailable: 1048576 kB\n')
        (self.root / 'etc/os-release').write_text('ID=ubuntu\nVERSION_ID=26.04\n')
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.log = self.root / 'actions.log'
        self.active = self.root / 'active'
        self.deployed = self.root / 'opt/vps-dr'
        self.base_version = 'v26.3.27'
        self.fixture = self.root / 'fixture-xray'
        self.fixture.write_bytes(self.core_bytes(self.base_version))
        self.fixture.chmod(0o755)
        self.archive = self.root / 'fixture.zip'
        with zipfile.ZipFile(self.archive, 'w') as archive:
            archive.writestr('xray', self.fixture.read_bytes())
        archive_hash = self.sha(self.archive)
        self.digest = self.root / 'fixture.zip.dgst'
        self.digest.write_text('SHA2-256= ' + archive_hash + '\n')
        defaults = self.source / 'config/defaults.env'
        text = defaults.read_text()
        for suffix in ('AMD64', 'ARM64'):
            text = re.sub(r'^XRAY_SHA256_' + suffix + r'=.*$',
                          'XRAY_SHA256_' + suffix + '=' + archive_hash, text, flags=re.M)
            text = re.sub(r'^XRAY_BINARY_SHA256_' + suffix + r'=.*$',
                          'XRAY_BINARY_SHA256_' + suffix + '=' + self.sha(self.fixture), text, flags=re.M)
        defaults.write_text(text)
        self.env = dict(os.environ, PATH=str(self.bin) + ':/usr/bin:/bin',
                        TMPDIR=str(self.root / 'tmp'), TEST_ROOT=str(self.root),
                        ACTION_LOG=str(self.log), TEST_ACTIVE=str(self.active),
                        TEST_BINARY=str(self.binary), TEST_UNIT=str(self.unit),
                        TEST_ARCHIVE=str(self.archive), TEST_DIGEST=str(self.digest),
                        TEST_REAL_PYTHON=sys.executable, PYTHONDONTWRITEBYTECODE='1')
        for key in ('VPS_DR_LOCKED', 'VMESS_UUID', 'VPS_DR_SERVER', 'VPS_DR_DOMAIN',
                    'FAIL_INSTALL_CONFIG', 'TRAFFIC_STATUS'):
            self.env.pop(key, None)
        self.make_tools()

    @staticmethod
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    @staticmethod
    def core_bytes(version):
        return ("#!/usr/bin/env bash\n"
                "if [[ ${1:-} == version ]]; then echo 'Xray " + version[1:] + " (test fixture)'; exit 0; fi\n"
                "if [[ ${1:-} == run && ${2:-} == -test && ${3:-} == -config ]]; then\n"
                "  exec \"$TEST_REAL_PYTHON\" -c 'import json,sys; json.load(open(sys.argv[1]))' \"$4\"\n"
                "fi\nexit 1\n").encode()

    def tool(self, name, content):
        path = self.bin / name
        path.write_text('#!/usr/bin/env bash\nset -eu\n' + content)
        path.chmod(0o755)

    def make_tools(self):
        self.tool('curl', '''
echo "curl $*" >> "$ACTION_LOG"
if [[ "$*" == *-fsSIL* ]]; then exit 0; fi
destination= url=
while (( $# )); do
  case "$1" in
    -o) destination=$2; shift 2 ;;
    https://*) url=$1; shift ;;
    *) shift ;;
  esac
done
[[ -n "$destination" && "$destination" == "$TEST_ROOT/"* ]] || exit 90
if [[ "$url" == *.dgst ]]; then cp "$TEST_DIGEST" "$destination"; else cp "$TEST_ARCHIVE" "$destination"; fi
''')
        self.tool('systemctl', '''
echo "systemctl $*" >> "$ACTION_LOG"
case "${1:-}" in
  is-active) [[ -e "$TEST_ACTIVE" ]] ;;
  is-enabled) [[ -e "$TEST_ROOT/enabled" ]] ;;
  enable) touch "$TEST_ROOT/enabled"; [[ "$*" != *--now* ]] || touch "$TEST_ACTIVE" ;;
  stop) rm -f "$TEST_ACTIVE" ;;
  start|restart) touch "$TEST_ACTIVE" ;;
  daemon-reload) exit 0 ;;
  cat) [[ -f "$TEST_UNIT" ]] && cat "$TEST_UNIT" ;;
  show)
    case "$*" in
      *MainPID*) if [[ -e "$TEST_ACTIVE" ]]; then echo 42; else echo 0; fi ;;
      *FragmentPath*) [[ ! -f "$TEST_UNIT" ]] || echo "$TEST_UNIT" ;;
      *DropInPaths*) : ;;
      *) exit 1 ;;
    esac ;;
  *) exit 1 ;;
esac
''')
        self.tool('ss', '''
if [[ -e "$TEST_ACTIVE" ]]; then
  echo 'LISTEN 0 512 0.0.0.0:443 0.0.0.0:* users:(("xray",pid=42,fd=3))'
fi
''')
        self.tool('readlink', '''
if [[ "${2:-}" == "$TEST_ROOT/proc/42/exe" ]]; then echo "$TEST_BINARY"; else exec /usr/bin/readlink "$@"; fi
''')
        self.tool('getent', 'echo "140.82.114.3 STREAM github.com"\n')
        self.tool('df', 'printf "Filesystem 1024-blocks Used Available Capacity Mounted on\\nfixture 9999999 1 9999998 1%% /\\n"\n')
        self.tool('ufw', 'echo "Status: inactive"\n')
        self.tool('install', '''
for value in "$@"; do
  if [[ "$value" == /* && "$value" != "$TEST_ROOT/"* ]]; then exit 90; fi
done
if [[ "${FAIL_INSTALL_CONFIG:-0}" == 1 && "${!#}" == "$TEST_ROOT/usr/local/etc/xray/config.json" ]]; then
  echo 'simulated config install failure' >&2
  exit 42
fi
exec /usr/bin/install "$@"
''')
        # Only health/socket and timer operations are replaced; state, UUID,
        # profile, locks, binary verification and management deploy run for real.
        wrapper = self.bin / 'python3'
        wrapper.write_text('#!' + sys.executable + '\n' + '''
import os, pathlib, sys
args = sys.argv[1:]
if args and pathlib.Path(args[0]).name == 'traffic.py' and args[1:] == ['setup']:
    with open(os.environ['ACTION_LOG'], 'a') as output: output.write('traffic setup\\n')
    sys.exit(int(os.environ.get('TRAFFIC_STATUS', '0')))
if args and pathlib.Path(args[0]).name == 'dr.py' and len(args) > 1 and args[1] == 'health':
    sys.path.insert(0, str(pathlib.Path(args[0]).parent))
    import dr
    state = dr.read_state(args[2])
    assert dr.config_matches(dr.CONFIG, state['uuid'])
    assert pathlib.Path(os.environ['TEST_ACTIVE']).is_file()
    with open(os.environ['ACTION_LOG'], 'a') as output: output.write('health validated\\n')
    print('LOCAL_SERVICE: PASS (isolated fixture)')
    sys.exit(0)
os.execv(os.environ['TEST_REAL_PYTHON'], [os.environ['TEST_REAL_PYTHON'], *args])
''')
        wrapper.chmod(0o755)

    def run_install(self, *args):
        return subprocess.run(['bash', str(self.source / 'install.sh'), *args],
                              input='', text=True, capture_output=True, env=self.env, timeout=30)

    def actions(self):
        return self.log.read_text() if self.log.exists() else ''

    def assert_success(self, response):
        self.assertEqual(response.returncode, 0, response.stdout + response.stderr)
        self.assertIn('READY_TO_INSTALL: YES', response.stdout)

    def assert_profile_consistent(self):
        data = json.loads(self.state.read_text())
        profile = json.loads(self.config.read_text())
        self.assertEqual(profile['inbounds'][0]['settings']['clients'][0]['id'], data['uuid'])
        self.assertEqual((self.state.stat().st_mode & 0o777, self.config.stat().st_mode & 0o777),
                         (0o600, 0o600))
        return data

    def downloads(self):
        return [line for line in self.actions().splitlines()
                if line.startswith('curl ') and ' -o ' in line]

    def test_first_install_and_repeat_preserve_uuid_without_re_download(self):
        first = self.run_install()
        self.assert_success(first)
        before = self.assert_profile_consistent()
        self.assertTrue(self.active.is_file())
        self.assertEqual(len(self.downloads()), 2)
        self.assertTrue((self.deployed / 'scripts/update.py').is_file())
        self.assertTrue((self.binary.parent / 'caidan').is_file())
        self.assertIn('CLIENT_EXPORT_SKIPPED', first.stdout)
        self.assertNotIn(before['uuid'], first.stdout + first.stderr + self.actions())
        second = self.run_install()
        self.assert_success(second)
        self.assertIn('ALREADY_CONFIGURED', second.stdout)
        self.assertEqual(self.assert_profile_consistent(), before)
        self.assertEqual(len(self.downloads()), 2)

    def test_resume_after_binary_written_before_config_and_unit(self):
        self.env['FAIL_INSTALL_CONFIG'] = '1'
        interrupted = self.run_install()
        self.assertEqual(interrupted.returncode, 42, interrupted.stdout + interrupted.stderr)
        self.assertTrue(self.state.is_file())
        uid = json.loads(self.state.read_text())['uuid']
        self.assertTrue(self.binary.is_file())
        self.assertFalse(self.config.exists())
        self.assertFalse(self.unit.exists())
        self.assertFalse(self.active.exists())
        self.assertEqual(len(self.downloads()), 2)
        self.env.pop('FAIL_INSTALL_CONFIG')
        resumed = self.run_install()
        self.assert_success(resumed)
        self.assertEqual(self.assert_profile_consistent()['uuid'], uid)
        self.assertTrue(self.unit.is_file())
        self.assertTrue(self.active.is_file())
        self.assertEqual(len(self.downloads()), 2)

    def test_upgraded_official_core_is_reused_without_download_or_downgrade(self):
        self.assert_success(self.run_install())
        old_state = self.assert_profile_consistent()
        new_version = 'v26.11.20'
        self.binary.write_bytes(self.core_bytes(new_version))
        self.binary.chmod(0o755)
        data = dict(old_state, xray_version=new_version)
        self.state.write_text(json.dumps(data))
        trust = {'schema': 1, 'version': new_version, 'machine': platform.machine(),
                 'archive_sha256': 'a' * 64, 'binary_sha256': self.sha(self.binary)}
        (self.state_dir / 'core.json').write_text(json.dumps(trust))
        (self.state_dir / 'core.json').chmod(0o600)
        before = self.binary.read_bytes()
        count = len(self.downloads())
        response = self.run_install()
        self.assert_success(response)
        self.assertEqual(self.assert_profile_consistent(), data)
        self.assertEqual(self.binary.read_bytes(), before)
        self.assertEqual(len(self.downloads()), count)

    def test_management_upgrade_accepts_legacy_1_1_state_without_restarting_node(self):
        self.assert_success(self.run_install())
        data = self.assert_profile_consistent()
        data['deployment_version'] = '1.1.0'
        self.state.write_text(json.dumps(data))
        (self.deployed / 'VERSION').write_text('1.1.0\n')
        before = {p: p.read_bytes() for p in (self.state, self.config, self.binary, self.unit)}
        self.log.write_text('')
        response = self.run_install('--upgrade-management')
        self.assert_success(response)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.assertEqual((self.deployed / 'VERSION').read_bytes(), (self.source / 'VERSION').read_bytes())
        self.assertTrue(self.active.is_file())
        self.assertFalse(self.downloads())
        self.assertNotIn('systemctl restart', self.actions())
        self.assertNotIn('systemctl stop', self.actions())

    def test_traffic_setup_failure_does_not_fail_healthy_install_or_upgrade(self):
        self.env['TRAFFIC_STATUS'] = '1'
        response = self.run_install()
        self.assert_success(response)
        data = self.assert_profile_consistent()
        self.assertIn('TRAFFIC_SETUP_FAILED', response.stderr)
        self.assertIn('LOCAL_SERVICE: PASS', response.stdout)
        self.assertTrue(self.active.exists())
        before = (self.state.read_bytes(), self.config.read_bytes())
        upgraded = self.run_install('--upgrade-management')
        self.assert_success(upgraded)
        self.assertIn('TRAFFIC_SETUP_FAILED', upgraded.stderr)
        self.assertEqual((self.state.read_bytes(), self.config.read_bytes()), before)
        self.assertEqual(self.assert_profile_consistent()['uuid'], data['uuid'])


if __name__ == '__main__':
    unittest.main()
