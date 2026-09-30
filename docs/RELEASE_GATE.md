# V1 发布门槛

`PRIMARY_CLIENT_VALIDATION=Shadowrocket`。V1 的真实客户端验收基准是 Shadowrocket；PassWall 为可选项，`PASSWALL_TEST=NOT_TESTED`。PassWall 实际连接兼容性未在 V1 实机验证，这是范围限制，不是发布阻塞。

| 必须同时满足 | 当前状态 | 证据 |
| --- | --- | --- |
| STATIC_VALIDATION | PASS | Bash 语法、Python、JSON/配置、单元测试、差异、文档链接与静态路径检查 |
| REAL_VPS_SERVER_VALIDATION | PASS | 临时 LightNode `38.60.248.15`：Ubuntu 24.04 amd64 安装、Xray v26.3.27 配置测试、systemd active、443 由 Xray 监听、健康检查通过 |
| SHADOWROCKET_TEST | PASS | 用户确认临时 IP 节点实际连通性正常 |
| QR_IMPORT_TEST | PASS | 用户确认自动二维码可见、Shadowrocket 扫码导入正常；二维码与 URI 使用同一生成逻辑 |
| IDEMPOTENCY_TEST | PASS | 实机同配置重跑 `ALREADY_CONFIGURED`，state/config、Xray 二进制和 systemd unit 校验值不变，服务和 443 正常 |
| REBOOT_PERSISTENCE | PASS | 实机重启前后 boot ID 不同；重启后服务 active、自动启动 enabled、443 属于 Xray，错误日志无条目 |
| SECRETS_COMMITTED | NO | 当前树、差异和当前仓库 Git 历史的已知秘密模式扫描；UUID 仅在 VPS 的 root-only 文件中 |
| PRERELEASE_DEPENDENCIES | NO | Xray 固定官方稳定版 v26.3.27，ZIP、官方 `.dgst` 和固定 SHA-256 校验 |

补充静态证据：`XRAY_CONFIG_TEST=PASS`、`VMESS_URI_ROUNDTRIP_TEST=PASS`、`SECRET_SCAN=PASS`。实机工具更新至 `ef5cb95e4522367a88387ec801a74ac6a144d1c7` 后，预检通过；交互式重跑返回 `ALREADY_CONFIGURED` 和 `QR_CODE_DISPLAY: PASS`，运行文件未变。用户随后确认扫码导入与连通性。初次安装使用 `3bdc0719d3965824f2a02eea3c6757eda5a0f2d9`。后续候选提交只更新文档、`VERSION` 和随版本更新的测试数据；测试机仍报告原部署版本 `0.1.0-dev`，不应把它写成已运行 `1.0.0`。

`REAL_VPS_VALIDATION=PASS`。上述必需门槛全部成立时，`DR_RELEASE_READY=YES` 表示技术验收完成；**创建 tag/GitHub Release 仍须人工批准**。`v1.0.0` 尚未创建。候选版本文件为 `1.0.0`；批准后应在候选提交上创建 `v1.0.0` tag，记录并核对 tag commit SHA，禁止移动或重建。Git tag 名称本身可被移动，因此其不可变性依赖仓库保护策略和保存的 commit SHA；创建前固定 tag 灾备命令不可用。`main` 是开发入口，固定 tag 才是灾备入口。不得把未验证的其他 OS/架构或 PassWall 连接写成 PASS。
