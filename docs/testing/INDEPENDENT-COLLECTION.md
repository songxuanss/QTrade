# 独立采集验收记录

变更：`extract-independent-data-collection`。未结束验收前不声明所有主干完成。

## 契约和真实任务库

- 便携契约/归档/来源 fixture/HTTP 解码/日志测试：`pytest tests/test_independent_collection.py`，23 项通过。
- 独立 PostgreSQL 17 `collector_test` 数据库：`pytest tests/test_independent_collection_postgres.py`，5 项通过。数据库由验收启动器在独立采集容器网络创建，通过私有环境变量提供连接，未接入生产任务数据。
- 验证七天分段、补缺/全量、预览竞争、幂等请求、来源/作用域隔离、租约 fencing、归档后中断和新连接恢复、无数据与成功区分、撤权拒绝、限流退避、取消和重试。
- HTTP 解码回归专门覆盖 JSON UUID/日期/tuple 被 FastAPI 预解析后仍严格接受合法请求、拒绝版本/额外字段。

## 购买 Pro 后真实供应商样本

使用私有配置，响应样本仅存 Git 忽略 `.tools`，不保存带证书请求 URL。

| 接口类别 | 标的 / 范围 | 真实结果 | 限制 |
| --- | --- | --- | --- |
| Pro 1 分钟历史 | 600519.SH / 2025-09-01 / limit 5 | HTTP 200、5 条 | 只证明样本权限与记录存在 |
| Pro 5 分钟年片 | 600519.SH / 2025 | HTTP 200、11,664 条 | 未证明全部市场/全部年份覆盖 |
| 因子目录 | 当前目录 | HTTP 200、code 200，85 项实际 data 数组 | 目录动态保存摘要，单位/计算版本缺失保留 unknown |
| pe_ttm 历史 | 600519 / 2025-09-01 至 05 | HTTP 200、code 200，5 条 | 不证明财务值准确性和 PIT |

来源返回实际形态与文档中的分页包装可能不同，Adapter 同时接受 data 数组和 data.list；fixture 覆盖年片缓存、裁剪、区间结束标签转换、手→股、空因子、401/403、429 和服务失败。北向三项接口未开放，仅显示目录及不可用说明，不伪造结果。

## 端到端

UI 验收脚本：`apps/web/tests/verification-independent-collector.mjs`。必须达到 UI 创建→独立任务库→Worker→真实来源→非空 Parquet/manifest→HTTP 分页→市场数据/metadata→图表或因子浏览→刷新，才标实时集成完成。

部署验收和结果待本次运行写入；第二台物理机器、其他供应商、PIT、逐笔全历史、实盘订单未验收。

### 2026-10-11 实际部署结果

原生 QTrade 验证入口 <http://127.0.0.1:3120/workspace/data>，独立采集接口本机 18100，独立 Postgres/归档卷和后台消费者运行。QTrade API/Worker 使用远程 HTTP，采集回查 3122 当前角色权限。主 Compose 消费镜像成功构建并验证 HTTP Adapter 导入；主 Compose 实例未连入这套验证作用域，不混用业务库身份。

真实 UI 端到端八组检查通过：

| 流程 | 集成与证据 | 结果 |
| --- | --- | --- |
| 600519.SH 贵州茅台 / Pro 5m / 北京时间 2025-09-01 至 10 | 10 天拆两段，原始和标准记录各 240+144=384；真实来源、独立任务库/Worker、Parquet、HTTP、市场导入、带简称图表、刷新；图表 API 的 Bar dataset_id 对应本次产物 | 通过 |
| 重复与补缺 | 相同范围显示新增 0，扩展到 09-12 只新增 1 段；PG 验证全量重采保留旧版本 | 通过 |
| pe_ttm / 2025-09-01 至 05 | 当前 85 项来源目录，真实 5 点、不可变清单、独立因子浏览、刷新，来源版本 unknown 保留 | 通过；未证明金融值准确/PIT |
| 已保存股票池两成员 | 贵州茅台与农业银行各 48 条，共 96，所有成员实际下载并导入，名称与刷新保留 | 通过 |
| 桌面/手机 | 1440/390 宽布局、无页面横向溢出、axe 无问题，修复滚动区键盘焦点 | 通过 |
| 拒绝与只读 | 未支持美股在 UI/API 拒绝、不建任务；CSRF、匿名、未知作用域拒绝；第二个真实身份为风险管理员，手机只读隐藏表单、API 写入拒绝 | 通过 |
| 受控真实停服 | 停止 collector 后 UI 明确未连接，不能创建新独立任务，旧采集任务仍可查看 | 通过 |
| 重启及持久化 | 重启保留 DB/卷，公开分页读取五个非空产物，原始 SHA/数量及标准 SHA/数量校验；四个行情产物实际 Timescale 240/144/48/48，QTrade metadata visible | 通过 |
| 来源失败/租约恢复 | 403、429、错误结构使用来源 fixture；真实独立 PG 检查退避/撤权/无记录/归档后恢复，消费 fixture 检查部分写入失败后重试 | 通过；不冒充真实供应商故障演练 |

机器数说明：独立容器网络、数据库、卷和进程已经验收；另一台物理机器尚未提供，不能声明跨物理机器验收完成。当前 Pro 1m 仅实际样本权限检查，完整 UI 样本为 5m；普通1d、其他分钟周期、其他年份/全市场不因本例自动获得覆盖保证。实时行情、全历史 Tick、其他供应商、财务 PIT、正式回测、Paper 完整链路、Live 订单均不在本次成功声明中。

运行证据在 Git 忽略 `.tools/verification-local/review/independent-collector-*.json/png`。身份与连接参数只读私有文件，不写报告；测试连接显示被修正为脱敏，轮换了受影响的测试数据库角色密码，最终运行日志秘密扫描通过。

### 回归与工具结果

- 全量 Python：757 passed、84 skipped（包括默认未配置的外部集成；不能算通过）。另行真实 collector PG 5 passed、主业务 Postgres/对象存储集成 65 passed。
- 消费边界 10 项：分页、范围/身份/周期、总数/摘要、幂等、候选半导入不可见、失败后重试。
- Ruff、mypy（235 文件）、架构检查、Bandit 中高危阈值通过；Python pip-audit 无已知漏洞。
- Web lint/format/typecheck、48 单元测试、生产构建通过；基础 UI 7 项通过。认证 UI 11 项通过（旧 RuntimeStore 替身 audit 参数已对齐）；这些使用 fixture，不证明交易就绪。
- 全量 npm audit high 阈值仍失败：已有 braces→micromatch→fast-glob→Next ESLint 的开发依赖告警；生产依赖 high 阈值通过，DOMPurify/Monaco 仍有两个 low 告警。没有自动降级 Next/Monaco 或宣称已解决；本次未变更 npm 依赖。
- 可选全库 Python format 检查仍发现原有未格式化文件；本轮新增代码已格式化，CI 要求的 Ruff lint 通过。
