"""Pure local safety checks; no VPS or panel access."""
import base64
import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import dr  # noqa: E402


class RecoverySafetyTests(unittest.TestCase):
    def setUp(self):
        self.state_path = Path("/mock/state.json")
        self.uid = str(uuid.uuid4())

    def test_matching_inbound_reused_without_mutation(self):
        with patch.object(dr, "inbounds", return_value=[dr.profile(self.uid)]), \
             patch.object(dr, "state", return_value={"uuid": self.uid}), \
             patch.object(dr, "panel_request") as request:
            dr.configure(self.state_path)
            request.assert_not_called()

    def test_different_uuid_stops_without_mutation(self):
        with patch.object(dr, "inbounds", return_value=[dr.profile(str(uuid.uuid4()))]), \
             patch.object(dr, "state", return_value={"uuid": self.uid}), \
             patch.object(dr, "panel_request") as request:
            with self.assertRaisesRegex(SystemExit, "CONFIG_CONFLICT"):
                dr.configure(self.state_path)
            request.assert_not_called()

    def test_official_link_without_aid_is_validated_but_not_rewritten(self):
        data = {"v": "2", "add": dr.DOMAIN, "port": 443, "id": self.uid,
                "scy": "auto", "net": "tcp", "type": "none", "tls": "none"}
        uri = "vmess://" + base64.b64encode(json.dumps(data).encode()).decode()
        self.assertEqual(dr.validate_official_uri(uri, self.uid), uri)
        data["tls"] = "tls"
        wrong = "vmess://" + base64.b64encode(json.dumps(data).encode()).decode()
        with self.assertRaisesRegex(SystemExit, "CLIENT_EXPORT_CONFLICT"):
            dr.validate_official_uri(wrong, self.uid)


if __name__ == "__main__":
    unittest.main()
