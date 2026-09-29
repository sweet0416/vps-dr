# 排查

**XRAY_STABLE_COMPATIBILITY_BLOCKED**：3x-ui v3.8.5 捆绑预发布核心，当前稳定 Xray 不在其允许更新列表。当前停止安装；先完成稳定组合审计，不得强行替换二进制。

**PORT_443_CONFLICT / PANEL_PORT_CONFLICT**：端口被未知程序使用。查看预检报告，人工辨认进程；脚本不会自动结束它。

**CONFIG_CONFLICT / PARTIAL_INSTALL_REQUIRES_REVIEW**：机器上已有未归本项目管理的 3x-ui、已有 inbound 配置不匹配、上游安装残留，或重跑时 UUID/版本改变。不要强行覆盖；优先用另一台干净 VPS。

**安装器失败**：在**新 VPS** 查看 `sudo less /var/log/vps-dr-install.log`。该日志可能含面板密码，不要公开上传。

**面板打不开**：从安装结果取端口和 path，用 SSH 隧道连接。`sudo systemctl status x-ui`、`sudo ss -ltn` 检查本机监听。面板不应能从公网直接访问。

**443 未监听或配置失败**：运行 `sudo bash /opt/vps-dr/health-check.sh`，查看 `sudo journalctl -u x-ui -n 100`，核对服务商防火墙 443/tcp。`xray run -test` 是配置静态验证；客户端实连仍需测试。

**DNS 仍是旧 IP**：若尚未切换，这是预期。部署通过后人工修改 Cloudflare A/node 为新 IP，保持 DNS Only，并等待缓存更新。

**客户端仍不通**：确认使用原 UUID、VMess/TCP、443、alterId 0、auto、TLS 关闭；核对 Shadowrocket/PassWall 使用域名而不是硬编码旧 IP。公网连通性必须由真实客户端确认。
