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
import tempfile
import urllib.request
import uuid
from pathlib import Path

sys.dont_write_bytecode = True

DOMAIN = os.environ.get("VPS_DR_DOMAIN", "")
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
        die("CONFIG_CONFLICT: VMESS_UUID 必须为规范 UUID")


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
        if not isinstance(data, dict) or data.get("architecture") != "direct-xray-v1":
            raise ValueError("state architecture")
        data["uuid"] = canonical_uuid(data["uuid"])
        if (not re.fullmatch(r"v\d+\.\d+\.\d+", data["xray_version"])
                or not re.fullmatch(r"\d+\.\d+\.\d+(?:-dev)?", data["deployment_version"])):
            raise ValueError("state version")
        return data
    except (OSError, ValueError, KeyError, TypeError):
        die("CONFIG_CONFLICT: 状态文件缺失、损坏或权限不安全，请检查 root 专用状态文件")


def write_state(path, uid, xray_version, deployment_version):
    p = Path(path)
    uid = canonical_uuid(uid)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    data = {"architecture": "direct-xray-v1", "uuid": uid, "xray_version": xray_version,
            "deployment_version": deployment_version}
    fd, temporary = tempfile.mkstemp(prefix=".state-", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            os.rename(temporary, p)  # Windows rename also refuses existing targets.
        else:
            os.link(temporary, p)  # Atomic, refuses existing targets on Linux.
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def check_state(path, version, deployment_version, supplied_uid=""):
    data = read_state(path)
    if (data["deployment_version"] not in {"0.1.0-dev", "1.0.0", "1.1.0", deployment_version}
            or (supplied_uid and data["uuid"] != canonical_uuid(supplied_uid))):
        die("CONFIG_CONFLICT: Xray 版本、状态格式或 UUID 不兼容，已停止覆盖")
    if data["xray_version"] != version:
        try:
            import update
            update.verify_installed(path, check_unit=False, check_config=False)
        except (ImportError, OSError, ValueError, RuntimeError):
            die("CONFIG_CONFLICT: Xray 核心没有匹配的官方升级校验记录，已停止覆盖")
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


def validate_server(server):
    if not isinstance(server, str) or not server or len(server) > 253:
        die("SERVER_INVALID: --server 需要有效 IPv4 或完整域名")
    try:
        addr = ipaddress.IPv4Address(server)
        if not addr.is_global or addr.is_multicast:
            die("SERVER_INVALID: 请输入 VPS 的公网 IPv4")
        return str(addr)
    except ipaddress.AddressValueError:
        if re.fullmatch(r"[0-9.]+", server) or not re.fullmatch(
                r"(?=.{1,253}$)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+"
                r"[A-Za-z](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", server):
            die("SERVER_INVALID: --server 需要有效 IPv4 或完整域名，不要填写协议、路径或端口")
        return server.lower()


def uri_payload(uid, server=None):
    server = server or public_ip()
    if server == "UNKNOWN":
        die("PUBLIC_IP_UNAVAILABLE: 公网 IPv4 获取失败，请使用 --server 指定地址")
    server = validate_server(server)
    return {"v": "2", "ps": "VPS-DR", "add": server, "port": "443",
            "id": canonical_uuid(uid), "aid": "0", "scy": "auto", "net": "tcp",
            "type": "none", "host": "", "path": "", "tls": "none"}


def encode_uri(uid, server=None):
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
        die("HEALTH_CHECK_FAIL: 无法检查 TCP 443 监听状态")
    lines = [line for line in result.stdout.splitlines() if re.search(r"(?<!\d):443\s", line)]
    if len(lines) != 1:
        die("HEALTH_CHECK_FAIL: TCP 443 监听数量异常")
    match = re.search(r"pid=(\d+)", lines[0])
    if not match:
        die("HEALTH_CHECK_FAIL: 无法确认 TCP 443 所属进程")
    return int(match.group(1))


def public_ip():
    try:
        with urllib.request.urlopen("https://api.ipify.org", timeout=8) as response:
            value = response.read(64).decode().strip()
        address = ipaddress.IPv4Address(value)
        return str(address) if address.is_global else "UNKNOWN"
    except (OSError, ValueError):
        return "UNKNOWN"


def dns_ips():
    if not DOMAIN:
        return []
    try:
        return sorted({item[4][0] for item in socket.getaddrinfo(DOMAIN, None, socket.AF_INET)})
    except OSError:
        return []


def health(path):
    state = read_state(path)
    try:
        import update
        update.verify_installed(path)
    except (ImportError, OSError, ValueError, RuntimeError):
        die("HEALTH_CHECK_FAIL: 核心摘要、配置或实际服务定义不受信，请核对后重试")
    if not CONFIG.is_file() or CONFIG.is_symlink() or (os.name != "nt" and
                               (CONFIG.stat().st_uid != 0 or CONFIG.stat().st_mode & 0o077)):
        die("HEALTH_CHECK_FAIL: 配置路径或权限不安全")
    if not XRAY.is_file() or not xray_version_ok(state["xray_version"]):
        die("HEALTH_CHECK_FAIL: Xray 程序不存在或版本不匹配")
    if not config_matches(CONFIG, state["uuid"]):
        die("HEALTH_CHECK_FAIL: 配置或 UUID 与本机状态不一致")
    result = run(str(XRAY), "run", "-test", "-config", str(CONFIG))
    if result.returncode:
        die("HEALTH_CHECK_FAIL: Xray 配置校验失败")
    for _ in range(20):
        if (run("systemctl", "is-active", "--quiet", UNIT).returncode == 0
                and run("ss", "-ltnH", "( sport = :443 )").stdout.strip()):
            break
        time.sleep(1)
    else:
        die("HEALTH_CHECK_FAIL: Xray 服务或 TCP 443 未就绪")
    pid = listener_pid()
    service_pid = run("systemctl", "show", "--property=MainPID", "--value", UNIT)
    if service_pid.returncode or service_pid.stdout.strip() != str(pid):
        die("HEALTH_CHECK_FAIL: TCP 443 被其他服务占用")
    if os.path.realpath(f"/proc/{pid}/exe") != str(XRAY):
        die("HEALTH_CHECK_FAIL: TCP 443 监听程序不是已安装的 Xray")
    try:
        with socket.create_connection(("127.0.0.1", 443), timeout=3):
            pass
    except OSError:
        die("HEALTH_CHECK_FAIL: 本机 TCP 443 无法连接")
    ip, resolved = public_ip(), dns_ips()
    print("=== VPS_DR_HEALTH ===")
    print(f"DEPLOYMENT_VERSION: {state['deployment_version']}")
    print(f"XRAY_VERSION: {state['xray_version']}")
    print("CONFIG_JSON: PASS\nXRAY_CONFIG_TEST: PASS\nSYSTEMD: ACTIVE\nPORT_443: XRAY")
    print(f"PUBLIC_IP: {ip}\nDOMAIN: {DOMAIN or '未设置（使用 IP）'}")
    print(f"DOMAIN_RESOLVES_TO: {','.join(resolved) if resolved else 'UNKNOWN'}")
    print("LOCAL_SERVICE: PASS — 本机服务正常。客户端连通性需在客户端实际测试。")
    if ip == "UNKNOWN" or (DOMAIN and not resolved):
        print("NETWORK_NOTICE: 公网 IP 或可选域名未能确认；本机服务检查已通过。")


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
            die("CONFIG_CONFLICT: 现有 Xray 配置与状态不一致，已停止覆盖")
    elif action == "health":
        health(args[0])
    elif action == "validate-server":
        print(validate_server(args[0]))
    elif action == "public-ip":
        address = public_ip()
        if address == "UNKNOWN":
            die("PUBLIC_IP_UNAVAILABLE: 无法获取公网 IPv4，请使用 --server 指定地址")
        print(address)
    elif action == "export":
        state = read_state(args[0])
        options = args[1:]
        show_qr = bool(options and options[-1] == "--qr")
        if show_qr:
            options = options[:-1]
        if len(options) > 1:
            die("Usage: export [SERVER] [--qr]")
        server = validate_server(options[0]) if options else public_ip()
        if server == "UNKNOWN":
            die("公网 IP 获取失败，请使用 --server 指定这台 VPS 的 IPv4 地址。")
        uri = encode_uri(state["uuid"], server)
        print("客户端配置（含密钥，请勿公开或发送到日志）")
        print(uri)
        print(f"协议: VMess\n地址: {server}\n端口: 443\nUUID: {state['uuid']}")
        print("AlterID: 0\nAEAD: 开启\n加密: auto\n传输: tcp / none\nTLS: 关闭\nUDP: 由客户端设置")
        if show_qr:
            print(f"=== PRIVATE VMESS QR: {server}:443 ===", flush=True)
            print("请用 Shadowrocket 扫码导入；请勿公开终端输出。", flush=True)
            try:
                result = subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "2", "-o", "-"],
                                        input=uri, text=True, check=False, timeout=15)
                available = result.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                available = False
            print(f"QR_CODE_DISPLAY: {'PASS' if available else 'UNAVAILABLE'}")
            if not available:
                print("二维码不可用，请复制上方 vmess:// 链接导入。")
    elif action == "self-test":
        uid = str(uuid.uuid4())
        assert json.loads(json.dumps(profile(uid))) == profile(uid)
        assert decode_uri(encode_uri(uid, "1.2.3.4")) == uri_payload(uid, "1.2.3.4")
        assert decode_uri(encode_uri(uid, "1.2.3.4")) == uri_payload(uid, "1.2.3.4")
        print("LOCAL_TEST: PASS")
    else:
        die("Unknown action")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.TimeoutExpired) as exc:
        die(f"OPERATION_FAILED: 系统命令或文件操作失败，请检查依赖、权限及服务日志（{type(exc).__name__}）。")
