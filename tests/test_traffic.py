"""Traffic counters and unit deployment are isolated from the live host."""
import contextlib
from datetime import datetime, timezone
import io
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import traffic


class TrafficTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state_dir = self.root / 'state'
        self.units = self.root / 'units'
        self.vendor = self.root / 'vendor'
        self.source = Path(traffic.__file__).resolve().parents[1] / 'config'
        self.routes = self.root / 'routes'
        self.routes6 = self.root / 'routes6'
        self.net = self.root / 'net'
        self.boot = self.root / 'boot'
        self.boot.write_text('boot-one\n')
        self.routes.write_text('Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
                               'eth0 00000000 0100000A 0003 0 0 100 00000000\n')
        self.routes6.write_text('')
        self.current_time = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
        for name, value in dict(STATE_DIR=self.state_dir,
                                STATE=self.state_dir / 'traffic.json',
                                LOCK=self.state_dir / '.traffic.lock',
                                ROUTES=self.routes, ROUTES6=self.routes6,
                                NET=self.net, BOOT=self.boot,
                                UNIT_DIR=self.units, OTHER_UNIT_DIRS=(self.vendor,),
                                SOURCE=self.source).items():
            guard = patch.object(traffic, name, value)
            guard.start()
            self.addCleanup(guard.stop)
        self.clock = patch.object(traffic, 'now', side_effect=lambda: self.current_time)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.systemctl = patch.object(traffic, 'systemctl')
        self.ctl = self.systemctl.start()
        self.addCleanup(self.systemctl.stop)
        self.out = io.StringIO()
        guard = contextlib.redirect_stdout(self.out)
        guard.__enter__()
        self.addCleanup(guard.__exit__, None, None, None)
        self.counter('eth0', 1000, 2000)

    def counter(self, iface, received, sent):
        stats = self.net / iface / 'statistics'
        stats.mkdir(parents=True, exist_ok=True)
        (stats / 'rx_bytes').write_text(str(received))
        (stats / 'tx_bytes').write_text(str(sent))

    def select(self, iface):
        self.routes.write_text('Iface Destination Gateway Flags RefCnt Use Metric Mask\n'
                               f'{iface} 00000000 0100000A 0003 0 0 100 00000000\n')

    def state(self):
        return json.loads(traffic.STATE.read_text())

    def test_baseline_then_deltas_and_repeat_are_private(self):
        first = traffic.sample()
        self.assertEqual(first['total'], [0, 0])
        self.counter('eth0', 1200, 2300)
        second = traffic.sample()
        self.assertEqual(second['total'], [200, 300])
        self.assertEqual(second['monthly']['2026-11'], [200, 300])
        self.assertEqual(traffic.sample()['total'], [200, 300])
        self.assertEqual(traffic.STATE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(traffic.STATE.stat().st_uid, 0)
        self.assertEqual(traffic.LOCK.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.out.getvalue(), '')
        self.ctl.assert_not_called()

    def test_boot_change_counts_new_boot_without_underflow(self):
        traffic.sample()
        self.counter('eth0', 1100, 2150)
        traffic.sample()
        self.boot.write_text('boot-two\n')
        self.counter('eth0', 15, 20)
        state = traffic.sample()
        self.assertEqual(state['total'], [115, 170])
        self.assertEqual(state['gaps'], 1)
        self.assertEqual(traffic.sample()['gaps'], 1)

    def test_counter_reset_and_interface_change_preserve_totals(self):
        traffic.sample()
        self.counter('eth0', 10, 2100)
        state = traffic.sample()
        self.assertEqual(state['total'], [10, 100])
        self.assertEqual(state['gaps'], 1)
        self.select('eth1')
        self.counter('eth1', 1000000, 2000000)
        state = traffic.sample()
        self.assertEqual(state['total'], [10, 100])
        self.assertEqual(state['gaps'], 2)
        self.counter('eth1', 1000050, 2000060)
        self.assertEqual(traffic.sample()['total'], [60, 160])

    def test_month_rollover_retains_previous_month(self):
        traffic.sample()
        self.counter('eth0', 1050, 2100)
        traffic.sample()
        self.current_time = datetime(2026, 12, 1, 0, 0, tzinfo=timezone.utc)
        self.counter('eth0', 1070, 2140)
        state = traffic.sample()
        self.assertEqual(state['monthly'], {'2026-11': [50, 100], '2026-12': [20, 40]})
        self.assertEqual(state['total'], [70, 140])
        self.assertEqual(state['started_at'], '2026-11-21T12:00:00+00:00')

    def test_show_explains_scope_and_sampling_limits(self):
        traffic.show()
        output = self.out.getvalue()
        for expected in ('整机默认出口网卡', 'UTC', 'eth0', '首次采集时间', '本月', '累计', '服务商', '跨月', '重启'):
            self.assertIn(expected, output)

    def test_route_discovery_deduplicates_ipv4_ipv6(self):
        self.routes6.write_text('0' * 32 + ' 00 ' + '0' * 32 + ' 00 ' + '0' * 32 + ' 00000064 00000000 00000000 00000001 eth0\n')
        self.assertEqual(traffic.interfaces(), ['eth0'])
        self.select('lo')
        self.routes6.write_text('')
        with self.assertRaisesRegex(ValueError, 'TRAFFIC_INTERFACE_UNAVAILABLE'):
            traffic.sample()
        self.assertFalse(traffic.STATE.exists())

    def test_lower_metric_default_route_is_preferred(self):
        with self.routes.open('a') as stream:
            stream.write('backup0 00000000 0100000A 0003 0 0 200 00000000\n')
            stream.write('primary0 00000000 0100000A 0003 0 0 50 00000000\n')
        self.assertEqual(traffic.interfaces(), ['primary0'])

    def test_negative_counter_and_missing_boot_do_not_modify_state(self):
        traffic.sample()
        original = traffic.STATE.read_bytes()
        self.counter('eth0', -1, 2000)
        with self.assertRaisesRegex(ValueError, 'TRAFFIC_COUNTER_INVALID'):
            traffic.sample()
        self.counter('eth0', 1100, 2100)
        self.boot.write_text('')
        with self.assertRaisesRegex(ValueError, 'TRAFFIC_BOOT_UNAVAILABLE'):
            traffic.sample()
        self.assertEqual(traffic.STATE.read_bytes(), original)

    def test_state_corruption_permissions_and_symlink_are_rejected(self):
        traffic.sample()
        original = traffic.STATE.read_bytes()
        for malformed in (b'[]', b'null', b'{"schema":2}', b'not json'):
            traffic.STATE.write_bytes(malformed)
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                traffic.sample()
            self.assertEqual(traffic.STATE.read_bytes(), malformed)
        traffic.STATE.write_bytes(original)
        traffic.STATE.chmod(0o644)
        with self.assertRaisesRegex(ValueError, 'TRAFFIC_STATE_UNSAFE'):
            traffic.sample()
        traffic.STATE.unlink()
        target = self.root / 'retained'
        target.write_bytes(original)
        traffic.STATE.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'TRAFFIC_STATE_UNSAFE'):
            traffic.sample()
        self.assertEqual(target.read_bytes(), original)

    def test_atomic_failure_retains_previous_sample_and_retry_is_exact(self):
        traffic.sample()
        original = traffic.STATE.read_bytes()
        self.counter('eth0', 1120, 2180)
        with patch.object(traffic.os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(OSError, 'disk full'):
                traffic.sample()
        self.assertEqual(traffic.STATE.read_bytes(), original)
        self.assertFalse(list(self.state_dir.glob('.vps-dr-traffic-*')))
        self.assertEqual(traffic.sample()['total'], [120, 180])

    def test_concurrent_samples_share_lock_and_do_not_double_count(self):
        traffic.sample()
        self.counter('eth0', 1120, 2180)
        context = multiprocessing.get_context('fork')
        children = [context.Process(target=traffic.sample) for _ in range(2)]
        try:
            for child in children:
                child.start()
            for child in children:
                child.join(timeout=5)
                self.assertEqual(child.exitcode, 0)
        finally:
            for child in children:
                if child.is_alive():
                    child.terminate()
                    child.join()
        self.assertEqual(self.state()['total'], [120, 180])

    def test_setup_is_idempotent_and_enables_only_known_timer(self):
        traffic.setup()
        self.assertEqual(self.ctl.call_args_list[-2].args, ('daemon-reload',))
        self.assertEqual(self.ctl.call_args_list[-1].args, ('enable', '--now', 'vps-dr-traffic.timer'))
        first_mtime = (self.units / traffic.UNITS[0]).stat().st_mtime_ns
        traffic.setup()
        self.assertEqual((self.units / traffic.UNITS[0]).stat().st_mtime_ns, first_mtime)
        self.assertTrue(traffic.STATE.exists())
        for name in traffic.UNITS:
            self.assertEqual((self.units / name).read_bytes(), (self.source / name).read_bytes())

    def test_unknown_unit_or_dropin_is_retained_before_any_write(self):
        for location, suffix in ((self.units, ''), (self.vendor, ''), (self.vendor, '.d')):
            with self.subTest(location=location, suffix=suffix):
                location.mkdir(exist_ok=True)
                path = location / (traffic.UNITS[-1] + suffix)
                if suffix:
                    path.mkdir()
                    unknown = path / 'override.conf'
                else:
                    unknown = path
                unknown.write_text('retained')
                with self.assertRaisesRegex(ValueError, 'TRAFFIC_UNIT_CONFLICT'):
                    traffic.setup()
                self.assertEqual(unknown.read_text(), 'retained')
                self.assertFalse((self.units / traffic.UNITS[0]).exists())
                self.ctl.assert_not_called()
                unknown.unlink()
                if suffix:
                    path.rmdir()

    def test_systemd_failure_is_retryable_and_not_reported_ready(self):
        self.ctl.side_effect = subprocess.CalledProcessError(1, 'systemctl')
        with self.assertRaises(subprocess.SubprocessError):
            traffic.setup()
        self.assertNotIn('TRAFFIC_READY', self.out.getvalue())
        self.ctl.side_effect = None
        traffic.setup()
        self.assertIn('TRAFFIC_READY', self.out.getvalue())

    def test_nonroot_refusal_has_no_side_effects(self):
        with patch.object(os, 'geteuid', return_value=1000):
            for action in (traffic.sample, traffic.setup, traffic.show):
                with self.subTest(action=action), self.assertRaisesRegex(ValueError, 'ROOT_REQUIRED'):
                    action()
        self.assertFalse(self.state_dir.exists())
        self.ctl.assert_not_called()


if __name__ == '__main__':
    unittest.main()
