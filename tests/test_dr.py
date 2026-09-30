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


class DirectXrayTests(unittest.TestCase):
    def setUp(self):
        self.uid = str(uuid.uuid4())
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
        dr.write_state(path, self.uid, "v26.3.27", "0.1.0-dev")
        self.assertEqual(dr.check_state(path, "v26.3.27", "0.1.0-dev")["uuid"], self.uid)
        with self.assertRaisesRegex(SystemExit, "CONFIG_CONFLICT"):
            dr.check_state(path, "v26.3.27", "0.1.0-dev", str(uuid.uuid4()))
        with self.assertRaises(FileExistsError):
            dr.write_state(path, self.uid, "v26.3.27", "0.1.0-dev")

    def test_legacy_panel_state_is_rejected(self):
        path = self.root / "state.json"
        path.write_text(json.dumps({"uuid": self.uid, "xray_version": "v26.3.27",
                                    "deployment_version": "0.1.0-dev"}))
        with self.assertRaisesRegex(SystemExit, "CONFIG_CONFLICT"):
            dr.read_state(path)

    def test_vmess_uri_roundtrip_default_and_temporary_ip(self):
        for address in (dr.DOMAIN, "1.2.3.4"):
            uri = dr.encode_uri(self.uid, address)
            data = dr.decode_uri(uri)
            self.assertEqual(data, dr.uri_payload(self.uid, address))
            self.assertEqual(data["aid"], "0")
            self.assertEqual(data["scy"], "auto")
            self.assertEqual(data["tls"], "none")
        with self.assertRaisesRegex(SystemExit, "--server requires"):
            dr.encode_uri(self.uid, "node.passwallv2ray.top.evil.example")

    def test_export_uri_and_qr_use_same_client_config(self):
        path = self.root / "state.json"
        dr.write_state(path, self.uid, "v26.3.27", "0.1.0-dev")
        with (patch.object(dr.subprocess, "run") as qrencode,
              patch.object(sys, "argv", ["dr.py", "export", str(path), "1.2.3.4", "--qr"]),
              redirect_stdout(StringIO()) as output):
            qrencode.return_value.returncode = 0
            dr.main()
        lines = output.getvalue().splitlines()
        uri = next(line for line in lines if line.startswith("vmess://"))
        self.assertIn("Address: 1.2.3.4", lines)
        self.assertIn("QR_CODE_DISPLAY: PASS", lines)
        self.assertEqual(qrencode.call_args.args[0],
                         ["qrencode", "-t", "ANSIUTF8", "-m", "2", "-o", "-"])
        self.assertEqual(qrencode.call_args.kwargs["input"], uri)
        self.assertEqual(dr.decode_uri(uri), dr.uri_payload(self.uid, "1.2.3.4"))

    def test_qr_failure_keeps_uri_available(self):
        path = self.root / "state.json"
        dr.write_state(path, self.uid, "v26.3.27", "0.1.0-dev")
        with (patch.object(dr.subprocess, "run", side_effect=FileNotFoundError),
              patch.object(sys, "argv", ["dr.py", "export", str(path), "1.2.3.4", "--qr"]),
              redirect_stdout(StringIO()) as output):
            dr.main()
        self.assertIn("vmess://", output.getvalue())
        self.assertIn("QR_CODE_DISPLAY: UNAVAILABLE", output.getvalue())

    def test_systemd_unit_checks_config_before_start(self):
        unit = configparser.ConfigParser(interpolation=None)
        unit.read(Path(__file__).resolve().parents[1] / "config" / "xray.service")
        self.assertEqual(unit["Service"]["Restart"], "on-failure")
        self.assertIn("run -test -config /usr/local/etc/xray/config.json",
                      unit["Service"]["ExecStartPre"])
        self.assertEqual(unit["Service"]["ExecStart"],
                         "/usr/local/bin/xray run -config /usr/local/etc/xray/config.json")


if __name__ == "__main__":
    unittest.main()
