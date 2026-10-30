# 数据采集主入口访问修复（2026-10-12）

整体 Goal 未完成；本文仅证明下面列出的访问和既有采集链路。新增自动增量、质量门禁、新鲜度、用量控制及扩展类别仍待实现/验收。

## 复现与根因

之前公布的用户入口为 `http://127.0.0.1:13000/workspace/data`，部署审计时工作树提交为 `962fe36`；运行 Web 镜像为 `sha256:d8309e115bacc4714a888ec9594d45aa8f3ebe0be5180697664dacd85e19a256`，镜像 revision 标签为 unknown，不能仅由工作树断言镜像提交。主 Compose Web 未传入任何 OIDC 设置，`/auth/login` 实测 HTTP 503，数据页 HTTP 200 但仅渲染权限占位；不是授权用户数据采集工作台。主 18000 API 的 collector 地址也为空。另一个 sibling SSO 容器处于重启状态，但不是已验收 3121 SSO 的提供方，不能将其健康状态混为当前认证链路。

3120 的既有生产构建、3121 SSO、3122 API、3123 Worker、独立 18100 collector 与持久业务库均仍在运行。旧文档明确默认 Compose 未连接 SSO，上一阶段将 13000 健康检查描述为交付入口可用的结论过强。

## 修复与实际部署拓扑

Compose 显式传入身份配置。本机 `compose.local-auth.yaml` 让 Web 使用 host 网络访问现有 loopback SSO/API；配置脚本只扩展已有 SSO 客户端的本机回调/登出回跳白名单，保留已有地址，将秘密写入 0600、Git 忽略的 `.env.local-auth`。没有移库、删数据、创建替代身份、绕过权限或启用实盘。

链路：13000 Docker Web → 3122 认证 API → 既有隔离持久业务 PostgreSQL / Timescale / MinIO → 18100 独立 Docker collector → 独立 PostgreSQL / Parquet 卷 / 后台采集进程。3123 QTrade Worker 通过公开 HTTP 导入标准行情。身份来自真实 sibling SSO 3121。主 Compose 18000 API 不是本链路的认证 API，不声称它已接通。

## 执行证据

| 验证 | 类型 | 结果 |
| --- | --- | --- |
| `QTRADE_VERIFICATION_ORIGIN=http://127.0.0.1:13000 node tests/verification-independent-collector.mjs` | 实际 Docker Web、真实 SSO/API/DB/Worker/来源 | 8 组通过：10 天 5m 行情 384 条；pe_ttm 历史 5 点；已保存池两成员 96 条；非空原始和标准归档、导入、图表身份/数据集对应、因子浏览、刷新 |
| 同脚本的重复/补缺、拒绝、角色与 UI 检查 | 真实作用域与权限、桌面/手机 | 补缺预览、CSRF/跨 scope/未知资产拒绝、风险管理员不能写；1440/390 宽无溢出、axe 无问题 |
| `node tests/verification-collector-access.mjs` | 真实导航及数据库会话过期 | 3 组通过：桌面/手机主导航、直接访问、刷新；只将本次测试会话在实际 DB 设过期，API 401 后通过 SSO 重认证返回数据页，产生新会话，API 恢复 200 |
| 同脚本 `--unavailable-check`，实际停止 collector 后运行，再恢复容器 | 受控真实采集服务故障 | 工作台可打开、明确未连接、无新独立任务表单、既有兼容任务可读；不等同上游供应商故障验收 |
| `PYTHONPATH=packages/data-contracts/src:packages/data-core/src:adapters/data_http/src .venv/bin/python scripts/verification-independent-collection.py`，在上述重启后运行 | 当前 13000 本轮创建的 5 个产物 | 真实独立 PG/HTTP/Parquet 原始和标准摘要、非空数量再次验证；实际行情库与 QTrade 导入状态/数量匹配；采集日志秘密扫描通过 |

本次行情使用供应商真实历史响应及已验证年度缓存；采集仍生成新的不可变产物。因子重新请求真实来源。数据只证明列出的标的/周期/范围，不证明全年、全市场或 PIT。证据文件在 Git 忽略 `.tools/verification-local/review/independent-collector-results.json`、`collector-access-results.json`，无秘密。

剩余访问验收：完整来源故障/空数据/无配置状态矩阵将随后续能力实现补充；上述服务停止不能替代这些案例。后端与独立采集的第二台物理机器尚未验收。
