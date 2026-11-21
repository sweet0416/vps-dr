# VPS DR 中文安装与管理

在 VPS 的 **root SSH 终端**执行，一条命令进入中文菜单：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/main/bootstrap.sh | bash
```

输入数字并回车即可操作。每次操作后返回菜单，失败时显示原因和退出码。菜单从 `/dev/tty` 读取选择，支持上方 curl 管道入口。

| 数字 | 菜单操作 | 用途 |
| --- | --- | --- |
| 1 | 安装 / 继续安装 | 首次创建节点，或补齐中断安装 |
| 2 | 节点链接与二维码 | 查看当前节点的导入链接和二维码 |
| 3 | 检查运行状态 | 检查配置、服务、进程和本机监听端口 |
| 4 | 重启节点服务 | 预检通过后重启 Xray 并检查状态 |
| 5 | 升级管理脚本 | 将当前下载的管理脚本部署到本机 |
| 6 | 一键开启 BBR | 检查内核支持，启用并保存开机配置 |
| 0 | 退出 | 退出菜单，保留上次操作的退出码 |

**首次使用选 1，已有节点更新菜单选 5，导入客户端选 2，开启 BBR 选 6。**

安装或升级成功后，输入 **`caidan`** 即可再次打开本机菜单。需要获取仓库的新代码时，使用上方在线入口并选择 **5**。

## 菜单面板与状态

菜单顶部显示本机节点状态、`VMess / TCP 443` 协议信息和 BBR 状态。状态在每次返回菜单时刷新。

| 面板状态 | 含义 |
| --- | --- |
| 未安装 | 本机尚无 `/etc/vps-dr/state.json` 状态文件 |
| 服务运行中（绿色） | 已有节点状态文件，且 systemd 报告 Xray 服务处于 active 状态 |
| 已安装 / 服务未运行 | 已有节点状态文件，Xray 服务当前未处于 active 状态 |
| 服务状态不可用 | 已有节点状态文件，当前环境缺少 systemctl 命令 |

选择 **3** 执行完整本机检查。客户端连接效果需要在客户端实际测试。

面板采用边框、青色编号和对齐列表。字体、字号和终端背景色由 SSH 客户端设置，例如 Xshell 的会话外观设置。设置非空的 `NO_COLOR` 可关闭颜色与粗体，简易终端和重定向输出自动使用无 ANSI 样式的文本。

首次安装默认生成新 UUID，导出当前 VPS 的公网 IPv4。再次执行复用本机 /etc/vps-dr/state.json 中的 UUID，补齐中断安装；不会自动轮换 UUID。域名不再是安装前提。

## 已有节点

已有节点，包括旧版和仅手工修复过 DOMAIN 的已知版本，执行上方同一命令，然后选择 **5**。它只升级 /opt/vps-dr 管理文件，保留 UUID、Xray 二进制、配置、systemd 服务和防火墙，不重启服务。升级前核验本机状态、官方二进制摘要、配置与 unit；遇到未知内容会解释原因并停止。

升级成功后，在 VPS 的 root 终端输入 `caidan` 即可打开本机中文菜单。也可使用 `bash /opt/vps-dr/menu.sh`。需要获取后续新代码时，重新执行上方在线入口。选择 1 会完成安装／续装，可能启动尚未启动的服务并补齐必要文件。

## 固定版本与无人值守

默认推荐使用本文开头的 **main 在线入口**，获取最新面板和修复。

`VERSION` 当前为 `1.1.0`，main 已包含正式 `v1.1.0` 发布之后的面板改进和 BBR 功能。固定 `v1.1.0` 标签提供当时的中文菜单快照。需要带边框、实时服务状态和 BBR 操作的新版面板，请使用 main 入口。

使用固定发布快照时，同时指定下载 ref：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/v1.1.0/bootstrap.sh | VPS_DR_REF=v1.1.0 bash
```

历史 v1.0.0 tag 保持原样，不包含本次修复。main 获取最新代码；固定 tag 获取对应快照。旧 state 中的 deployment_version 记录最初安装版本，管理版本以 /opt/vps-dr/VERSION 为准；接受 0.1.0-dev、1.0.0 与当前版本的 direct-xray-v1 状态，仍严格检查 Xray 版本和 UUID。

无交互终端不会等待菜单输入。自动安装入口：

```bash
curl -fsSL https://raw.githubusercontent.com/sweet0416/vps-dr/main/bootstrap.sh | bash -s -- --install
```

将末尾 --install 换成 --preflight 或 --dry-run 可只做预检；换成 --upgrade-management 可只升级管理脚本。重定向安装输出时不会自动打印 UUID 或节点链接。

## BBR 状态与一键开启

顶部 BBR 状态来自本机 `net.ipv4.tcp_congestion_control`。绿色“已开启”表示当前默认算法为 `bbr`，其余算法显示“未开启（算法名）”。读取失败显示“状态不可用”。每次返回菜单时刷新。

已有节点通过本文开头的在线入口选择 **5**，保存最新管理脚本，再选择 **6**。脚本检查内核提供的算法，必要时加载当前内核自带的 `tcp_bbr` 模块。启用后回读验证，再将配置保存到 `/etc/sysctl.d/99-vps-dr-bbr.conf` 和 `/etc/modules-load.d/vps-dr-bbr.conf`。再次执行可核对并补齐本项目配置。

该设置影响本机后续新建 TCP 连接。已有连接的算法由内核和应用决定，实际速度取决于网络条件。队列规则沿用本机配置。此功能使用当前内核的 BBR 支持。

| 结果或错误码 | 说明与处理 |
| --- | --- |
| BBR_ENABLED | 当前内核已报告 BBR 生效，开机加载配置已保存 |
| BBR_UNSUPPORTED | 内核无法提供或加载 BBR，请检查内核模块及 VPS 虚拟化限制 |
| BBR_CONFIG_CONFLICT | 按提示核对会覆盖 BBR 的已有设置，或本项目文件的未知内容及权限 |
| BBR_COMMAND_FAILED / BBR_VERIFY_FAILED | 执行失败或回读不符，请检查系统权限与内核限制 |
| BBR_ROLLBACK_FAILED | 恢复失败，请按提示核对系统参数或配置文件 |
| INSTALL_IN_PROGRESS | 其他管理操作占用共享锁，等待完成后重试 |

保存失败时，会尝试删除本次新建的配置并恢复原 TCP 算法。恢复失败会单独报告。安装、升级、重启和开启 BBR 共用操作锁。

## 地址和二维码

自动查询公网 IPv4 失败时，菜单 2 会要求输入地址。也可显式提供 VPS_DR_SERVER（IPv4 或完整域名），或 VPS_DR_DOMAIN（可选域名）。环境变量需传给管道右侧的 bash。域名应解析到当前 VPS；查询失败只提示，不将本机 Xray 误判为故障。

已安装节点可使用 `bash /opt/vps-dr/export-client.sh --server example.com --qr` 显式导出域名，或不带 --server 自动导出 IP。会先核对配置 UUID 与 state。链接、二维码、手动参数使用同一个配置。qrencode 缺失时菜单尝试从系统包仓库安装；安装或显示失败时仍可复制 vmess:// 链接。

UUID、链接和二维码都是客户端密钥，请只在私密终端查看，不公开截图，不写入 CI 日志。预检、状态检查不输出 UUID。

## 安装保护与恢复

架构保持 **Xray v26.3.27 + VMess TCP/443 + systemd**。固定官方 ZIP、官方 .dgst 与二进制 SHA-256 校验保留；配置在启动前和 systemd ExecStartPre 中校验。安装／升级／重启共享安装锁。

识别 v1.0.0、v1.1.0、此前已发布的菜单脚本和仅修改 DOMAIN 赋值的已知脚本。遇到其他未知修改时提示核对并保留原文件。已知脚本缺失可以补齐。安装管理脚本先于启动和健康检查，避免服务已启动却没有管理入口。

管理目录升级前复制到同盘暂存目录，再将旧目录保存为 /opt/.vps-dr.backup-*。交换失败会恢复旧目录；进程在交换间被终止时，下次安装或升级会恢复 /opt/.vps-dr.previous。备份只包含管理脚本，不包含节点密钥。回滚时将下面的 BACKUP_PATH 替换为升级输出的具体备份目录：

```bash
flock /run/lock/vps-dr.lock python3 /opt/vps-dr/scripts/manage.py rollback BACKUP_PATH /opt/vps-dr
```

回滚不操作 Xray 服务和节点配置；回到没有 menu.sh 的旧版后，可重新使用在线入口。

支持预检 Ubuntu 22.04/24.04/26.04、Debian 12/13，amd64/arm64；需要 systemd、至少 1 GiB 可用磁盘和 256 MiB 可用内存。未知二进制、配置、unit、覆盖片段、端口占用或不安全路径会阻止安装／重启。仅在现有 UFW 已激活且缺少规则时放行 TCP 443；服务商安全组仍需自行放行。

本机健康检查通过仅表示配置、服务、进程归属和本机端口正常。**客户端实际连通性仍需用 Shadowrocket 等客户端测试**。此次修复不使用现有生产 VPS 做重装测试。

## 验证与文档

当前面板版本通过 **43 项 Linux 隔离测试**，覆盖菜单分支、管道终端、手工地址、二维码工具失败、错误返回、无终端、颜色回退、边框对齐、服务状态变化、BBR 状态、内核支持、保存失败回滚、并发锁及 caidan 命令部署。服务操作均使用临时测试脚本，BBR 测试模拟内核接口并使用临时配置目录。GitHub Actions 的 Bash 语法与空白检查通过。

本地执行 `python3 -m unittest discover -s tests -v`。菜单与管理目录权限测试需要 Linux root 环境，可使用隔离容器或用户命名空间运行。

此次更新的完整新 VPS 安装、真实 systemd 服务操作、BBR 在生产 VPS 上的开启与重启后持久化效果、客户端端到端连通性尚未实机验证。

[面板改进 PR](https://github.com/sweet0416/vps-dr/pull/3) · [合并后的检查结果](https://github.com/sweet0416/vps-dr/actions/runs/44901580177)

本次验证和限制见 [1.1.0 验证记录](docs/VALIDATION_1_1.md)。[原版验收记录](docs/RELEASE_GATE.md)只适用于历史 v1.0.0，不能作为本次实机验证证据。[Xray 固定资产审计](docs/XRAY_RELEASE_AUDIT.md)、[安全说明](docs/SECURITY.md)、[免责声明](DISCLAIMER.md)。
