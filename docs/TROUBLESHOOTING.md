# 排查

**PREFLIGHT_BLOCKED / EXISTING_XRAY_CONFLICT**：主机已有未知 Xray 二进制、配置、systemd unit 或 `/opt/vps-dr`。脚本不会覆盖；先人工确认来源，优先换一台干净的临时 VPS。

**PORT_443_CONFLICT**：443 已有未知监听者。预检会显示 PID/process；不要让脚本结束它。选用空白 VPS 或人工排查。

**CONFIG_CONFLICT**：本项目 state、配置 UUID、版本或已安装文件不一致。不要删除 state 或覆盖配置来绕过检查；先核对 UUID、版本和文件来源。

**CHECKSUM_MISMATCH**：官方 ZIP、`.dgst` 和仓库固定 SHA-256 不一致。停止安装，重新核对官方 release；不要跳过校验。

**XRAY_CONFIG_TEST_FAIL / systemd 未启动**：在**新 VPS**运行 `sudo /usr/local/bin/xray run -test -config /usr/local/etc/xray/config.json`，再看 `sudo journalctl -u xray -n 100`。这些输出可能包含服务器信息，分享前脱敏。

**公网 TCP 443 不通**：检查服务商安全组及已有 UFW 规则。脚本不更改 SSH；健康检查的 localhost TCP 成功不能证明公网可达。

**DNS_SWITCH_REQUIRED: YES**：演练期间 DNS 保持旧 IP，这是预期。使用 `export-client.sh --server NEW_VPS_IP` 测试，不切生产 DNS。

**客户端无法连接**：核对 VMess、TCP、443、同一 UUID、AlterID 0、AEAD、auto、TLS off，并确认服务器和客户端时间同步。URI 往返测试不能代替实际客户端连接；V1 必需验收使用 Shadowrocket，PassWall 为可选项。
