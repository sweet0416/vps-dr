# 1.2.0 验证记录

验证日期为 2026-11-21。本次增加流量、诊断、连接与资源、私密备份恢复、BBR恢复、日志，以及管理脚本和官方Xray稳定版更新。

## 已完成

- `unshare -Ur python3 -m unittest discover -s tests -q`，138 项通过。Linux用户命名空间提供隔离root身份，文件均位于临时目录。
- 菜单全部0–14分支、无效输入、确认/取消、错误状态、无TTY、curl管道入口、颜色回退、52列边框和状态刷新。
- 真实执行临时路径改写的install/preflight及状态、配置、受信摘要和锁逻辑。覆盖首次/同机重跑UUID保留、二进制落地后的中断续装、旧1.1状态升级，以及新版核心复用而不降级。systemctl、ss和网络下载使用stub；health中的系统/套接字段替换为临时配置一致性校验。
- 流量默认网卡去重、UTC跨月、重启/计数归零、采样并发、原子写失败、不安全文件与未知单元保护。
- 备份权限/格式/校验和、UUID/架构/路径校验、保留当前核心版本、停止服务保持停止、恢复写入失败/启动失败回滚、state/config混合中断恢复、未知改动保护。
- BBR支持检测、首次原状态私密记录、重复开启保留快照、恢复/明确cubic、失败回滚、历史记录缺失及并发锁。内核接口使用模拟值，未改变主机sysctl。
- 核心官方API/ZIP摘要、未知二进制/unit保护、停止状态、健康失败/写失败回滚、回滚失败续做、清理完成事务时中断、并发锁。
- 安装锁拒绝预置链接、FIFO、硬链接、不安全权限和路径替换；真实子进程验证FD9继承与锁释放。
- 管理升级后本地模块导入不产生bytecode缓存，不阻塞下一次升级。未知管理文件保护保留。
- 从本次修改前已发布main（67ce5c0b4de0dc4a5680454f1389c4422020e62a）读取全部部署文件，在隔离root临时目录真实执行verify、upgrade、rollback，均通过。旧文件摘要纳入兼容清单。
- 所有Shell脚本Bash语法、`git diff --check`通过；流量service/timer通过`systemd-analyze verify`静态校验。

## 官方核心临时包验证

使用官方latest发布API及官方资产，实际下载x86_64 `v26.11.9`。ZIP通过`.dgst`及发布API SHA-256一致性校验，解压核心version正确，现有VMess TCP配置`run -test`通过。仅在临时目录运行，无系统安装或服务操作。

- ZIP SHA-256 `b651f7e1d9966ee2c1b1ee1bf8b52c72771824d3065dce96bbc8bcc19ed95f82`
- 核心 SHA-256 `e87bce586acdb9136aa5b1f1d9c8db9f1ea1d145b6d86368c7f6f662f9f6a66d`
- 官方来源 https://github.com/XTLS/Xray-core/releases/latest
- 官方安装器参考 https://github.com/XTLS/Xray-install/blob/main/install-release.sh

首次安装仍固定v26.3.27。菜单14是明确选择后的官方最新稳定版更新，后续latest可能变化。

## 未实机验证

- 全新VPS上的完整联网root安装、真实systemd启停/更新回滚、流量timer长期采集。
- 真实VPS内核BBR启用、恢复和重启持久化。
- ARM64最新核心资产的实际运行。
- 跨真实机器迁移恢复及外部VMess客户端端到端连接、YouTube速度。

本次未重装、重启或修改现有生产VPS，不将模拟测试等同于上述实机验证。本机检查通过仅说明受管配置、服务、进程和本机端口健康。
