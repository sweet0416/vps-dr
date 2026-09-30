# VPS DR V1 — direct Xray

## Purpose

在**全新 VPS** 上恢复个人自托管的 VMess TCP/443 灾难恢复服务。V1 安装固定稳定版 Xray、最小 JSON 配置和 systemd 服务。V1 正式稳定版本使用固定 tag `v1.0.0`；`main` 是后续开发入口，不是灾难当天的首选入口。临时 LightNode 已完成 Ubuntu 24.04 amd64 安装、重启、幂等性及 Shadowrocket 扫码和实连验收。PassWall 实际连接未纳入 V1 必需验收范围。

本仓库仅供作者个人自托管灾难恢复使用；使用范围、责任与限制见 [Disclaimer / 免责声明](DISCLAIMER.md)。

## Quick Start

正式灾备版本使用固定 tag `v1.0.0`。该 tag 对应冻结的 V1 恢复快照；后续开发使用 `main` 和新的版本号，不回写或移动 `v1.0.0`。

正式灾备命令：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/v1.0.0/bootstrap.sh | sudo bash
```

从 raw 管道运行时，bootstrap 默认下载固定版本 `v1.0.0`；在检出的仓库目录直接运行时使用该目录中的文件。开发预检如需使用 `main`，须显式指定 `VPS_DR_REF=main`。恢复前应核对所用 tag 对应的提交 SHA，并保存旧 VMess UUID。新 VPS 部署后先用临时 IP 验证 Shadowrocket；验证通过后再切换 Cloudflare DNS。不要先删除旧 VPS。

## Preflight

在新机器上先运行，只检查，不安装、不改防火墙/systemd/配置：

```bash
sudo ./bootstrap.sh --preflight
```

预检检查 Ubuntu 22.04/24.04/26.04 或 Debian 12/13、amd64/arm64、root、systemd、必需命令、网络、DNS、磁盘、内存、已有 Xray/配置和 TCP 443 监听者。未知安装或未知 443 占用会停止。新机需要至少 1 GiB 可用磁盘和 256 MiB 可用内存。真实验收已在 Ubuntu 24.04 amd64 完成；其他系统和架构尚未实机验证。

## Install

推荐复用旧客户端的 UUID，在临时测试机的**私密终端**输入；避免把 UUID 留在 shell 历史中：

```bash
sudo ./bootstrap.sh
```

也支持 `sudo VMESS_UUID="<uuid>" ./bootstrap.sh`。无 UUID 时可在提示处留空，或无人值守时不设置变量；脚本会生成新 UUID，并醒目提示旧客户端配置不再匹配。UUID 只写入新机 root-only `/etc/vps-dr/state.json` 和 `/usr/local/etc/xray/config.json`，绝不写入 Git。不要把安装输出或 `vmess://` 链接公开。面板、数据库、API token 和面板端口均不属于 V1。

在交互式终端安装成功或重跑确认 `ALREADY_CONFIGURED` 后，脚本会通过 `export-client.sh` 自动显示 Shadowrocket 手动参数、`vmess://` URI 和可扫描的终端二维码。三者使用新 VPS 检测到的公网 IP 和同一份本地 UUID；演练期间不会误用仍指向生产机的域名。若缺少 `qrencode`，脚本会尝试从 Ubuntu/Debian 官方包仓库安装；失败时仍输出 URI 和 `QR_CODE_DISPLAY: UNAVAILABLE`，不影响运行中的 Xray。二维码和 URI 含 UUID，勿截图公开或保存到日志；非交互式输出会跳过客户端凭据。

固定 Xray `v26.3.27` 来自 [官方 release](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27)。amd64/arm64 包同时用仓库固定 SHA-256 与官方 `.dgst` 的 SHA2-256 核对。配置先通过 `xray run -test`，再启动 systemd；服务的 `ExecStartPre` 也会再次验证配置。详情见 [发行资产审计](docs/XRAY_RELEASE_AUDIT.md)。脚本仅在已有 UFW **处于 active** 且缺少 443/tcp 允许规则时添加该规则；不启用 UFW、不更改默认策略或 SSH 配置。服务商防火墙仍需人工允许 443/tcp。

## Test with temporary IP

临时 VPS 演练期间，生产 DNS 继续指向 `38.54.95.213`。在新机导出使用临时 IP 的客户端配置：

```bash
sudo /opt/vps-dr/health-check.sh
sudo /opt/vps-dr/export-client.sh --server NEW_VPS_IP
```

导出内容包含 UUID。安装完成时也会自动显示使用新 VPS 公网 IP 的手动参数、URI 和二维码。先在 Shadowrocket **新增临时节点**测试，不覆盖现有节点；PassWall 测试可选。默认不带 `--server` 时，导出地址为 `node.passwallv2ray.top`。URI 采用已记录的 VMess Base64 JSON 分享格式并做本地 encode/decode 往返；自动显示和 Shadowrocket 扫码、连通性已在测试 VPS 验证。按 [真实 VPS 测试计划](docs/REAL_VPS_TEST_PLAN.md) 操作。

## DNS Cutover

只有临时 VPS、Shadowrocket 和幂等性通过且发布门槛放行后，灾难发生时才人工切换 `node.passwallv2ray.top` 的 A 记录，保持 **DNS Only / 灰云**。PassWall 实际连接不属于 V1 必需发布门槛。本仓库不会调用 Cloudflare。DNS 尚指向旧 IP 时，健康检查显示 `DNS_SWITCH_REQUIRED: YES`，不因此判定 Xray 故障。

## Recovery

同版本、同 UUID、同配置重跑会验证并输出 `ALREADY_CONFIGURED`；不同 UUID/配置、未知二进制、未知 systemd unit 或未知 443 服务会停止，不会覆盖或结束未知进程。若安装中断，root-only state 会保留 UUID，重跑只补齐缺失的本项目文件；发现已存在文件不一致时停止。故障处理见 [排查](docs/TROUBLESHOOTING.md)。

## Uninstall

在单独测试机上可运行 `sudo /opt/vps-dr/uninstall.sh`，输入 `REMOVE`。这会停用 Xray 并移除本项目 unit；保留二进制、root-only 配置/state 和防火墙规则以便恢复。不会删除未知文件。安全界限见 [SECURITY](docs/SECURITY.md)，灾难当天步骤见 [DISASTER_RECOVERY](docs/DISASTER_RECOVERY.md)。
