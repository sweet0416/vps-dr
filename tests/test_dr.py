"""Local config, state, and client-link checks; no VPS access."""
import json
import configparser
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr  # noqa: E402
import update


class DirectXrayTests(unittest.TestCase):
    def setUp(self):
        self.uid = str(uuid.uuid4())
        self.version = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_minimal_vmess_config_and_json_roundtrip(self):
        path = self.root / "config.json"
        dr.render_config(path, self.uid)
        data = json.loads(path.read_text())
        inbound = data["inbounds"][0]
        self.assertEqual((inbound["listen"], inbound["port"], inbound["protocol"]),
                         ("0.0.0.0", 443, "vmess"))
        self.assertEqual(inbound["settings"]["clients"], [{"id": self.uid, "alterId": 0}])
        self.assertEqual(inbound["streamSettings"]["security"], "none")
        self.assertEqual(data["outbounds"], [{"protocol": "freedom", "tag": "direct"}])
        self.assertTrue(dr.config_matches(path, self.uid))
        self.assertFalse(dr.config_matches(path, str(uuid.uuid4())))

    def test_state_reuse_and_uuid_conflict(self):
        path = self.root / "state.json"
        dr.write_state(path, self.uid, "v26.3.27", self.version)
        self.assertEqual(dr.check_state(path, "v26.3.27", self.version)["uuid"], self.uid)
        with self.assertRaisesRegex(SystemExit, "CONFIG_CONFLICT"):
            dr.check_state(path, "v26.3.27", self.version, str(uuid.uuid4()))
        with self.assertRaises(FileExistsError):
            dr.write_state(path, self.uid, "v26.3.27", self.version)

    def test_legacy_panel_state_is_rejected(self):
        path = self.root / "state.json"
        path.write_text(json.dumps({"uuid": self.uid, "xray_version": "v26.3.27",
                                    "deployment_version": self.version}))
        with self.assertRaisesRegex(SystemExit, "CONFIG_CONFLICT"):
            dr.read_state(path)

    def test_vmess_uri_roundtrip_default_and_temporary_ip(self):
        for address in ("node.example.com", "1.2.3.4"):
            uri = dr.encode_uri(self.uid, address)
            data = dr.decode_uri(uri)
            self.assertEqual(data, dr.uri_payload(self.uid, address))
            self.assertEqual(data["aid"], "0")
            self.assertEqual(data["scy"], "auto")
            self.assertEqual(data["tls"], "none")
        with self.assertRaisesRegex(SystemExit, "SERVER_INVALID"):
            dr.encode_uri(self.uid, "https://node.example.com/path")

    def test_export_uri_and_qr_use_same_client_config(self):
        path = self.root / "state.json"
        dr.write_state(path, self.uid, "v26.3.27", self.version)
        with (patch.object(dr.subprocess, "run") as qrencode,
              patch.object(sys, "argv", ["dr.py", "export", str(path), "1.2.3.4", "--qr"]),
              redirect_stdout(StringIO()) as output):
            qrencode.return_value.returncode = 0
            dr.main()
        lines = output.getvalue().splitlines()
        uri = next(line for line in lines if line.startswith("vmess://"))
        self.assertTrue("地址: 1.2.3.4" in lines)
        self.assertIn("QR_CODE_DISPLAY: PASS", lines)
        self.assertEqual(qrencode.call_args.args[0],
                         ["qrencode", "-t", "ANSIUTF8", "-m", "2", "-o", "-"])
        self.assertEqual(qrencode.call_args.kwargs["input"], uri)
        self.assertEqual(dr.decode_uri(uri), dr.uri_payload(self.uid, "1.2.3.4"))

    def test_qr_failure_keeps_uri_available(self):
        path = self.root / "state.json"
        dr.write_state(path, self.uid, "v26.3.27", self.version)
        with (patch.object(dr.subprocess, "run", side_effect=FileNotFoundError),
              patch.object(sys, "argv", ["dr.py", "export", str(path), "1.2.3.4", "--qr"]),
              redirect_stdout(StringIO()) as output):
            dr.main()
        self.assertIn("vmess://", output.getvalue())
        self.assertIn("QR_CODE_DISPLAY: UNAVAILABLE", output.getvalue())

    def test_export_defaults_to_detected_ip(self):
        path = self.root / "state.json"
        dr.write_state(path, self.uid, "v26.3.27", self.version)
        with (patch.object(dr, "public_ip", return_value="8.8.4.4"),
              patch.object(sys, "argv", ["dr.py", "export", str(path)]),
              redirect_stdout(StringIO()) as output):
            dr.main()
        uri = next(line for line in output.getvalue().splitlines() if line.startswith("vmess://"))
        self.assertEqual(dr.decode_uri(uri)["add"], "8.8.4.4")

    def test_export_unknown_ip_requires_explicit_address(self):
        path = self.root / "state.json"
        dr.write_state(path, self.uid, "v26.3.27", self.version)
        with (patch.object(dr, "public_ip", return_value="UNKNOWN"),
              patch.object(sys, "argv", ["dr.py", "export", str(path)])):
            with self.assertRaisesRegex(SystemExit, "--server"):
                dr.main()

    def test_health_allows_unresolved_legacy_domain(self):
        from unittest.mock import MagicMock
        from types import SimpleNamespace
        state = self.root / "state.json"
        config = self.root / "config.json"
        binary = self.root / "xray"
        dr.write_state(state, self.uid, "v26.3.27", self.version)
        dr.render_config(config, self.uid)
        binary.touch()

        def command(*args):
            output = "42" if args[1:2] == ("show",) else "LISTEN"
            return SimpleNamespace(returncode=0, stdout=output)

        safe_config = MagicMock()
        safe_config.is_file.return_value = True
        safe_config.is_symlink.return_value = False
        safe_config.stat.return_value = SimpleNamespace(st_uid=0, st_mode=0o600)
        with (patch.object(update, "verify_installed"),
              patch.object(dr, "CONFIG", safe_config), patch.object(dr, "XRAY", binary),
              patch.object(dr, "config_matches", return_value=True),
              patch.object(dr, "xray_version_ok", return_value=True),
              patch.object(dr, "run", side_effect=command),
              patch.object(dr, "listener_pid", return_value=42),
              patch.object(dr.os.path, "realpath", return_value=str(binary)),
              patch.object(dr.socket, "create_connection", return_value=MagicMock()),
              patch.object(dr, "public_ip", return_value="UNKNOWN"),
              patch.object(dr, "dns_ips", return_value=[]),
              redirect_stdout(StringIO()) as output):
            dr.health(state)
        self.assertIn("SYSTEMD: ACTIVE", output.getvalue())
        self.assertIn("NETWORK_NOTICE", output.getvalue())

    def test_systemd_unit_checks_config_before_start(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(Path(__file__).resolve().parents[1] / "config" / "xray.service")
        self.assertEqual(unit["Service"]["Restart"], "on-failure")
        self.assertIn("run -test -config /usr/local/etc/xray/config.json",
                      unit["Service"]["ExecStartPre"])
        self.assertEqual(unit["Service"]["ExecStart"],
                         "/usr/local/bin/xray run -config /usr/local/etc/xray/config.json")

    def test_health_rejects_untrusted_core_before_execution(self):
        state = self.root / 'state.json'
        dr.write_state(state, self.uid, 'v26.3.27', self.version)
        with patch.object(update, 'verify_installed', side_effect=RuntimeError('digest mismatch')), \
                patch.object(dr, 'run') as command:
            with self.assertRaisesRegex(SystemExit, 'HEALTH_CHECK_FAIL'):
                dr.health(state)
            command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
