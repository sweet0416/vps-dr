# 3x-ui / Xray 版本审计（2026-09-29）

```
XRAY_VERSION_STRATEGY={
  MODE: 3x-ui-managed single Xray; fail closed until stable pairing exists
  VERSION: v26.3.27 stable target; v26.9.9 bundled by current 3x-ui, blocked
  SOURCE: XTLS official release + MHSanaei/3x-ui v3.8.5 release source
  PRERELEASE: NO for allowed installation (current bundle is YES, so no install)
  RATIONALE: v3.8.5 manages and validates its own core; its update API rejects v26.3.27
}
```

证据：

1. [3x-ui v3.8.5 release](https://github.com/MHSanaei/3x-ui/releases/tag/v3.8.5)；[该 tag 的 release workflow](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/.github/workflows/release.yml) 固定下载 Xray `v26.9.9`。
2. [3x-ui v3.8.5 的 ServerService](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/internal/web/service/server.go) 中，`GetXrayVersions` 过滤掉早于 `v26.6.27` 的版本；`UpdateXray` 只允许列表中的版本，停止面板管理的 Xray、下载官方资产、核对 SHA-256、替换同一二进制、重启。仓库不应再安装独立 Xray 服务。
3. [3x-ui v3.8.5 官方 API 文档](https://github.com/MHSanaei/3x-ui/blob/v3.8.5/docs/public/openapi.json) 提供 Xray 更新与 inbound 分享链接接口。此处的“支持”是接口规则，不等于真实 VPS/VMess 客户端兼容性已经通过。
4. [Xray v26.9.9](https://github.com/XTLS/Xray-core/releases/tag/v26.9.9) 标为 pre-release；[Xray v26.3.27](https://github.com/XTLS/Xray-core/releases/tag/v26.3.27) 是本次核查的最新非预发布 release。版本状态随上游发布变化，解除阻断前须重新核查。

结论：当前 `VERSION_XRAY=v26.3.27` 是稳定目标，`XRAY_BUNDLED=v26.9.9` 是上游实际打包版本。两者不一致且面板拒绝稳定目标，因此 `XRAY_STABLE_COMPATIBLE=NO`，预检报告 `READY_TO_INSTALL: NO`。不能用预发布版本“先试一下”，也不能手动替换面板管理的二进制。下一步重新审计官方稳定组合，再做临时 VPS 演练。
