# VPS disaster recovery

本仓库只用于在**新 VPS** 恢复 3x-ui + VMess TCP/443 节点。不会登录当前生产 VPS，也不会修改 Cloudflare DNS。本项目目前处于 `0.1.0-dev`，**禁止安装和真实 VPS 演练**：3x-ui v3.8.5 捆绑预发布 Xray v26.9.9，而其核心管理接口拒绝当前最新稳定版 v26.3.27。`--preflight` 会报告 `XRAY_STABLE_COMPATIBILITY_BLOCKED`，`READY_TO_INSTALL: NO`。

## 版本与启动入口

| 模式 | 来源 | 状态 |
| --- | --- | --- |
| DEVELOPMENT | `main` | 可变，仅供代码检查和本地预检；不得据此部署 |
| STABLE_DISASTER_RECOVERY | 固定 Git tag `v1.0.0` | **尚不存在**；真实 VPS 和客户端验证通过后才可创建 |

开发预检（只检查，不安装）：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/main/bootstrap.sh | sudo VPS_DR_REF=main bash -s -- --preflight
```

未来通过 [发布门槛](docs/RELEASE_GATE.md) 后，推荐灾备入口才是：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/v1.0.0/bootstrap.sh | sudo bash
```

该命令**目前不能执行**，因为 `v1.0.0` 尚未创建。`bootstrap.sh` 默认下载固定 tag；使用主分支必须显式设 `VPS_DR_REF=main`。下载后显示 `VPS_DR_VERSION` 和 `SOURCE_REF`；tag 与 `VERSION` 不一致会停止。tag 发布后记录并核对 tag commit SHA，避免移动 tag。日后版本更新须新建 tag，不能重写旧 tag。

## 固定组件与阻断原因

- 3x-ui 固定 `v3.8.5`，安装器固定到 commit `7ef22f94c950ff09f0870e2295fa65ad5968742c`。该发行包内置 Xray `v26.9.9`，属于 **pre-release**。
- 截至 2026-09-29，Xray 官方最新非预发布版是 `v26.3.27`。3x-ui v3.8.5 的 `GetXrayVersions` 只列出 `v26.6.27` 起的核心版本，`UpdateXray` 拒绝不在列表中的版本。
- 3x-ui 自己下载、校验并管理**一份** Xray 二进制及进程。仓库不单独安装 Xray，也不绕过面板兼容限制替换二进制。
- 需要官方明确兼容的非预发布组合，或经审计的稳定 3x-ui 版本，才可把 `XRAY_STABLE_COMPATIBLE` 改为 `YES`。版本选择与升级必须重新做静态和真实 VPS 验证。

详细证据见 [依赖审计](docs/DEPENDENCY_AUDIT.md)。

原配置保持 VMess、TCP、443、AlterID 0、AEAD、`security=auto`、TCP header `none`、TLS off。UDP 是客户端开关，承载流量仍经过 VMess TCP。TLS off 沿用旧节点配置，不提供 TLS 传输保护。

## 当前可做的检查

```bash
sudo bash ./install.sh --preflight
sudo bash ./install.sh --dry-run
python3 scripts/dr.py self-test
```

预检报告列出 OS、架构、root、网络、DNS、443 监听者、面板端口、磁盘、内存与阻断原因；不运行包管理器，不改防火墙/systemd/inbound。当前版本阻断时返回非零。要求 Ubuntu 22.04/24.04/26.04 或 Debian 12/13、amd64/arm64、systemd、apt、至少 1 GiB 可用磁盘和 256 MiB 可用内存。这些只是脚本门槛，尚未做真实 VPS 验证。

安装路径要求用户提供现有 UUID，不自动新建或更换。重复运行会核对版本、UUID、443 监听者、面板和 inbound；配置一致才复用，不一致停止。失败后只在尚无 3x-ui 与防火墙残留时重试；发现上游安装或已启用防火墙残留时停止并要求人工检查。绝不清理未知 inbound、结束未知进程、重置面板或覆盖未知配置。更多场景见 [测试计划](docs/REAL_VPS_TEST_PLAN.md)。

## 面板与客户端

3x-ui 官方安装器随机生成面板用户名、密码、Web path 和 API token，写入新机 root-only `/etc/x-ui/install-result.env`。安装日志也按 root-only 权限保存，可能含凭据。请把凭据保存到密码管理器；不得贴到 issue、聊天记录或 Git。面板固定监听 `127.0.0.1`，通过 SSH 端口转发访问；公网只需 SSH 与 443/tcp。面板端口随机选 20000–59999，选定后检查冲突，不与 443 相同。不引入 VPN、隧道服务或 PKI。

`export-client.sh` 使用 3x-ui v3.8.5 的 `/panel/api/inbounds/allLinks` 官方链接并核对内容，不自行拼 VMess URI。上游该版本的 AEAD 分享链接省略 `aid` 字段，服务端 inbound 明确 `alterId=0`；Shadowrocket/PassWall 对省略字段的导入行为必须在真实客户端测试中确认，当前为 `NOT_VERIFIED`。导出链接含 UUID，应只在私密终端查看。

灾备当天与临时演练的不同流程见 [灾难恢复手册](docs/DISASTER_RECOVERY.md) 和 [真实 VPS 测试计划](docs/REAL_VPS_TEST_PLAN.md)。当前生产 DNS 仍应指向 `38.54.95.213`，演练全程不切换。
