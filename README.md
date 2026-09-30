# VPS DR V1 — direct Xray

## Purpose

在**全新 VPS** 上恢复一个与现有 Shadowrocket / PassWall 节点参数一致的 VMess TCP/443 服务。V1 只安装固定稳定版 Xray、最小 JSON 配置和 systemd 服务。不会登录或修改当前生产 VPS `38.54.95.213`，不会自动修改 Cloudflare DNS。临时 LightNode 已完成安装、重启和幂等性演练，Shadowrocket 已由用户确认可用；PassWall 的公网出口与本次新增的自动二维码路径仍待完整验证，`v1.0.0` 尚未发布。

## Quick Start

当前 `main` 是可变开发入口；`v1.0.0` **尚未创建**。先在本地仓库或将来人工创建的临时 VPS 上检查代码。未来只有 [发布门槛](docs/RELEASE_GATE.md) 全部通过后，才使用固定 tag 的灾备命令：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/v1.0.0/bootstrap.sh | sudo bash
```

该固定 tag 命令目前不可执行。直接运行仓库中的 `bootstrap.sh` 会使用同目录的文件；从 raw 管道运行时默认下载 `v1.0.0`，开发预检须显式指定 `VPS_DR_REF=main`。`VERSION` 当前为 `0.1.0-dev`。

## Preflight

在新机器上先运行，只检查，不安装、不改防火墙/systemd/配置：

```bash
sudo ./bootstrap.sh --preflight
```

预检检查 Ubuntu 22.04/24.04/26.04 或 Debian 12/13、amd64/arm64、root、systemd、必需命令、网络、DNS、磁盘、内存、已有 Xray/配置和 TCP 443 监听者。未知安装或未知 443 占用会停止。新机需要至少 1 GiB 可用磁盘和 256 MiB 可用内存；这只是静态门槛，真实 VPS 尚未验收。

## Install

推荐复用旧客户端的 UUID，在临时测试机的**私密终端**输入；避免把 UUID 留在 shell 历史中：

```bash
sudo ./bootstrap.sh
```

也支持 `sudo VMESS_UUID="<uuid>" ./bootstrap.sh`。无 UUID 时可在提示处留空，或无人值守时不设置变量；脚本会生成新 UUID，并醒目提示旧客户端配置不再匹配。UUID 只写入新机 root-only `/etc/vps-dr/state.json` 和 `/usr/local/etc/xray/config.json`，绝不写入 Git。不要把安装输出或 `vmess://` 链接公开。面板、数据库、API token 和面板端口均不属于 V1。

在交互式终端安装成功或重跑确认 `ALREADY_CONFIGURED` 后，脚本会自动安装缺失的 Ubuntu/Debian `qrencode` 工具，并在终端显示可扫描的 VMess 二维码。二维码使用新 VPS 检测到的公网 IP；演练期间不会误用仍指向生产机的域名。二维码含 UUID，勿截图公开或保存到日志。若二维码工具安装失败，Xray 服务仍可用，脚本会明确提示；非交互式输出会跳过二维码。

固定 Xray `v26.3.27` 来自 [官方 release](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27)。amd64/arm64 包同时用仓库固定 SHA-256 与官方 `.dgst` 的 SHA2-256 核对。配置先通过 `xray run -test`，再启动 systemd；服务的 `ExecStartPre` 也会再次验证配置。详情见 [发行资产审计](docs/XRAY_RELEASE_AUDIT.md)。脚本仅在已有 UFW **处于 active** 且缺少 443/tcp 允许规则时添加该规则；不启用 UFW、不更改默认策略或 SSH 配置。服务商防火墙仍需人工允许 443/tcp。

## Test with temporary IP

临时 VPS 演练期间，生产 DNS 继续指向 `38.54.95.213`。在新机导出使用临时 IP 的客户端配置：

```bash
sudo /opt/vps-dr/health-check.sh
sudo /opt/vps-dr/export-client.sh --server NEW_VPS_IP
```

导出内容包含 UUID。安装完成时也会自动显示使用新 VPS 公网 IP 的二维码。先在 Shadowrocket、PassWall **新增临时节点**测试，不覆盖现有节点。默认不带 `--server` 时，导出地址为 `node.passwallv2ray.top`。URI 采用已记录的 VMess Base64 JSON 分享格式并做本地 encode/decode 往返；两款客户端的实际导入和连接仍必须在真实演练中确认。按 [真实 VPS 测试计划](docs/REAL_VPS_TEST_PLAN.md) 操作。

## DNS Cutover

只有临时 VPS、Shadowrocket、PassWall 和幂等性全部通过且发布门槛放行后，灾难发生时才人工切换 `node.passwallv2ray.top` 的 A 记录，保持 **DNS Only / 灰云**。本仓库不会调用 Cloudflare。DNS 尚指向旧 IP 时，健康检查显示 `DNS_SWITCH_REQUIRED: YES`，不因此判定 Xray 故障。

## Recovery

同版本、同 UUID、同配置重跑会验证并输出 `ALREADY_CONFIGURED`；不同 UUID/配置、未知二进制、未知 systemd unit 或未知 443 服务会停止，不会覆盖或结束未知进程。若安装中断，root-only state 会保留 UUID，重跑只补齐缺失的本项目文件；发现已存在文件不一致时停止。故障处理见 [排查](docs/TROUBLESHOOTING.md)。

## Uninstall

在单独测试机上可运行 `sudo /opt/vps-dr/uninstall.sh`，输入 `REMOVE`。这会停用 Xray 并移除本项目 unit；保留二进制、root-only 配置/state 和防火墙规则以便恢复。不会删除未知文件。安全界限见 [SECURITY](docs/SECURITY.md)，灾难当天步骤见 [DISASTER_RECOVERY](docs/DISASTER_RECOVERY.md)。
