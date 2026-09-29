#!/usr/bin/env python3
"""3x-ui v3.8.5 API and local verification for the private DR node."""
import base64
import json
import os
import platform
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

DOMAIN = "node.passwallv2ray.top"
XRAY = Path("/usr/local/x-ui/bin/xray-linux-amd64" if platform.machine() == "x86_64" else "/usr/local/x-ui/bin/xray-linux-arm64")
XRAY_CONFIG = Path("/usr/local/x-ui/bin/config.json")


def die(message):
    raise SystemExit(message)


def valid_uuid(value):
    try:
        parsed = uuid.UUID(value)
        return str(parsed) == value.lower()
    except (ValueError, AttributeError):
        return False


def state(path):
    p = Path(path)
    if not p.exists() or p.stat().st_mode & 0o077:
        die("CONFIG_CONFLICT: state missing or readable by non-root users")
    try:
        data = json.loads(p.read_text())
        if not valid_uuid(data["uuid"]) or not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", data["version"]):
            raise ValueError("invalid state fields")
        return data
    except (OSError, ValueError, KeyError, TypeError):
        die("CONFIG_CONFLICT: invalid state file")


def write_state(path, value):
    p = Path(path)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(value, f)
        f.write("\n")


def profile(uid):
    return {
        "enable": True, "remark": "DR-VMESS", "listen": "0.0.0.0",
        "port": 443, "protocol": "vmess", "expiryTime": 0, "total": 0,
        "settings": {"clients": [{"id": uid, "alterId": 0, "security": "auto", "email": "dr-vmess@local", "enable": True}]},
        "streamSettings": {"network": "tcp", "security": "none", "tcpSettings": {"header": {"type": "none"}}},
        "sniffing": {"enabled": False},
    }


def object_value(value):
    return json.loads(value) if isinstance(value, str) else value


def matches(inbound, uid):
    try:
        settings = object_value(inbound["settings"])
        stream = object_value(inbound["streamSettings"])
        clients = settings["clients"]
        return (
            inbound["protocol"] == "vmess" and inbound["port"] == 443
            and inbound.get("listen", "") in ("", "0.0.0.0")
            and inbound.get("enable") is True and inbound.get("remark") == "DR-VMESS"
            and inbound.get("total", 0) == 0 and inbound.get("expiryTime", 0) == 0
            and stream["network"] == "tcp" and stream.get("security", "none") == "none"
            and stream.get("tcpSettings", {}).get("header", {}).get("type", "none") == "none"
            and len(clients) == 1 and clients[0]["id"].lower() == uid.lower()
            and clients[0].get("alterId", 0) == 0 and clients[0].get("security", "auto") == "auto"
            and clients[0].get("enable", True) is True
        )
    except (KeyError, TypeError, ValueError):
        return False


def active_match(inbound, uid):
    try:
        stream = inbound["streamSettings"]
        clients = inbound["settings"]["clients"]
        return (inbound["protocol"] == "vmess" and inbound["port"] == 443
                and inbound.get("listen", "") in ("", "0.0.0.0")
                and stream["network"] == "tcp" and stream.get("security", "none") == "none"
                and stream.get("tcpSettings", {}).get("header", {}).get("type", "none") == "none"
                and len(clients) == 1 and clients[0]["id"].lower() == uid.lower()
                and clients[0].get("alterId", 0) == 0)
    except (KeyError, TypeError, ValueError):
        return False


def panel_request(method, route, payload=None):
    port = os.environ["XUI_PANEL_PORT"]
    base = os.environ["XUI_WEB_BASE_PATH"].strip("/")
    token = os.environ["XUI_API_TOKEN"]
    url = f"http://127.0.0.1:{port}/{base}/{route.lstrip('/')}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    for attempt in range(10):
        try:
            with urllib.request.urlopen(req, timeout=5) as response:
                result = json.load(response)
            if not result.get("success"):
                die(f"3x-ui API rejected {route}: {result.get('msg', 'unknown error')}")
            return result.get("obj")
        except (OSError, ValueError) as exc:
            if attempt == 9:
                die(f"3x-ui API unavailable at localhost for {route}: {exc}")
            time.sleep(1)


def inbounds():
    value = panel_request("GET", "panel/api/inbounds/list")
    if not isinstance(value, list):
        die("3x-ui returned an unexpected inbound list")
    return value


def configure(path):
    uid = state(path)["uuid"]
    current = [x for x in inbounds() if x.get("port") == 443 or x.get("remark") == "DR-VMESS"]
    if current:
        if len(current) == 1 and matches(current[0], uid):
            print("ALREADY_CONFIGURED")
            return
        die("CONFIG_CONFLICT: existing inbound on port 443 or with remark DR-VMESS differs from expected profile")
    panel_request("POST", "panel/api/inbounds/add", profile(uid))
    for _ in range(20):
        current = [x for x in inbounds() if x.get("port") == 443]
        if len(current) == 1 and matches(current[0], uid):
            print("VMess inbound created through official 3x-ui API")
            return
        time.sleep(1)
    die("Inbound creation was not confirmed by the panel API")


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, check=False)


def listening(port):
    result = run("ss", "-ltnH")
    if result.returncode:
        return False
    return any(line.split()[3].rsplit(":", 1)[-1] == str(port) for line in result.stdout.splitlines() if len(line.split()) >= 4)


def public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=5) as response:
            value = response.read(64).decode().strip()
        socket.inet_aton(value)
        return value
    except (OSError, ValueError):
        return "UNKNOWN"


def dns_ips():
    try:
        return sorted({x[4][0] for x in socket.getaddrinfo(DOMAIN, None, socket.AF_INET)})
    except OSError:
        return []


def verify(path):
    deployed = state(path)
    uid = deployed["uuid"]
    if not run("systemctl", "is-active", "--quiet", "x-ui").returncode == 0:
        die("HEALTH_CHECK_FAIL: x-ui systemd service is not active")
    panel_version = run("/usr/local/x-ui/x-ui", "-v")
    if panel_version.returncode or deployed["version"].lstrip("v") not in panel_version.stdout:
        die("HEALTH_CHECK_FAIL: 3x-ui version differs from pinned version")
    xray = {}
    for _ in range(20):
        status = panel_request("GET", "panel/api/server/status")
        xray = status.get("xray", {}) if isinstance(status, dict) else {}
        if xray.get("state") == "running" and listening(443) and XRAY_CONFIG.is_file():
            break
        time.sleep(1)
    if xray.get("state") != "running":
        die("HEALTH_CHECK_FAIL: embedded Xray is not running")
    if "26.9.9" not in str(xray.get("version", "")):
        die(f"HEALTH_CHECK_FAIL: unexpected bundled Xray version: {xray.get('version')}")
    current = [x for x in inbounds() if x.get("port") == 443]
    if len(current) != 1 or not matches(current[0], uid):
        die("HEALTH_CHECK_FAIL: inbound or UUID differs from expected profile")
    if not XRAY_CONFIG.is_file() or not XRAY.is_file():
        die("HEALTH_CHECK_FAIL: Xray binary or generated config missing")
    try:
        config = json.loads(XRAY_CONFIG.read_text())
    except (OSError, ValueError):
        die("HEALTH_CHECK_FAIL: generated Xray configuration is not valid JSON")
    if not any(active_match(i, uid) for i in config.get("inbounds", [])):
        die("HEALTH_CHECK_FAIL: generated Xray configuration differs from expected VMess profile")
    test = subprocess.run((str(XRAY), "run", "-test", "-config", str(XRAY_CONFIG)),
                          text=True, capture_output=True, check=False,
                          env={**os.environ, "XRAY_LOCATION_ASSET": "/usr/local/x-ui/bin"})
    if test.returncode:
        die(f"HEALTH_CHECK_FAIL: Xray config test failed: {test.stderr[-500:]}")
    if not listening(443):
        die("HEALTH_CHECK_FAIL: TCP 443 not listening")
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=3):
            pass
    except OSError as exc:
        die(f"HEALTH_CHECK_FAIL: localhost TCP 443 connection failed: {exc}")
    port = os.environ["XUI_PANEL_PORT"]
    if not listening(port):
        die("HEALTH_CHECK_FAIL: 3x-ui panel port not listening")
    addresses = [line.split()[3] for line in run("ss", "-ltnH").stdout.splitlines() if len(line.split()) >= 4 and line.split()[3].rsplit(":", 1)[-1] == port]
    if not addresses or any(not address.startswith("127.0.0.1:") for address in addresses):
        die("HEALTH_CHECK_FAIL: panel is not restricted to localhost")
    ip = public_ip()
    resolved = dns_ips()
    print("========================================")
    print("VPS DISASTER RECOVERY STATUS")
    print("========================================")
    print(f"OS: {Path('/etc/os-release').read_text().split('PRETTY_NAME=')[1].splitlines()[0].strip(chr(34))}")
    print(f"PUBLIC_IP: {ip}")
    print(f"DOMAIN: {DOMAIN}")
    print(f"DOMAIN_RESOLVES_TO: {', '.join(resolved) if resolved else 'UNKNOWN'}")
    print("VMESS: PASS\nPORT_443: LISTENING\nSERVICE: ACTIVE\n3X_UI: ACTIVE")
    print(f"DNS_SWITCH_REQUIRED: {'NO' if ip != 'UNKNOWN' and ip in resolved else 'YES'}")
    print("========================================")
    if ip == "UNKNOWN" or not resolved:
        print("WARNING: public IP or DNS lookup unavailable; verify manually before switching DNS")


def client_uri(uid):
    data = {"v": "2", "ps": "DR-VMESS", "add": DOMAIN, "port": "443", "id": uid,
            "aid": "0", "scy": "auto", "net": "tcp", "type": "none", "host": "", "path": "", "tls": "none"}
    return "vmess://" + base64.b64encode(json.dumps(data, separators=(",", ":")).encode()).decode()


def export(path):
    uid = state(path)["uuid"]
    print("CLIENT CONFIGURATION (sensitive: UUID; do not save in public logs)")
    print(client_uri(uid))
    print(f"Protocol: VMess\nAddress: {DOMAIN}\nPort: 443\nUUID: {uid}\nAlterID: 0\nEncryption: auto\nTransport: TCP / none\nTLS: off\nUDP: on (client setting)")


def main():
    action = sys.argv[1]
    if action == "init-state":
        path, version, uid = sys.argv[2:5]
        if not valid_uuid(uid):
            die("VMESS_UUID must be a canonical UUID")
        if Path(path).exists():
            die("CONFIG_CONFLICT: state already exists")
        write_state(path, {"version": version, "uuid": uid.lower()})
    elif action == "check-state":
        data = state(sys.argv[2])
        if data.get("version") != sys.argv[3] or (sys.argv[4] and data.get("uuid") != sys.argv[4].lower()):
            die("CONFIG_CONFLICT: deployed version or UUID differs; no overwrite performed")
    elif action == "state-uuid":
        print(state(sys.argv[2])["uuid"])
    elif action == "configure":
        configure(sys.argv[2])
    elif action == "verify":
        verify(sys.argv[2])
    elif action == "export":
        export(sys.argv[2])
    elif action == "self-test":
        uid = str(uuid.uuid4())
        p = profile(uid)
        assert matches(p, uid)
        assert not matches({**p, "port": 444}, uid)
        active = {k: v for k, v in p.items() if k in ("protocol", "port", "listen", "settings", "streamSettings")}
        assert active_match(active, uid)
        assert not active_match({**active, "streamSettings": {"network": "ws"}}, uid)
        assert json.loads(json.dumps(p))["settings"]["clients"][0]["alterId"] == 0
        assert json.loads(base64.b64decode(client_uri(uid)[8:]))["id"] == uid
        print("LOCAL_TEST: PASS")
    else:
        die("Usage: dr.py init-state|check-state|state-uuid|configure|verify|export|self-test")


if __name__ == "__main__":
    main()
