# 临时 LightNode VPS 测试计划

状态：`NOT_VERIFIED`。本文件是下一阶段手动创建**临时** VPS 后的测试步骤；当前阶段不创建 VPS、不部署、不访问生产 VPS、不修改 Cloudflare。

## 先决条件

先解除 `XRAY_STABLE_COMPATIBILITY_BLOCKED`，固定官方支持的稳定非预发布 3x-ui/Xray 组合，并通过静态检查。确认客户端有可用测试 UUID，保存于密码管理器；不要放进命令历史、仓库或公开日志。确认现有 `node.passwallv2ray.top → 38.54.95.213` 保持不变。

## 顺序

1. 人工创建全新临时 LightNode VPS，记录 `NEW_VPS_IP`，仅开放 SSH 和 443/tcp。
2. 下载固定待测 commit 的源文件并检查，再运行 `sudo bash install.sh --preflight`；保存**脱敏**报告，要求 `READY_TO_INSTALL: YES`。443 若被未知程序监听，确认 `PORT_443_CONFLICT` 并停止，不结束进程。
3. 使用**测试 UUID** 安装，禁止更改生产 DNS。检查 `VPS_DR_VERSION`、source commit 和安装日志权限。不得在公开记录中写 UUID、面板密码或 API token。
4. 运行健康检查：3x-ui systemd active、面板仅 localhost、面板管理的 Xray running、443 listening、VMess inbound 与 UUID 正确、生成的 Xray JSON 校验通过、localhost TCP 443 可连。
5. 从外部网络测试 `NEW_VPS_IP:443` 的公网 TCP 可达；检查服务商防火墙。
6. 在 Shadowrocket 新建**临时** VMess 节点，Address=`NEW_VPS_IP`、Port=443、UUID=测试 UUID、AlterID=0、AEAD、Encryption=auto、TCP/none、TLS=off、UDP=on（如支持）；真实连接并记录结果。另行测试官方导出 `vmess://` 在当前 Shadowrocket 版本的导入结果；它的域名字段用于灾备，测试时不要覆盖旧节点。
7. 在 PassWall 新建同样的临时 IP 节点，真实连接并记录结果。不要编辑现有生产节点。
8. 进行幂等矩阵：首次安装、成功后第二次运行（`ALREADY_CONFIGURED`）、安装中断后重试或明确 STOP、预存 3x-ui、未知 443 占用、相同/不同 UUID inbound、已启用 UFW、已运行 systemd；核实无未知配置被覆盖。
9. 上述全部通过后记录 `REAL_VPS_VALIDATION=PASS`、`SHADOWROCKET_TEST=PASS`、`PASSWALL_TEST=PASS`、`IDEMPOTENCY_TEST=PASS`，并复核秘密和预发布依赖。失败项保持 FAIL/NOT_VERIFIED。
10. 只有 [发布门槛](RELEASE_GATE.md) 全部通过，才考虑创建 `v1.0.0`。测试完成可人工销毁临时 VPS；生产 DNS 始终保持 `node.passwallv2ray.top → 38.54.95.213`。

记录时保留版本、commit、脱敏健康报告、客户端版本、测试时间和结果。`xray run -test`、localhost TCP 与公网 TCP 不能代替真实客户端连通性。
