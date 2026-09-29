# 安全边界

- **只在全新 VPS 使用。** 安装会运行官方 3x-ui 安装器、修改新机 UFW 和 systemd；不触碰现有生产 VPS。
- UUID、面板密码和 API token 保存在新 VPS root-only 文件中，不在仓库。终端导出的 `vmess://` 含 UUID，禁止粘贴到公开 issue 或日志。
- 3x-ui 面板 HTTP 只监听 `127.0.0.1`，只能通过 SSH 隧道访问。安装过程中先放行 SSH/443 并启用 UFW，再启动面板。
- SSH 端口、root 登录和密码登录配置不变。上线后可另行加固 SSH，但先确认替代登录方法。
- VMess TCP/443 **没有 TLS**，是为了与原客户端节点兼容；它不是抗审查或加密传输的长期升级方案。
- GitHub raw `curl | bash` 依赖 GitHub 和仓库主分支可信；生产演练时可先下载并检查脚本，再运行。上游 3x-ui 安装器固定到 v3.8.5 commit，release 资产由官方脚本核对 SHA-256。
- 服务商安全组和 VPS 出站连通性不由 UFW 控制；必须在服务商控制台检查。
