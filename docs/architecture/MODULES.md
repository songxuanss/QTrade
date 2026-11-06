# QTrade 模块与领域设计

项目采用模块化单体，保留 Web、API、worker、realtime 和隔离 runner 的独立进程。
业务上下文决定模块划分；部署进程不拥有业务规则。目标是替换一个实现时，修改集中在
该模块、适配器与组装入口，调用方继续使用同一接口。

Strategy Runner 是独立的执行信任边界：worker 只通过权限受限的 Unix socket 发送任务；
runner 容器禁用网络、只读挂载根文件系统，源码在每次运行的低权限子进程中执行，并由
OS 限制 CPU、地址空间、文件大小、打开句柄、进程数、时长和输出。静态校验只作预检，
不能替代此边界。

## 依赖方向

| 层 | 路径 | 允许依赖 | 职责 |
| --- | --- | --- | --- |
| 交易契约 | `packages/contracts` | 标准库、pydantic | 版本化公开数据结构与 JSON Schema；不包含 ORM/引擎对象 |
| 数据契约 | `packages/data-contracts` | 标准库、pydantic | 独立采集/发布/质量/新鲜度的可移植契约；不依赖 QTrade 身份与交易模型 |
| 数据核心 | `packages/data-core` | 数据契约、标准库 | 可移植的参考数据与采集业务逻辑；不得导入 QTrade 业务模块 |
| 领域 | `packages/domain` | 契约、数据契约、标准库 | 业务不变量、状态转换、策略和 Port；不做网络或数据库 I/O |
| 应用 | `packages/application` | 领域、契约、数据契约 | 用例、授权检查、事务编排、幂等与任务协调；注入依赖 |
| 数据适配器 | `adapters/data_*` | 数据契约、数据核心、供应商库 | 可移植数据管道（供应商协议、数据独占存储、归档）；只能导入自身、`qtrade_data_core`、`qtrade_data_contracts` |
| 适配器 | 其他 `adapters/*` | 领域、契约、供应商库 | SQL、外部协议、错误转换、能力声明；不得导入应用或进程入口 |
| 运行基础 | `packages/runtime` | 通用基础库 | 配置、健康、观测、进程工具；不得导入业务上下文或应用入口 |
| 组装入口 | `apps/*` | 所有公开接口 | HTTP/WebSocket/任务入口与依赖组装；不得互相导入，不包含风控或撮合规则 |

`apps/common`（`qtrade_composition`）是唯一允许把多个适配器组装到用例的组装根；
采集服务 `qtrade_collector` 只允许导入数据契约、数据核心与自身模块。

领域不会依赖 FastAPI、Next.js、数据库驱动或任务队列。应用不会构造具体 Adapter。
API 路由只调用应用用例；组装代码统一选择 Adapter。worker 和 realtime 不导入 API。
前端按 `features/<context>` 组织，通过 BFF/版本化契约访问领域；只有 `server/` 可读取密钥。
共享 UI 只放通用交互，业务权限仍由后端重新判断。

## 上下文、聚合与数据所有权

| 上下文 | 所有权与聚合 | 公开操作/输出 | 与其他上下文的关系 |
| --- | --- | --- | --- |
| 身份与访问 | QTrade User、Workspace、Membership、Session | 映射身份、认证会话、判定权限 | SSO 为外部身份来源；其他上下文只引用用户与 Workspace ID |
| 市场数据 | Instrument 规格版本、Data Batch、Market Dataset、Data Quality Issue | 采集、质量检查、标准行情查询、血缘 | 回测引用冻结 Dataset；执行查询市场状态，不修改市场数据 |
| 策略 | Strategy、不可变 Strategy Version、版本状态、发布审批 | 保存新版本、静态预检、校验状态机、收集回测/模拟资格、记录风险审批 | 回测/执行只引用版本 ID；策略上下文不声称模拟或实盘正在运行，资格和活动运行状态分别由其上下文通过只读 Port 提供 |
| 回测 | Backtest Run、不可变 Artifact Manifest、版本化进度事件与结果索引 | 校验冻结输入、创建/取消任务、收集引擎状态和结果、比较运行 | 只依赖 TradingEnginePort、ObjectStorePort 与数据/策略查询 Port；PostgreSQL CAS 保存运行版本和追加式事件，完成时验证 Workspace 作用域与结果校验和；成功完成形成版本资格事实供策略查询 |
| 执行 | Runtime、Broker Account、Order、Fill、Reconciliation | 提交已授权命令、跟踪成交、恢复、对账 | 必须经过 Risk Decision；提供指定版本是否实际处于活动状态的事实；行情失效与对账差异阻断新实盘订单 |
| 风控 | 限额配置版本、Risk Decision、Risk Event、Kill Switch、恢复审批 | 评估意图、限额使用率、熔断/恢复 | 风控拥有允许/拒绝规则；执行拥有订单，不允许绕过风控 |
| 告警 | Alert Rule Version、Alert、Notification Attempt | 评估、冷却/升级、处置、通知 | 消费来源事实，不写入来源上下文的数据 |
| 审计 | Audit Event、导出记录 | 追加、授权查询与导出 | 记录所有上下文动作，普通业务操作不能修改既有记录 |

聚合内同步维护不变量。每张表和迁移由一个上下文负责；其他上下文只能通过公开接口
或只读投影访问，禁止跨上下文直接更新 ORM 对象。初期共享 PostgreSQL 实例不代表共享所有权。
不强制全系统事件溯源；领域实体保存状态与必要历史，审计与不可变产物保留事实。

## 模块之间如何通信

- 需要立即裁决的操作使用同步应用接口：鉴权、风控、实盘启动条件和事务内幂等检查。
- 告警、报告和外部通知使用明确的集成事件。事件包含 `event_id`、`schema_version`、
  `workspace_id`、发生时间、关联对象与 `trace_id`，不携带凭证或完整供应商私有对象。
- 数据库状态改变与事件 Outbox 同一事务提交；发布器与消费者使用至少一次交付和去重。
  去重记录与消费结果必须在同一事务内写入，不能用“先检查后插入”替代唯一约束。
- 订单按账户/运行时串行化关键状态转换，以唯一幂等键约束并发提交；部分成交、取消
  和超时后查询保留已成交事实。外部超时不等于未提交，恢复时先查询/对账再重试。
- 跨上下文长流程记录状态与补偿步骤；不以跨服务分布式事务掩盖 broker 的不可逆副作用。

## 公共接口与重构方式

每个上下文在公开 facade/Port 提供用例，内部文件不成为其他上下文的依赖。
接口文档同时记录前置条件、时间/数量单位、失败分类、幂等语义和一致性要求。
最小共享内核只有 ID、时间/精度规则、版本化契约与标准错误；业务规则留在其所有者内。
只为真实变化点创建 Port，并使用生产/模拟两种 Adapter 验证接口的可替换性。

替换存储：实现 Repository/UnitOfWork 或 MarketDataPort → 运行同一接口契约测试 →
迁移/回填与影子比对 → 切换组装配置。替换引擎：实现 Port、runner、结果转换与能力声明 →
运行确定性和失败映射测试。抽取服务：公开用例改为远程 Adapter，迁移其拥有的数据与
Outbox，消费者继续使用原契约。每次迁移都验证 Workspace 隔离、历史 manifest 与审计关联。

契约有显式版本；破坏性改变引入新版本，不静默改变既有字段语义。
数据库使用 expand/contract 迁移，保留滚动升级兼容窗口；不可变输入不回写到历史版本。
Read model 可以为工作台跨上下文汇总，写命令仍回到对应拥有者。

## 测试与约束

依赖策略由 `scripts/check_architecture.py` 在 AST 层执行（CI 直接运行，`tests/test_architecture.py`
另行验证检查器对故意违规样例确实报错）：

- 层内只能导入自身、允许依赖与标准库；供应商 SDK（`ccxt`、`pyarrow`、`ibapi` 等）只允许出现在适配器。
- 进程入口之间禁止互相导入；`qtrade_runtime` 不得依赖业务模块；采集服务只依赖数据层。
- 可移植数据适配器（`qtrade_data_*`）不得导入 QTrade 身份/交易模块。
- 领域子包之间只能通过公开 facade 互相引用；非字面量动态导入直接失败。

领域测试从公开接口验证不变量；所有 Adapter 运行共享契约测试。真实基础设施集成测试与
浏览器 E2E 验证完整链路，不以 Mock 通过替代它们。每个 OpenSpec checkbox 附带实际验收证据；
尚无市场/授权/凭证的外部验收保持未完成。

## 进程与运行拓扑

部署是模块化单体加一个独立采集服务；进程拥有运行边界，业务规则仍归上下文所有：

| 进程 | 路径 | 端口（本地验收） | 职责 |
| --- | --- | --- | --- |
| Web/BFF | `apps/web` | 3120（Compose 13000） | Next.js：会话、CSRF、服务端渲染与只读页面；持有浏览器会话，不持有供应商凭据 |
| API | `apps/api` | 3122（Compose 18000） | 工作区鉴权、权限判定、版本化路由；通过服务令牌桥接采集服务 |
| 采集服务 | `apps/collector` | 18100（独立部署） | 采集任务/分段/发布/质量/新鲜度的所有者；供应商适配器与归档在此执行 |
| Worker | `apps/worker` | 3123 | 领取带租约的分段、执行采集与标准化、写入发布；研究任务由 `ResearchWorker` 执行 |
| Realtime | `apps/realtime` | 8000（Compose 内） | 行情流分发，不承接重计算 |
| Runner | `apps/runner` + `adapters/runner` | Unix socket | 隔离执行策略源码：无网络、只读根文件系统、低权限子进程与资源上限 |

本地 Compose 依赖：PostgreSQL 17（身份与业务状态）、TimescaleDB 2.30（行情）、Valkey 8
（缓存/队列）、MinIO（对象存储）。`scripts/verification-local.py` 以原生进程启动
sso/api/worker/web，连接上述容器；`compose.collector.yaml` 单独部署采集服务。

## 独立采集任务的数据流

1. 浏览器只访问 Web/BFF；BFF 读取 HttpOnly 会话并向 API 发送带 CSRF 的请求。
2. API 校验工作区角色后调用采集服务：`POST /v1/preview` 返回计划、调用规模与确认要求；
   `POST /v1/jobs` 保存不可变冻结请求，重复提交依赖幂等键。
3. Worker 以租约领取 `collection_part`（至少一次），执行供应商请求、保留原始批次、
   标准化并写入不可变发布；重试与等待预算由分段状态机记录。
4. API 读取任务/分段/发布的只读投影呈现给前端；质量、新鲜度与用量同样通过版本化接口读取。
5. 发布记录包含原始记录数、校验和与限制说明；前端不得把“任务已提交”当作“数据已发布”。

## 扩展指南

新增一个采集能力（以“因子历史”为例）：

1. 在 `packages/data-contracts` 定义请求/结果契约；破坏性变更引入新版本，不静默改字段语义。
2. 在 `packages/data-core` 实现业务服务与端口；不导入 QTrade 身份/交易模块。
3. 在 `adapters/data_*` 实现供应商协议与归档；能力声明与限制文案随契约返回。
4. 在采集服务暴露版本化端点；在 `apps/api` 增加只做鉴权与桥接的路由。
5. Worker 侧接入分段执行；在 `apps/common` 组装适配器（唯一允许的组装根）。
6. 前端按 `features/<context>` 增加界面与单测；服务端动作经 BFF 访问 API。
7. 数据库变更写成 `adapters/postgres/src/qtrade_postgres/migrations/NNNN_*.sql`
   （expand/contract，保留滚动升级窗口），并在 `tests/test_postgres_integration.py` 增加集成用例。
8. 交付前运行 `scripts/check_architecture.py`、Ruff、mypy、pytest、Web 门禁与真实栈验收，
   在 OpenSpec 变更中记录证据与未完成项。
