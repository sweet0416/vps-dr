# Xray 发行资产审计（2026-09-30）

官方 [GitHub releases/latest API](https://api.github.com/repos/XTLS/Xray-core/releases/latest) 返回 `v26.3.27`，`prerelease=false`、`draft=false`。该版本的 [release 页面](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27) 提供 Linux amd64 与 arm64 ZIP 及各自 `.dgst`。仓库仅下载 `XTLS/Xray-core` 对应固定 tag 的资产，不访问浮动 `latest` 路径。

| 架构 | 官方资产 | 固定 SHA-256 |
| --- | --- | --- |
| amd64 | `Xray-linux-64.zip` | `23cd9af937744d97776ee35ecad4972cf4b2109d1e0fe6be9930467608f7c8ae` |
| arm64 | `Xray-linux-arm64-v8a.zip` | `4d30283ae614e3057f730f67cd088a42be6fdf91f8639d82cb69e48cde80413c` |

本次从官方 API 核对资产摘要，并在本地下载两份 Linux ZIP 重新计算 SHA-256；两份对应 `.dgst` 的 `SHA2-256` 也一致。安装脚本要求实际 ZIP SHA-256、`.dgst` SHA2-256 和仓库固定值三者相等才解压，并与从这些 ZIP 计算的固定二进制 SHA-256 比对；预检也用固定二进制摘要识别已有安装。ZIP 内的 `xray` 路径亦在本地核对。Windows 同版本官方二进制通过仓库生成的 VMess JSON 的 `run -test -config`；Linux/systemd 和真实网络连接尚未验证。

VMess JSON 结构参考 [Project X VMess inbound 文档](https://xtls.github.io/en/config/inbounds/vmess.html) 与 [传输配置文档](https://xtls.github.io/config/transports/raw.html)。客户端 URI 使用 [v2rayN 的 VMess 分享链接字段说明](https://github.com/2dust/v2rayN/wiki/Description-of-VMess-share-link)所记录的 `vmess://Base64(JSON)`、`aid=0`、`scy=auto`、`net=tcp`、`type=none`、`tls=none`，并做编码/解码/字段比对。Shadowrocket 与 PassWall 是否接受并成功连接仍为 `NOT_VERIFIED`。
