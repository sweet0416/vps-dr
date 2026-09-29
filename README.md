# VPS disaster recovery (v1)

当现有 VMess VPS 无法使用时，在**全新** VPS 上通过一条命令恢复相同节点。这个仓库不会登录或修改当前生产 VPS，也不会自动改 Cloudflare DNS。

## Quick start

推荐 Ubuntu 24.04 LTS。SSH 登录全新 VPS，先从 Shadowrocket 或 PassWall 找到旧 VMess UUID，再运行：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/main/bootstrap.sh | sudo bash
```

脚本会在终端询问旧 UUID。自动化运行可用 `sudo env VMESS_UUID='原 UUID' bash install.sh`（注意 shell 历史与进程环境）；直接运行仓库文件更适合演练。若没有 UUID，交互式留空会生成新的 UUID，**旧客户端必须更新**。不要把 UUID 写进 Git。

部署成功后，按 [灾难恢复手册](docs/DISASTER_RECOVERY.md) 手动切换 DNS。公网可用性和客户端兼容性必须在临时 VPS 演练后确认。

## Required inputs and supported hosts

- 全新、自己控制的 Ubuntu 22.04/24.04/26.04 或 Debian 12/13，amd64/arm64，systemd，root/sudo，互联网连接。
- 原 VMess UUID（推荐），不需要 Cloudflare/GitHub token、SSH 私钥或现有 VPS 登录权限。
- VPS 服务商防火墙也必须允许 22/tcp（或当前 SSH 端口）及 443/tcp。
- 这些系统版本仅有脚本的环境检查；**真实 VPS 安装均尚未验证**。

## What it installs

官方 3x-ui **v3.8.5**，含官方构建捆绑的 Xray-core **v26.9.9**。版本核对日期：2026-09-29。固定安装器来自 [3x-ui v3.8.5 commit](https://github.com/MHSanaei/3x-ui/tree/v3.8.5)；安装器从同版 release 获取资产并核对官方 SHA-256。自动创建 inbound 使用 [官方 API](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/docs/public/openapi.json)。Xray 不单独安装或升级。

VMess profile: TCP :443、alterId 0、AEAD、`security=auto`、TCP header `none`、TLS off。客户端 UDP 开关保持 on；流量通过 VMess TCP 传输。这个裸 TCP/无 TLS 组合沿用现有客户端设置，流量缺少 TLS 保护，V1 仅为兼容性灾备。

## Cloudflare DNS

灾难发生时，人工把 `node.passwallv2ray.top` 的 A 记录从旧 IP `38.54.95.213` 改到 **NEW_VPS_IP**，并保持 **DNS Only（灰云）**。不要打开橙云。健康检查发现 DNS 仍指向旧 IP 时只提示，不判安装失败。

## Security

3x-ui 官方安装器生成随机用户名、密码、Web path 和 API token，保存到新 VPS 的 `/etc/x-ui/install-result.env`（root only）。请立即把凭据保存到密码管理器。面板只绑定 `127.0.0.1`，通过 SSH 隧道访问；公网只开放 SSH 和 VMess 443/tcp。安装过程的 root-only 日志可能含面板凭据，请勿上传。脚本不修改 SSH 登录方式。

```bash
ssh -L PANEL_PORT:127.0.0.1:PANEL_PORT root@NEW_VPS_IP
```

打开安装输出中的 `http://127.0.0.1:PANEL_PORT/WEB_PATH/`。首次安装的临时公网监听受预先启用的 UFW 默认拒绝入站规则保护；服务商侧规则仍需人工确认。不要把 `vmess://` 链接发到公共日志。

## Checks and maintenance

```bash
sudo bash /opt/vps-dr/health-check.sh
sudo bash /opt/vps-dr/export-client.sh
sudo bash /opt/vps-dr/uninstall.sh        # 明确输入 REMOVE 才会停服务
sudo env DRY_RUN=1 bash /opt/vps-dr/install.sh
```

`--dry-run` 只检查 OS/架构并显示动作，不安装、不改防火墙。第二次运行会沿用 `/etc/vps-dr/state.json` 中的 UUID；匹配时显示 `ALREADY_CONFIGURED`，冲突时停止。卸载脚本停用服务并保留数据库、凭据、二进制和防火墙规则供恢复。

**升级策略：** 先在新临时 VPS 演练。核对官方稳定 release、安装器和捆绑 Xray 版本，再同时修改 `config/defaults.env` 的版本与 commit；运行静态测试和真实 VPS 验证后发布。现有部署不会自动升级；故意对版本不一致的重跑报 `CONFIG_CONFLICT`。

故障排查见 [TROUBLESHOOTING](docs/TROUBLESHOOTING.md)，安全界限见 [SECURITY](docs/SECURITY.md)。V2 可研究 VLESS Reality、sing-box、VPS/DNS API、监测与多地区，但不属于 V1。
