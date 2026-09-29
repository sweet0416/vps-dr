# 发布门槛

当前状态：`DR_RELEASE_READY=NO`。`v1.0.0` tag/release 尚不存在；README 中的固定 tag 命令只是验证通过后的预定入口。

所有条件必须同时通过，才允许 `DR_RELEASE_READY=YES`：

| 条件 | 当前状态 | 证据要求 |
| --- | --- | --- |
| STATIC_VALIDATION | PASS（本机可执行项） | Shell 语法、Python 编译/单元测试、生成 JSON 往返、diff 与本地路径检查；无仓库 JSON 文件；shellcheck 未安装 |
| REAL_VPS_VALIDATION | NOT_VERIFIED | 临时 LightNode VPS 安装与健康报告 |
| SHADOWROCKET_TEST | NOT_VERIFIED | 临时 IP 节点真实导入及连接 |
| PASSWALL_TEST | NOT_VERIFIED | 临时 IP 节点真实连接 |
| IDEMPOTENCY_TEST | NOT_VERIFIED | 新装、重跑、失败恢复与冲突场景 |
| SECRETS_COMMITTED | NO（模式扫描） | 当前树与两笔历史提交的 UUID、私钥、令牌模式扫描；真实 VPS 秘密未输入仓库 |
| PRERELEASE_DEPENDENCIES | YES | 必须换成官方支持的稳定组合并核实为 NO |

发布顺序：解决稳定版本兼容阻断 → 静态验证 → 在**临时** VPS 上按 `REAL_VPS_TEST_PLAN.md` 完成全部测试 → 把 `VERSION` 更新为 `1.0.0` 并固定组件与 commit → 审核最终 diff/秘密 → 创建且不移动 `v1.0.0` tag → 记录 tag commit SHA → 验证固定 tag 启动入口。任何一个条件未通过，停止发布。

主分支是开发入口，会改变。固定 tag 是灾备入口。不得把 `main`、`latest` 或预发布版本作为默认灾备依赖。
