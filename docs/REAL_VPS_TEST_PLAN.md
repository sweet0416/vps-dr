# 临时 LightNode VPS 测试计划

状态：`REAL_VPS_VALIDATION=PASS`（V1 必需范围）。用户创建的临时 LightNode `38.60.248.15` 已使用提交 `3bdc0719d3965824f2a02eea3c6757eda5a0f2d9` 完成初次安装、重启后健康检查和同配置重跑；随后仅将工具文件更新至 `ef5cb95e4522367a88387ec801a74ac6a144d1c7`，再次得到 `ALREADY_CONFIGURED`、`QR_CODE_DISPLAY: PASS`、运行文件校验值不变、Xray active 和 443 正常。用户确认 Shadowrocket 自动二维码导入及实际连通性正常。`PRIMARY_CLIENT_VALIDATION=Shadowrocket`；`PASSWALL_TEST=NOT_TESTED`，兼容性未实机验证，不属于 V1 发布阻塞。全程保持 `node.passwallv2ray.top → 38.54.95.213`；不访问生产 VPS、不修改 Cloudflare。

1. 新开一台全新 Ubuntu 24.04 LTS LightNode VPS，记录 `NEW_VPS_IP`；在服务商防火墙允许 SSH 和 TCP 443。
2. 获取固定待测 commit 的仓库文件并审阅；运行 `sudo ./bootstrap.sh --preflight`，记录脱敏输出。要求 `READY_TO_INSTALL: YES`，未知 443 owner 时停止。
3. 使用**测试 UUID 或旧 UUID**运行 `sudo ./bootstrap.sh`。UUID 在私密终端输入，勿贴到公开日志或仓库。无 UUID 时可生成，但旧客户端不能直接复用。
4. 运行 `sudo /opt/vps-dr/health-check.sh`；确认 Xray 版本、JSON、`xray run -test`、systemd active、443 属于 Xray、UUID 匹配、localhost TCP、公共 IP 和 DNS 检查。
5. 在外部网络确认 `NEW_VPS_IP:443` 公网可达；同时核查服务商防火墙和已有 UFW 规则。
6. 对含自动二维码的新提交，确认交互式 bootstrap 结束时显示二维码，地址为 `NEW_VPS_IP` 且 Shadowrocket 可扫描。也可运行 `sudo /opt/vps-dr/export-client.sh --server NEW_VPS_IP` 核对字段，并在 Shadowrocket **新增临时节点**真实连接。核对 VMess/TCP/443/AlterID 0/AEAD/auto/TLS off；不要覆盖旧节点。
7. 可选：在 PassWall 新增同参数的临时 IP 节点并真实连接；不要修改旧节点。V1 本次未做该项，记录为 `NOT_TESTED`。
8. 用同一 UUID 再次运行 bootstrap，期望 `ALREADY_CONFIGURED`；另在隔离测试场景核实不同 UUID、未知 Xray、未知 443 占用均停止。中断安装后仅补齐缺失的本项目文件，不覆盖不一致文件。
9. 若是专用测试机，可运行 `sudo /opt/vps-dr/uninstall.sh` 验证停用与保留配置，并按需复装；不得在生产机测试卸载。
10. 记录 `REAL_VPS_VALIDATION`、`SHADOWROCKET_TEST`、`QR_IMPORT_TEST`、`PASSWALL_TEST`、幂等与重启结果。V1 已记录的验收结果与范围见 [发布验证结果](RELEASE_GATE.md)；V1 灾难恢复使用固定 tag `v1.0.0`。

测试期间生产 DNS 不变。`xray run -test`、localhost TCP 和公网 TCP 都不能替代客户端真实连接。
