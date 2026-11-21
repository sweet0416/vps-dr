"""Read-only diagnostics regressions. All service/network calls are mocked."""
import contextlib
from io import StringIO
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import observe


def result(stdout='', code=0):
    return SimpleNamespace(stdout=stdout, returncode=code)


class ObservationTests(unittest.TestCase):
    def capture(self, function):
        with contextlib.redirect_stdout(StringIO()) as out:
            status = function()
        return status, out.getvalue()

    def test_public_ip_failure_does_not_mark_healthy_local_service_bad(self):
        def command(*args, **kwargs):
            if args[0] == 'ss':
                return result('LISTEN 0 1 0.0.0.0:443 0.0.0.0:* users:(("xray",pid=42,fd=3))')
            if args[0] == 'ip':
                return result('2: eth0 inet 10.0.0.3/24 brd 10.0.0.255 scope global eth0')
            return result()

        with (patch.object(observe, 'command', side_effect=command),
              patch.object(observe, 'config_status', return_value=(True, '一致')),
              patch.object(observe, 'unit_status', return_value=True),
              patch.object(observe, 'main_pid', return_value=42),
              patch.object(observe, 'managed_pid', return_value=True),
              patch.object(observe, 'firewall'),
              patch.object(observe.dr, 'public_ip', return_value='UNKNOWN'),
              patch.object(observe.socket, 'create_connection', return_value=MagicMock())):
            status, text = self.capture(observe.diagnostics)
        self.assertEqual(status, 0)
        self.assertIn('LOCAL_SERVICE: PASS', text)
        self.assertIn('获取失败，不影响', text)
        self.assertIn('10.0.0.3/24', text)
        self.assertIn('未进行 VMess 客户端握手', text)

    def test_foreign_443_listener_is_not_reported_healthy(self):
        def command(*args, **kwargs):
            if args[0] == 'ss':
                return result('LISTEN 0 1 0.0.0.0:443 0.0.0.0:* users:(("other",pid=75,fd=3))')
            return result()

        with (patch.object(observe, 'command', side_effect=command),
              patch.object(observe, 'config_status', return_value=(True, '一致')),
              patch.object(observe, 'unit_status', return_value=True),
              patch.object(observe, 'main_pid', return_value=42),
              patch.object(observe, 'managed_pid', return_value=True),
              patch.object(observe, 'firewall'),
              patch.object(observe.dr, 'public_ip', return_value='1.2.3.4'),
              patch.object(observe.socket, 'create_connection') as connection):
            status, text = self.capture(observe.diagnostics)
        self.assertEqual(status, 1)
        self.assertIn('LOCAL_SERVICE: FAIL', text)
        connection.assert_not_called()

    def test_config_errors_do_not_print_uuid_or_raw_error(self):
        secret = str(uuid.uuid4())
        with patch.object(observe.dr, 'read_state', side_effect=SystemExit(secret)):
            ok, text = observe.config_status()
        self.assertFalse(ok)
        self.assertNotIn(secret, text)

    def test_connections_counts_tcp_streams_and_explains_scope(self):
        with (patch.object(observe, 'command', return_value=result('0 0 local remote\n0 0 local remote\n')) as command,
              patch.object(observe, 'main_pid', return_value=42),
              patch.object(observe, 'resources')):
            status, text = self.capture(observe.connections)
        self.assertEqual(status, 0)
        self.assertIn('已建立连接：2', text)
        self.assertIn('不等于独立用户人数', text)
        command.assert_called_once_with('ss', '-Htn', 'state', 'established', '( sport = :443 )')

    def test_connection_failure_returns_error_without_claiming_zero_users(self):
        with (patch.object(observe, 'command', return_value=result(code=127)),
              patch.object(observe, 'main_pid', return_value=None),
              patch.object(observe, 'resources')):
            status, text = self.capture(observe.connections)
        self.assertEqual(status, 1)
        self.assertIn('CONNECTIONS_UNAVAILABLE', text)
        self.assertNotIn('已建立连接：0', text)

    def test_logs_redact_secrets_strip_terminal_controls_and_bound_output(self):
        secret = str(uuid.uuid4())
        raw = ('old-line\n' * 60 + 'id=' + secret + ' vmess://abcdef= token=private-value\n'
               + '\x1b[31mred\x1b[0m\x1b]8;;https://example.com\x07text\x1b]8;;\x07\n'
               + 'Authorization: Bearer hidden-authorization\n' + 'x' * 5000)
        with patch.object(observe, 'command', return_value=result(raw)) as command:
            status, text = self.capture(observe.logs)
        self.assertEqual(status, 0)
        for hidden in (secret, 'vmess://abcdef=', 'private-value', 'hidden-authorization', '\x1b'):
            self.assertNotIn(hidden, text)
        self.assertIn('[UUID已隐藏]', text)
        self.assertIn('[节点链接已隐藏]', text)
        self.assertIn('本行已截断', text)
        self.assertLess(text.count('old-line'), 50)
        self.assertLess(len(text), 5000)
        self.assertIn('--lines=50', command.call_args.args)

    def test_missing_journal_is_a_clear_error(self):
        with patch.object(observe, 'command', return_value=result(code=127)):
            status, text = self.capture(observe.logs)
        self.assertEqual(status, 1)
        self.assertIn('LOGS_UNAVAILABLE', text)

    def test_command_is_bounded_and_never_uses_shell(self):
        with patch.object(observe.subprocess, 'run', side_effect=subprocess.TimeoutExpired('ss', 6)) as run:
            response = observe.command('ss', 'literal;value')
        self.assertEqual(response.returncode, 127)
        self.assertEqual(run.call_args.args[0], ('ss', 'literal;value'))
        self.assertEqual(run.call_args.kwargs['timeout'], 6)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_resources_use_proc_and_handle_process_name_parentheses(self):
        with tempfile.TemporaryDirectory() as temporary:
            proc = Path(temporary)
            (proc / 'meminfo').write_text('MemTotal: 2048 kB\nMemAvailable: 1024 kB\n')
            (proc / 'loadavg').write_text('0.01 0.02 0.03 1/20 42')
            (proc / '42').mkdir()
            (proc / '42/status').write_text('Name: xray\nVmRSS: 256 kB\n')
            fields = ['S'] + ['0'] * 10 + ['100', '200'] + ['0'] * 20
            (proc / '42/stat').write_text('42 (process ) name) ' + ' '.join(fields))
            with (patch.object(observe, 'PROC', proc),
                  patch.object(observe, 'managed_pid', return_value=True),
                  patch.object(observe.os, 'sysconf', return_value=100)):
                _, text = self.capture(lambda: observe.resources(42))
        self.assertIn('已用 1.0 MiB / 2.0 MiB', text)
        self.assertIn('Xray 内存：256.0 KiB', text)
        self.assertIn('累计 CPU 时间：3.0 秒', text)

    def test_firewall_inspection_does_not_claim_cloud_security_group_open(self):
        with patch.object(observe, 'command', return_value=result('Status: active\n443/tcp ALLOW Anywhere\n')):
            _, text = self.capture(observe.firewall)
        self.assertIn('UFW：已启用', text)
        self.assertIn('云安全组', text)
        self.assertIn('仍需外部验证', text)


if __name__ == '__main__':
    unittest.main()
