#!/usr/bin/env python3
"""Small, dependency-free config, state, share-link, and health helpers."""
import base64
import binascii
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

DOMAIN = "node.passwallv2ray.top"
XRAY = Path("/usr/local/bin/xray")
CONFIG = Path("/usr/local/etc/xray/config.json")
UNIT = "xray.service"


def die(message):
    raise SystemExit(message)


def canonical_uuid(value):
    try:
        parsed = uuid.UUID(value)
        if str(parsed) != value.lower():
            raise ValueError("UUID must be canonical")
        return str(parsed)
    except (AttributeError, ValueError, TypeError):
        die("CONFIG_CONFLICT: VMESS_UUID must be a canonical UUID")


def profile(uid):
    return {
        "log": {"loglevel": "warning"},
        "inbounds": [{
            "listen": "0.0.0.0", "port": 443, "protocol": "vmess",
            "settings": {"clients": [{"id": canonical_uuid(uid), "alterId": 0}]},
            "streamSettings": {"network": "tcp", "security": "none",
                               "tcpSettings": {"header": {"type": "none"}}},
            "sniffing": {"enabled": False},
        }],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }


def read_state(path):
    p = Path(path)
    try:
        if p.is_symlink():
            raise ValueError("state symlink")
        meta = p.stat()
        if os.name != "nt" and os.geteuid() == 0 and (meta.st_uid != 0 or meta.st_mode & 0o077):
            raise ValueError("state permissions")
        data = json.loads(p.read_text(encoding="utf-8"))
        if data.get("architecture") != "direct-xray-v1":
            raise ValueError("state architecture")
        data["uuid"] = canonical_uuid(data["uuid"])
        if (not re.fullmatch(r"v\d+\.\d+\.\d+", data["xray_version"])
                or not re.fullmatch(r"\d+\.\d+\.\d+(?:-dev)?", data["deployment_version"])):
            raise ValueError("state version")
        return data
    except (OSError, ValueError, KeyError, TypeError):
        die("CONFIG_CONFLICT: missing or invalid root-only state")


def write_state(path, uid, xray_version, deployment_version):
    p = Path(path)
    uid = canonical_uuid(uid)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"architecture": "direct-xray-v1", "uuid": uid, "xray_version": xray_version,
            "deployment_version": deployment_version}
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
        stream.write("\n")


def check_state(path, version, deployment_version, supplied_uid=""):
    data = read_state(path)
    if (data["xray_version"] != version or data["deployment_version"] != deployment_version
            or (supplied_uid and data["uuid"] != canonical_uuid(supplied_uid))):
        die("CONFIG_CONFLICT: deployed version or UUID differs; no overwrite")
    return data


def render_config(path, uid):
    p = Path(path)
    data = profile(uid)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2)
        stream.write("\n")


def config_matches(path, uid):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8")) == profile(uid)
    except (OSError, ValueError, TypeError):
        return False


def uri_payload(uid, server=DOMAIN):
    if server != DOMAIN:
        try:
            ipaddress.IPv4Address(server)
        except ipaddress.AddressValueError:
            die("--server requires an IPv4 address for the temporary VPS")
    return {"v": "2", "ps": "VPS-DR", "add": server, "port": "443",
            "id": canonical_uuid(uid), "aid": "0", "scy": "auto", "net": "tcp",
            "type": "none", "host": "", "path": "", "tls": "none"}


def encode_uri(uid, server=DOMAIN):
    data = uri_payload(uid, server)
    raw = json.dumps(data, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    uri = "vmess://" + base64.b64encode(raw).decode("ascii")
    if decode_uri(uri) != data:
        die("VMESS_URI_ROUNDTRIP_FAIL")
    return uri


def decode_uri(uri):
    if not uri.startswith("vmess://"):
        die("VMESS_URI_INVALID")
    try:
        value = json.loads(base64.b64decode(uri[8:], validate=True))
    except (ValueError, UnicodeDecodeError, binascii.Error):
        die("VMESS_URI_INVALID")
    if not isinstance(value, dict):
        die("VMESS_URI_INVALID")
    return value


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, check=False, timeout=15)


def xray_version_ok(version):
    result = run(str(XRAY), "version")
    return result.returncode == 0 and re.search(r"^Xray " + re.escape(version.lstrip("v")) + r"\b", result.stdout) is not None


def listener_pid():
    result = run("ss", "-ltnpH", "( sport = :443 )")
    if result.returncode:
        die("HEALTH_CHECK_FAIL: ss could not inspect TCP 443")
    lines = [line for line in result.stdout.splitlines() if re.search(r"(?<!\d):443\s", line)]
    if len(lines) != 1:
        die("HEALTH_CHECK_FAIL: expected one TCP 443 listener")
    match = re.search(r"pid=(\d+)", lines[0])
    if not match:
        die("HEALTH_CHECK_FAIL: TCP 443 owner unavailable")
    return int(match.group(1))


def public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=8) as response:
            value = response.read(64).decode().strip()
        return str(ipaddress.IPv4Address(value))
    except (OSError, ValueError):
        return "UNKNOWN"


def dns_ips():
    try:
        return sorted({item[4][0] for item in socket.getaddrinfo(DOMAIN, None, socket.AF_INET)})
    except OSError:
        return []


def health(path):
    state = read_state(path)
    if not CONFIG.is_file() or CONFIG.is_symlink() or (os.name != "nt" and
                               (CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o077)):
        die("HEALTH_CHECK_FAIL: config permissions or path are unsafe")
    if not XRAY.is_file() or not xray_version_ok(state["xray_version"]):
        die("HEALTH_CHECK_FAIL: Xray binary missing or version mismatch")
    if not config_matches(CONFIG, state["uuid"]):
        die("HEALTH_CHECK_FAIL: config or UUID differs from root-only state")
    result = run(str(XRAY), "run", "-test", "-config", str(CONFIG))
    if result.returncode:
        die("HEALTH_CHECK_FAIL: Xray config test failed")
    for _ in range(20):
        if (run("systemctl", "is-active", "--quiet", UNIT).returncode == 0
                and run("ss", "-ltnH", "( sport = :443 )").stdout.strip()):
            break
        time.sleep(1)
    else:
        die("HEALTH_CHECK_FAIL: xray.service or TCP 443 did not become active")
    pid = listener_pid()
    service_pid = run("systemctl", "show", "--property=MainPID", "--value", UNIT)
    if service_pid.returncode or service_pid.stdout.strip() != str(pid):
        die("HEALTH_CHECK_FAIL: TCP 443 does not belong to xray.service")
    if os.path.realpath(f"/proc/{pid}/exe") != str(XRAY):
        die("HEALTH_CHECK_FAIL: TCP 443 owner is not the installed Xray binary")
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=3):
            pass
    except OSError:
        die("HEALTH_CHECK_FAIL: localhost TCP 443 unavailable")
    ip, resolved = public_ip(), dns_ips()
    print("=== VPS_DR_HEALTH ===")
    print(f"DEPLOYMENT_VERSION: {state['deployment_version']}")
    print(f"XRAY_VERSION: {state['xray_version']}")
    print("CONFIG_JSON: PASS\nXRAY_CONFIG_TEST: PASS\nSYSTEMD: ACTIVE\nPORT_443: XRAY")
    print(f"PUBLIC_IP: {ip}\nDOMAIN: {DOMAIN}")
    print(f"DOMAIN_RESOLVES_TO: {','.join(resolved) if resolved else 'UNKNOWN'}")
    print(f"DNS_SWITCH_REQUIRED: {'NO' if ip != 'UNKNOWN' and ip in resolved else 'YES'}")
    if ip == "UNKNOWN" or not resolved:
        die("HEALTH_CHECK_FAIL: public IP or domain resolution not verified")


def main():
    if len(sys.argv) < 2:
        die("Usage: dr.py new-uuid|state-init|state-check|state-uuid|render|config-check|health|public-ip|export|self-test")
    action, *args = sys.argv[1:]
    if action == "new-uuid":
        print(uuid.uuid4())
    elif action == "state-init":
        write_state(*args)
    elif action == "state-check":
        check_state(*args)
    elif action == "state-uuid":
        print(read_state(args[0])["uuid"])
    elif action == "render":
        render_config(*args)
    elif action == "config-check":
        if not config_matches(*args):
            die("CONFIG_CONFLICT: existing Xray config differs")
    elif action == "health":
        health(args[0])
    elif action == "public-ip":
        address = public_ip()
        if address == "UNKNOWN":
            die("PUBLIC_IP_UNAVAILABLE: cannot export a temporary-IP client node")
        print(address)
    elif action == "export":
        state = read_state(args[0])
        options = args[1:]
        show_qr = bool(options and options[-1] == "--qr")
        if show_qr:
            options = options[:-1]
        if len(options) > 1:
            die("Usage: export [SERVER] [--qr]")
        server = options[0] if options else DOMAIN
        uri = encode_uri(state["uuid"], server)
        print("CLIENT CONFIGURATION — contains UUID; do not publish")
        print(uri)
        print(f"Protocol: VMess\nAddress: {server}\nPort: 443\nUUID: {state['uuid']}")
        print("AlterID: 0\nAEAD: enabled\nEncryption: auto\nTransport: tcp / none\nTLS: off\nUDP: client setting")
        if show_qr:
            print(f"=== PRIVATE VMESS QR: {server}:443 ===", flush=True)
            print("Scan in Shadowrocket; keep this terminal output private.", flush=True)
            try:
                result = subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "2", "-o", "-"],
                                        input=uri, text=True, check=False)
                available = result.returncode == 0
            except OSError:
                available = False
            print(f"QR_CODE_DISPLAY: {'PASS' if available else 'UNAVAILABLE'}")
    elif action == "self-test":
        uid = str(uuid.uuid4())
        assert json.loads(json.dumps(profile(uid))) == profile(uid)
        assert decode_uri(encode_uri(uid)) == uri_payload(uid)
        assert decode_uri(encode_uri(uid, "1.2.3.4")) == uri_payload(uid, "1.2.3.4")
        print("LOCAL_TEST: PASS")
    else:
        die("Unknown action")


if __name__ == "__main__":
    main()
