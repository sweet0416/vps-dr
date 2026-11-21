# VPS DR 中文安装与管理

在 VPS 的 **root SSH 终端**执行，一条命令进入中文菜单：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/main/bootstrap.sh | bash
```

菜单提供：1 安装／续装，2 显示节点链接和二维码，3 检查运行状态，4 重启服务，5 仅升级管理脚本，0 退出。每次操作后返回菜单；失败显示原因和退出码。菜单从 /dev/tty 读取选择，支持 curl 管道。

首次安装默认生成新 UUID，导出当前 VPS 的公网 IPv4。再次执行复用本机 /etc/vps-dr/state.json 中的 UUID，补齐中断安装；不会自动轮换 UUID。域名不再是安装前提。

## 已有节点

已有旧版或手工修复过 DOMAIN 的节点，执行上方同一命令，然后选择 **5**。它只升级 /opt/vps-dr 管理文件，保留 UUID、Xray 二进制、配置、systemd 服务和防火墙，不重启服务。升级前核验本机状态、官方二进制摘要、配置与 unit；遇到未知内容会解释原因并停止。

升级成功后，离线进入菜单：`bash /opt/vps-dr/menu.sh`。需要获取后续新代码时，重新执行上方在线入口。选择 1 会完成安装／续装，可能启动尚未启动的服务并补齐必要文件。

## 固定版本与无人值守

当前代码版本为 1.1.0。固定版本入口须同时指定下载 ref，避免固定脚本又获取 main：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/v1.1.0/bootstrap.sh | VPS_DR_REF=v1.1.0 bash
```

历史 v1.0.0 tag 保持原样，不包含本次修复。main 获取最新代码；固定 tag 获取对应快照。旧 state 中的 deployment_version 记录最初安装版本，管理版本以 /opt/vps-dr/VERSION 为准；接受 0.1.0-dev、1.0.0 与当前版本的 direct-xray-v1 状态，仍严格检查 Xray 版本和 UUID。

无交互终端不会等待菜单输入。自动安装入口：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/main/bootstrap.sh | bash -s -- --install
```

将末尾 --install 换成 --preflight 或 --dry-run 可只做预检；换成 --upgrade-management 可只升级管理脚本。重定向安装输出时不会自动打印 UUID 或节点链接。

## 地址和二维码

自动查询公网 IPv4 失败时，菜单 2 会要求输入地址。也可显式提供 VPS_DR_SERVER（IPv4 或完整域名），或 VPS_DR_DOMAIN（可选域名）。环境变量需传给管道右侧的 bash。域名应解析到当前 VPS；查询失败只提示，不将本机 Xray 误判为故障。

已安装节点可使用 `bash /opt/vps-dr/export-client.sh --server example.com --qr` 显式导出域名，或不带 --server 自动导出 IP。会先核对配置 UUID 与 state。链接、二维码、手动参数使用同一个配置。qrencode 缺失时菜单尝试从系统包仓库安装；安装或显示失败时仍可复制 vmess:// 链接。

UUID、链接和二维码都是客户端密钥，请只在私密终端查看，不公开截图，不写入 CI 日志。预检、状态检查不输出 UUID。

## 安装保护与恢复

架构保持 **Xray v26.3.27 + VMess TCP/443 + systemd**。固定官方 ZIP、官方 .dgst 与二进制 SHA-256 校验保留；配置在启动前和 systemd ExecStartPre 中校验。安装／升级／重启共享安装锁。

识别 v1.0.0、交接补丁中的管理脚本，以及仅修改 DOMAIN 赋值的已知脚本；其他未知修改停止覆盖。已知脚本缺失可以补齐。安装管理脚本先于启动和健康检查，避免服务已启动却没有管理入口。

管理目录升级前复制到同盘暂存目录，再将旧目录保存为 /opt/.vps-dr.backup-*。交换失败会恢复旧目录；进程在交换间被终止时，下次安装或升级会恢复 /opt/.vps-dr.previous。备份只包含管理脚本，不包含节点密钥。回滚时将下面的 BACKUP_PATH 替换为升级输出的具体备份目录：

```bash
flock /run/lock/vps-dr.lock python3 /opt/vps-dr/scripts/manage.py rollback BACKUP_PATH /opt/vps-dr
```

回滚不操作 Xray 服务和节点配置；回到没有 menu.sh 的旧版后，可重新使用在线入口。

支持预检 Ubuntu 22.04/24.04/26.04、Debian 12/13，amd64/arm64；需要 systemd、至少 1 GiB 可用磁盘和 256 MiB 可用内存。未知二进制、配置、unit、覆盖片段、端口占用或不安全路径会阻止安装／重启。仅在现有 UFW 已激活且缺少规则时放行 TCP 443；服务商安全组仍需自行放行。

本机健康检查通过仅表示配置、服务、进程归属和本机端口正常。**客户端实际连通性仍需用 Shadowrocket 等客户端测试**。此次修复不使用现有生产 VPS 做重装测试。

## 验证与文档

本地：`python3 -m unittest discover -s tests -v`。Linux root 隔离测试覆盖菜单分支、管道终端、手工地址、错误返回和无终端；所有服务操作使用临时测试脚本。GitHub Actions 另做 Bash 语法检查。

本次验证和限制见 [1.1.0 验证记录](docs/VALIDATION_1_1.md)。[原版验收记录](docs/RELEASE_GATE.md)只适用于历史 v1.0.0，不能作为本次实机验证证据。[Xray 固定资产审计](docs/XRAY_RELEASE_AUDIT.md)、[安全说明](docs/SECURITY.md)、[免责声明](DISCLAIMER.md)。
