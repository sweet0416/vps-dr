# 灾难当天：9 步恢复（当前版本未放行）

**当前禁止执行安装。** `DR_RELEASE_READY=NO`；先完成 [发布门槛](RELEASE_GATE.md)。下列步骤只在 `v1.0.0` 经真实 VPS 与客户端验证并创建后生效。当前测试使用 [临时 VPS 计划](REAL_VPS_TEST_PLAN.md)，不切生产 DNS。

1. 在 LightNode 或其他服务商创建**全新** Ubuntu 24.04 LTS VPS，确认服务商防火墙允许 SSH 和 443/tcp。不要操作旧 VPS。
2. 从 Shadowrocket/PassWall 查看并安全记录**原 VMess UUID**。
3. `ssh root@NEW_IP` 登录新 VPS。
4. 执行：`curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/v1.0.0/bootstrap.sh | sudo bash`（tag 尚不存在，当前不可执行）。先运行 `--preflight` 并确认 `READY_TO_INSTALL: YES`。
5. 在终端输入原 UUID；不会自动生成新 UUID。等待 `VMESS: PASS`、`SERVICE: ACTIVE`、`PORT_443: LISTENING`。若失败，先看 [排查说明](TROUBLESHOOTING.md)，**不要切 DNS**。
6. 在 Cloudflare 把 `node.passwallv2ray.top` 的 **A / node** 从 `38.54.95.213` 改成 **NEW_IP**，代理状态保持 **DNS Only / 灰云**。
7. 用 `nslookup node.passwallv2ray.top` 或 `dig node.passwallv2ray.top` 确认解析到 NEW_IP；可能需要等待 DNS 缓存过期。
8. 在 Shadowrocket 和 PassWall 上用原节点做**真实连接测试**。如需临时配置，运行 `sudo bash /opt/vps-dr/export-client.sh` 输出 VMess 链接。
9. 确认新节点稳定后再决定旧 VPS 的去留；不要立即删除。

面板仅本机可访问。安装输出会给 SSH 隧道命令、面板 URL 和 root-only 凭据位置。

回退：新 VPS 失败时保持原 DNS 不变；修复脚本后在新的测试 VPS 复测。若已切 DNS，人工把 A 记录改回已确认可用的地址。这个仓库从不自动改 DNS。
