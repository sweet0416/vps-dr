# V1 发布门槛

当前 `DR_RELEASE_READY=NO`；不得创建 `v1.0.0` tag/release。

| 必须同时满足 | 当前状态 | 说明 |
| --- | --- | --- |
| STATIC_VALIDATION | PASS（本机可做项） | Shell/Python、JSON、diff、静态路径和秘密扫描；Linux 主机行为另列 |
| XRAY_CONFIG_TEST | PASS（Windows 同版本） | 官方 Xray v26.3.27 解析生成的配置；Linux 运行待验证 |
| VMESS_URI_ROUNDTRIP_TEST | PASS | 默认域名与临时 IPv4 地址的 Base64 JSON 往返与字段核对 |
| SECRET_SCAN | PASS | 当前树、变更和 Git 历史的已知秘密模式扫描 |
| IDEMPOTENCY_LOCAL_TEST | PASS | state 复用、不同 UUID/配置冲突与旧状态拒绝 |
| REAL_VPS_VALIDATION | NOT_VERIFIED | 临时 VPS 安装、Linux 配置测试、systemd、443、公网 |
| SHADOWROCKET_TEST | NOT_VERIFIED | 临时 IP 节点真实导入与连接 |
| PASSWALL_TEST | NOT_VERIFIED | 临时 IP 节点真实连接 |
| PRERELEASE_DEPENDENCIES | NO | 固定官方非预发布 Xray v26.3.27 |

上述所有项有真实证据且均为 PASS/NO，才可标记 `DR_RELEASE_READY=YES`。随后更新 `VERSION` 为 `1.0.0`、审阅最终差异和秘密、创建不可移动的 `v1.0.0` tag，并记录 tag commit SHA。`main` 是开发入口，不是稳定灾备入口。不得把静态测试当成真实 VPS 或客户端验收。
