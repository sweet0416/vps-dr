# 灾难恢复（仅在 V1 放行后执行）

本页记录 V1 灾难当天的人工恢复顺序。V1 使用固定版本 tag `v1.0.0`，它对应冻结的恢复快照；灾难恢复应使用这个固定版本，而不是可变的 `main`。实机证据与范围见 [临时 VPS 演练](REAL_VPS_TEST_PLAN.md) 和 [发布验证结果](RELEASE_GATE.md)。

1. 确认旧节点确实故障。不要自动关闭或重置原 VPS。
2. 新建全新 VPS，确认服务商防火墙允许 SSH 与 TCP 443。
3. 安全取得原 Shadowrocket / PassWall 节点 UUID，避免新 UUID 导致旧客户端不匹配。
4. 在新 VPS 下载**已验证的固定 tag**源码，先运行 `sudo ./bootstrap.sh --preflight`；要求 `READY_TO_INSTALL: YES`。
5. 输入原 UUID，运行 `sudo ./bootstrap.sh`；核对健康报告与 Xray 版本。
6. 先用 `sudo /opt/vps-dr/export-client.sh --server NEW_VPS_IP` 创建临时客户端节点，在 Shadowrocket 做真实连接测试；PassWall 可选，其 V1 实机兼容性尚未验证。
7. 只有新 VPS 实连正常且人工决定切换后，才在 Cloudflare 把 `node.passwallv2ray.top` 的 A 记录指向 NEW_VPS_IP，保持 DNS Only / 灰云。
8. 等待 DNS 生效，再用原节点做真实连接测试；确认稳定后再决定旧 VPS 去留。

回退：若新 VPS 未验证通过，保持旧 DNS 不变；若已切换后失败，人工改回已确认可用的地址。本仓库不自动修改 DNS。
